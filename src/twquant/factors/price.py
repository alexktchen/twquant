"""價格類因子（SPEC-002 §1a 起）。

第一版含 price.mom_20d（供端到端 golden 與框架測試）。使用還原價（目前無
corporate_events，adj == close；待 T-009 補齊後自動生效）。因子只看回看窗內資料，
反前視由 assert_no_lookahead 驗證。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from twquant.factors.registry import factor

if TYPE_CHECKING:
    from twquant.data.warehouse import PitView


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
    prev = pl.col("close").shift(window).over("stock_id", order_by="date")
    return (
        prices.with_columns((pl.col("close") / prev - 1.0).alias("value"))
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
        prices.with_columns((pl.col("close") / hi).alias("value"))
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
    prev = pl.col("close").shift(window).over("stock_id", order_by="date")
    return (
        prices.with_columns((pl.col("close") / prev - 1.0).alias("value"))
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
    prev = pl.col("close").shift(window).over("stock_id", order_by="date")
    return (
        prices.with_columns((pl.col("close") / prev - 1.0).alias("value"))
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
    ret = pl.col("close") / pl.col("close").shift(1).over("stock_id", order_by="date") - 1.0
    return (
        prices.with_columns(_r=ret)
        .with_columns(value=pl.col("_r").rolling_std(window).over("stock_id"))
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .collect()
    )


@factor(
    name="price.resid_mom_20d",
    category="price",
    direction=1,
    tables=["prices_daily", "index_daily"],
    lookback_days=70,
    params={"window": 20, "beta_window": 60},
)
def resid_mom_20d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """beta 調整殘差動能：個股 20 日報酬 − β·大盤 20 日報酬（β 由 60 日日報酬迴歸；正向）。

    取代舊 rs_market_20d（D-020）。舊版對橫斷面每檔減去同一個大盤 20 日報酬＝常數平移，
    經去極值/z-score/排名後與 mom_20d 完全同構，在綜合分中把 20 日動能隱性雙倍加權；它其實
    是本因子在 β≡1 的退化特例。此處每檔 β = Cov(r_i, r_m) / Var(r_m)（過去 beta_window 日
    日報酬，僅用 ≤t 資料），使市場調整量隨個股變動、恢復獨立橫斷面資訊。

    需 index_daily（TAIEX）；未 backfill-index 時 view.table 讀無資料 raise，此因子自動略過。
    大盤日報酬在窗內變異數為 0（如史料不足或指數平盤）時 β 未定義 → 該列不產生（不靜默填值）。
    退化價（close=0，schema 為 _nonneg 允許）會使日報酬出現 0/0=NaN 或 x/0=inf；只保留有限
    值（該股不產生值，比照 null），避免單一非有限值污染整個橫斷面標準化（median/std 傳染 NaN）。
    """
    raw = params.get("window", 20)
    window = int(raw) if isinstance(raw, int | float | str) else 20
    braw = params.get("beta_window", 60)
    beta_window = int(braw) if isinstance(braw, int | float | str) else 60
    px = view.table("prices_daily").select("date", "stock_id", "close").sort(["stock_id", "date"])
    idx = (
        view.table("index_daily")
        .filter(pl.col("index_id") == "TAIEX")
        .select("date", "close")
        .sort("date")
    )
    # 大盤日報酬與 20 日報酬一次算在指數序列上（正確的交易日位移），再 broadcast join
    idxf = idx.with_columns(
        _m=pl.col("close") / pl.col("close").shift(1) - 1.0,
        _m20=pl.col("close") / pl.col("close").shift(window) - 1.0,
    ).select("date", "_m", "_m20")
    sret_d = pl.col("close") / pl.col("close").shift(1).over("stock_id", order_by="date") - 1.0
    sret_20 = (
        pl.col("close") / pl.col("close").shift(window).over("stock_id", order_by="date") - 1.0
    )
    joined = (
        px.with_columns(_r=sret_d, _s20=sret_20)
        .join(idxf, on="date", how="inner")
        .sort(["stock_id", "date"])
    )
    # β = Cov(r,m)/Var(m)，以 rolling_mean 組合（母體動差，尾窗、僅回看；不足窗回 null）
    e_rm = (pl.col("_r") * pl.col("_m")).rolling_mean(beta_window).over("stock_id", order_by="date")
    e_r = pl.col("_r").rolling_mean(beta_window).over("stock_id", order_by="date")
    e_m = pl.col("_m").rolling_mean(beta_window).over("stock_id", order_by="date")
    e_mm = (pl.col("_m") * pl.col("_m")).rolling_mean(beta_window).over("stock_id", order_by="date")
    cov = e_rm - e_r * e_m
    var = e_mm - e_m * e_m
    beta = pl.when(var > 0).then(cov / var).otherwise(None)
    return (
        joined.with_columns(_beta=beta)
        .with_columns(value=pl.col("_s20") - pl.col("_beta") * pl.col("_m20"))
        .select("date", "stock_id", "value")
        .filter(pl.col("value").is_finite())  # 去 null 與 NaN/inf（退化價），不外洩非有限值
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
    ret = pl.col("close") / pl.col("close").shift(window).over("stock_id", order_by="date") - 1.0
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
    prev = pl.col("close").shift(window).over("stock_id", order_by="date")
    return (
        prices.with_columns((pl.col("close") / prev - 1.0).alias("value"))
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .collect()
    )
