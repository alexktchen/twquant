"""價格類因子（SPEC-002 §1a 起）。

第一版含 price.mom_20d（供端到端 golden 與框架測試）。使用還原價（目前無
corporate_events，adj == close；待 T-009 補齊後自動生效）。因子只看回看窗內資料，
反前視由 assert_no_lookahead 驗證。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from twquant.factors.registry import factor
from twquant.panel import safe_ratio

if TYPE_CHECKING:
    from twquant.data.warehouse import PitView


# T-139／T-140：價格比率的共用構造。兩個缺陷同一個根——`shift` 是**列位移**。
#
# 實測 `factor_values` 有 22,988 列 `inf`：5547 在 2026-03 只有 6 列，`shift(20)` 往回
# 落到 **2021-02** 一列 `close=0` 的無效資料上 → 除以零 → `inf`。而 `inf` 不是 null，
# `drop_nulls` 攔不住它，它會一路流進標準化與模型。
#
# `_SPAN_TOLERANCE` 是**開市日跨距的容忍倍數**。往回數 window 列至少跨 window 個開市日；
# 設 3 倍是刻意寬鬆的——正常的停牌、處置、減資復牌都在這個範圍內，只攔那種
# 「往回 20 列卻跨了 1,200 個開市日」的荒謬情形。收緊它會改變大量正常值，
# 屬策略層變更，須另行裁決。
_SPAN_TOLERANCE = 3


def _lagged_return(prices: pl.LazyFrame, window: int) -> pl.LazyFrame:
    """close(t)/close(t−window) − 1，附零分母與跨缺口防護。回傳含 `value` 欄的 LazyFrame。"""
    idx = prices.select("date").unique().sort("date").with_row_index("_sidx")
    joined = prices.join(idx, on="date", how="left").sort(["stock_id", "date"])
    prev = pl.col("close").shift(window).over("stock_id", order_by="date")
    sidx = pl.col("_sidx").cast(pl.Int64)
    span = sidx - sidx.shift(window).over("stock_id", order_by="date")
    ok = span <= window * _SPAN_TOLERANCE
    return joined.with_columns(
        pl.when(ok).then(safe_ratio(pl.col("close"), prev) - 1.0).otherwise(None).alias("value")
    )


@factor(
    name="price.mom_20d",
    category="price",
    direction=1,
    tables=["prices_daily"],
    lookback_days=25,
    params={"window": 20},
    anchor_invariant=True,
)
def mom_20d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """20 日動能：adj_close(t)/adj_close(t-window) − 1。回傳 (date, stock_id, value)。"""
    raw = params.get("window", 20)
    window = int(raw) if isinstance(raw, int | float | str) else 20
    prices = (
        view.table("prices_daily").select("date", "stock_id", "close").sort(["stock_id", "date"])
    )
    return (
        _lagged_return(prices, window)
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .collect()
    )


@factor(
    name="price.high_52w",
    category="price",
    direction=1,
    tables=["prices_daily"],
    lookback_days=260,
    params={"window": 252},
    anchor_invariant=True,
)
def high_52w(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """52 週高點接近度 = close / 期間最高（≤1，越接近 1 越貼近高點，STRATEGY §1-1）。"""
    raw = params.get("window", 252)
    window = int(raw) if isinstance(raw, int | float | str) else 252
    prices = (
        view.table("prices_daily").select("date", "stock_id", "close").sort(["stock_id", "date"])
    )
    hi = pl.col("close").rolling_max(window).over("stock_id")
    return (
        prices.with_columns(safe_ratio(pl.col("close"), hi).alias("value"))
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .collect()
    )


@factor(
    name="price.reversal_5d",
    category="price",
    direction=-1,
    tables=["prices_daily"],
    lookback_days=8,
    params={"window": 5},
    anchor_invariant=True,
)
def reversal_5d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """5 日短期反轉：近 5 日報酬（direction=-1，高者傾向反轉，STRATEGY §1-2）。"""
    raw = params.get("window", 5)
    window = int(raw) if isinstance(raw, int | float | str) else 5
    prices = (
        view.table("prices_daily").select("date", "stock_id", "close").sort(["stock_id", "date"])
    )
    return (
        _lagged_return(prices, window)
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .collect()
    )


@factor(
    name="price.mom_60d",
    category="price",
    direction=1,
    tables=["prices_daily"],
    lookback_days=65,
    params={"window": 60},
    anchor_invariant=True,
)
def mom_60d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """60 日中期動能：close(t)/close(t-60) − 1（正向：中期強勢延續）。"""
    raw = params.get("window", 60)
    window = int(raw) if isinstance(raw, int | float | str) else 60
    prices = (
        view.table("prices_daily").select("date", "stock_id", "close").sort(["stock_id", "date"])
    )
    return (
        _lagged_return(prices, window)
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .collect()
    )


@factor(
    name="price.vol_20d",
    category="price",
    direction=-1,
    tables=["prices_daily"],
    lookback_days=25,
    params={"window": 20},
    anchor_invariant=True,
)
def vol_20d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """20 日已實現波動率：日報酬的 20 日滾動標準差（direction=-1，低波動異象）。"""
    raw = params.get("window", 20)
    window = int(raw) if isinstance(raw, int | float | str) else 20
    prices = (
        view.table("prices_daily").select("date", "stock_id", "close").sort(["stock_id", "date"])
    )
    ret = (
        safe_ratio(pl.col("close"), pl.col("close").shift(1).over("stock_id", order_by="date"))
        - 1.0
    )
    return (
        prices.with_columns(_r=ret)
        .with_columns(value=pl.col("_r").rolling_std(window).over("stock_id"))
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .collect()
    )


@factor(
    name="price.rs_market_20d",
    category="price",
    direction=1,
    tables=["prices_daily", "index_daily"],
    lookback_days=25,
    params={"window": 20},
)
def rs_market_20d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """相對大盤強弱：個股 20 日報酬 − 加權指數 20 日報酬（正向：相對強者續強）。

    需 index_daily（TAIEX）；未 backfill-index 時 view.table 讀無資料 raise，此因子自動略過。
    """
    raw = params.get("window", 20)
    window = int(raw) if isinstance(raw, int | float | str) else 20
    px = view.table("prices_daily").select("date", "stock_id", "close").sort(["stock_id", "date"])
    sret = (
        safe_ratio(pl.col("close"), pl.col("close").shift(window).over("stock_id", order_by="date"))
        - 1.0
    )
    idx = (
        view.table("index_daily")
        .filter(pl.col("index_id") == "TAIEX")
        .select("date", "close")
        .sort("date")
    )
    idx = idx.with_columns(
        _mret=safe_ratio(pl.col("close"), pl.col("close").shift(window)) - 1.0
    ).select("date", "_mret")
    return (
        px.with_columns(_sret=sret)
        .join(idx, on="date", how="inner")
        .with_columns(value=pl.col("_sret") - pl.col("_mret"))
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .collect()
    )


@factor(
    name="price.overnight_ret_20d",
    category="price",
    direction=-1,
    tables=["prices_daily"],
    lookback_days=25,
    params={"window": 20},
    anchor_invariant=True,
)
def overnight_ret_20d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """20 日累計隔夜報酬：Σ ln(open_t / close_{t-1})（PAPERS-SHORTTERM §B）。

    反向：隔夜報酬高者其後走弱——Lou/Polk/Skouras (2019, JFE) 跨時段反轉；台股
    OMOM 為負由 Ho/Hsiao/Lo/Yang (2023, PBFJ) 複製。與 intraday_ret_20d 互補：
    兩者之和 = 20 日累計 ln 總報酬（分解恆等式，測試驗證）。
    """
    raw = params.get("window", 20)
    window = int(raw) if isinstance(raw, int | float | str) else 20
    prices = (
        view.table("prices_daily")
        .select("date", "stock_id", "open", "close")
        .sort(["stock_id", "date"])
    )
    prev = pl.col("close").shift(1).over("stock_id", order_by="date")
    on = (
        pl.when((pl.col("open") > 0) & (prev > 0))
        .then((pl.col("open") / prev).log())
        .otherwise(None)
    )
    return (
        prices.with_columns(_on=on)
        .with_columns(value=pl.col("_on").rolling_sum(window).over("stock_id"))
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .collect()
    )


@factor(
    name="price.intraday_ret_20d",
    category="price",
    direction=1,
    tables=["prices_daily"],
    lookback_days=25,
    params={"window": 20},
    anchor_invariant=True,
)
def intraday_ret_20d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """20 日累計盤中報酬：Σ ln(close_t / open_t)（PAPERS-SHORTTERM §B）。

    正向：盤中動能延續（對盤中資訊反應不足）——台股 IMOM 顯著為正
    （Ho/Hsiao/Lo/Yang 2023, PBFJ 82:102151）。
    """
    raw = params.get("window", 20)
    window = int(raw) if isinstance(raw, int | float | str) else 20
    prices = (
        view.table("prices_daily")
        .select("date", "stock_id", "open", "close")
        .sort(["stock_id", "date"])
    )
    intra = (
        pl.when((pl.col("open") > 0) & (pl.col("close") > 0))
        .then((pl.col("close") / pl.col("open")).log())
        .otherwise(None)
    )
    return (
        prices.with_columns(_in=intra)
        .with_columns(value=pl.col("_in").rolling_sum(window).over("stock_id"))
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .collect()
    )


@factor(
    name="price.residual_reversal_5d",
    category="price",
    direction=-1,
    tables=["prices_daily", "stock_meta"],
    lookback_days=10,
    params={"window": 5},
)
def residual_reversal_5d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """5 日產業殘差反轉：個股 5 日報酬 − 同產業等權平均 5 日報酬（反向）。

    Da/Liu/Schaumburg (2014, Mgmt Sci)：系統性（基本面/共同）驅動的漲跌不反轉，
    殘差才反轉——濾掉產業共同走勢後反轉訊號更純。產業別取自 stock_meta（緩變維度、
    現值套用全歷史，與個股體檢卡同一近似）；無 stock_meta 時 scan raise，健檢自動略過
    （同 rs_market_20d 的 index_daily 慣例）。單一成員產業殘差為 0。
    """
    raw = params.get("window", 5)
    window = int(raw) if isinstance(raw, int | float | str) else 5
    px = view.table("prices_daily").select("date", "stock_id", "close").sort(["stock_id", "date"])
    ret = (
        safe_ratio(pl.col("close"), pl.col("close").shift(window).over("stock_id", order_by="date"))
        - 1.0
    )
    meta = (
        view.table("stock_meta")
        .select("stock_id", "industry")
        .drop_nulls("industry")
        .unique(subset=["stock_id"])
    )
    return (
        px.with_columns(_r=ret)
        .drop_nulls("_r")
        .join(meta, on="stock_id", how="inner")
        .with_columns(value=pl.col("_r") - pl.col("_r").mean().over(["date", "industry"]))
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .collect()
    )


@factor(
    name="price.mom_1d",
    category="price",
    direction=1,
    tables=["prices_daily"],
    lookback_days=5,
    params={"window": 1},
    anchor_invariant=True,
)
def mom_1d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """1 日動能：close(t)/close(t-1) − 1（PAPERS-SHORTTERM §A）。

    正向先驗：台股屬「日動能」市場——Gao/Jiang/Xiong/Xiong (NBER 31839) 21 個新興
    市場中 14 個（含台灣）日動能顯著為正，1 日續勢、第 2 日起反轉。預測窗在 1 日
    （IC_HORIZONS 含 1 即為此因子）；5/10/20 日 IC 呈負屬預期（反轉主導），勿誤判。
    """
    raw = params.get("window", 1)
    window = int(raw) if isinstance(raw, int | float | str) else 1
    prices = (
        view.table("prices_daily").select("date", "stock_id", "close").sort(["stock_id", "date"])
    )
    return (
        _lagged_return(prices, window)
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .collect()
    )


@factor(
    name="price.at_low_20",
    category="price",
    direction=-1,
    tables=["prices_daily", "corporate_events"],
    lookback_days=25,
    params={"window": 20},
    anchor_invariant=True,
)
def at_low_20(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """收盤是不是近 window 個有收盤的交易日最低（還原除權息、同價也算）：是 1.0、否 0.0（D-184）。

    有收盤的日子不滿 window 天不給值（新股、長期停牌）。還原用固定過去錨（同 `ml.dataset.attach_adjustment`）：
    adj(t)＝t 以前（含）所有除權息因子的累乘，價格除以它——除息日的跳空不會被當成破底，
    且只用 t 以前的事件。窗口內沒有事件時 adj＝1 是正確值（不需要還原），不是缺值。
    方向 −1 只是使用者的先驗（破底不該買）；D-183 的開發期描述沒有看到差別，由第二保留期判。
    """
    raw = params.get("window", 20)
    window = int(raw) if isinstance(raw, int | float | str) else 20
    prices = (
        view.table("prices_daily")
        .filter(pl.col("close") > 0)
        .select("date", "stock_id", "close")
        .sort("date")
    )
    events = (
        view.table("corporate_events")
        .filter(pl.col("adj_factor").is_not_null() & (pl.col("adj_factor") > 0.0))
        .select("date", "stock_id", "adj_factor")
        .sort("stock_id", "date")
        .with_columns(adj=pl.col("adj_factor").cum_prod().over("stock_id"))
        .select("date", "stock_id", "adj")
        .sort("date")
    )
    # 這檔在那天以前還沒有任何除權息 → 累乘因子就是 1（定義，不是補缺值；同 attach_adjustment）
    adj_close = safe_ratio(pl.col("close"), pl.col("adj").fill_null(1.0))
    low = pl.col("_ac").rolling_min(window, min_samples=window).over("stock_id", order_by="date")
    return (
        prices.join_asof(events, on="date", by="stock_id", strategy="backward")
        .with_columns(_ac=adj_close)
        .with_columns(_lo=low)
        .filter(pl.col("_lo").is_not_null())
        .with_columns(value=(pl.col("_ac") <= pl.col("_lo")).cast(pl.Float64))
        .select("date", "stock_id", "value")
        .collect()
    )
