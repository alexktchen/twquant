"""資料集組裝（SPEC-010 §2）：前向報酬標籤與特徵×標籤對齊。純函數、無 IO。

標籤只存在於訓練組裝路徑（研究軌）；推論路徑（ml score，T-067）不得 import 本模組
的標籤函數——PIT 上「特徵在 t、標籤看 t+1 之後」的不對稱由這條邊界維持。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import polars as pl

from twquant.backtest.costs import CostConfig, trade_cost
from twquant.panel import session_index


def attach_adjustment(prices: pl.DataFrame, events: pl.DataFrame) -> pl.DataFrame:
    """為 prices 掛上累積除權息還原因子 `adj`（T-098／D-054）。純函數。

    `adj(t) = Π{ adj_factor(e) : ex_date(e) ≤ t }`——**固定過去錨**，只依賴 t 以前的
    事件，故與 as_of 無關。錨之前的事件對報酬比值 `adj(t1)/adj(t2)` 恆等抵銷，
    所以只載入所需期間的事件即可。

    以 as-of join（backward）而非等值 join：實測約 5.75% 的除權息日在 `prices_daily`
    沒有對應列（颱風休市、停牌），等值 join 會**靜默**丟掉那些調整。
    """
    if events.is_empty():
        # 靜默回傳 adj=1.0 會讓 total_return=True 的訓練**原樣退回未還原標籤**，
        # 而呼叫端仍會印「標籤改用總報酬」——正是 D-054 要消滅的失效模式。
        # 本函數只在呼叫端已選擇 total_return 時才被呼叫，故空表一定是錯誤狀態。
        raise ValueError(
            "corporate_events 讀到 0 筆事件，但標籤已要求總報酬（D-054）。"
            "空事件表會靜默退回未還原標籤——6~8 月近兩成的「最差一成」是除息假象。"
            "請先確認供應層 corporate_events 已載入（scripts/load_corporate_events.py）。"
        )
    bad = events.filter(pl.col("adj_factor").is_null() | (pl.col("adj_factor") <= 0.0))
    if bad.height:
        raise ValueError(
            f"adj_factor 須 > 0 且非 null，違規 {bad.height} 列（首列：{bad.row(0)}）"
            "——0 或負值會經累乘污染該股全部歷史"
        )
    cum = (
        events.select("date", "stock_id", "adj_factor")
        .sort("stock_id", "date")
        .with_columns(adj=pl.col("adj_factor").cum_prod().over("stock_id"))
        .select("stock_id", "date", "adj")
        .sort("date")
    )
    return (
        prices.sort("date")
        .join_asof(cum, on="date", by="stock_id", strategy="backward")
        .with_columns(adj=pl.col("adj").fill_null(1.0))
    )


def _return_price(adjusted: bool, columns: Sequence[str]) -> pl.Expr:
    """報酬用價格：`adjusted` 時除以累積還原因子 `adj`（T-098／D-054）。

    `adj` 是固定過去錨的累乘（只含 ex_date ≤ 該列日期的事件），故與 as_of 無關。
    缺欄卻要求還原 → raise：靜默回退成價格報酬正是 D-054 要消滅的失效模式。
    """
    if not adjusted:
        return pl.col("open")
    if "adj" not in columns:
        raise ValueError(
            "adjusted=True 需要 adj 欄（固定過去錨的累積除權息還原因子）；"
            "缺欄時不得回退成未還原價格——6~8 月的標籤會有近兩成是除息假象（D-054）"
        )
    return pl.col("open") / pl.col("adj")


def forward_open_returns(
    prices: pl.DataFrame,
    horizon: int,
    *,
    adjusted: bool = False,
    max_span_sessions: int | None = None,
) -> pl.DataFrame:
    """open(t+1) → open(t+1+h) 前向報酬。輸入需含 (date, stock_id, open)。

    「t+1」是各股票自身的下一個有行情列——停牌期間不可成交，進出場順延（與回測
    引擎的可成交語意一致）。未來列不足者 fwd_ret 為 null（保留列，不靜默丟棄）。

    `adjusted=True` 時改算**總報酬**（含股利），需另含 `adj` 欄。標籤是前瞻構造，
    不受 `assert_no_lookahead` 與還原價錨定規則（D-055）約束。

    **`max_span_sessions`（T-139）**：`shift` 是**列位移**，某檔中間缺 N 個開市日時，
    「h 日報酬」實際橫跨數月而不帶任何標記——它只是一個看起來很大的數字，會被模型
    當成真實的極端報酬學進去。實測 `prices_daily` 有 413 處 >5 開市日的個股缺口
    （最大 260 日），h=20 下約 8,673 個訓練樣本受影響。

    給定時，出場列與當前列的**開市日跨距**超過此值即設 null。跨距以全市場日曆計，
    故連假不受影響（全市場休市不佔開市日）。**預設 None ＝不啟用**，既有配置的
    結果逐位元不變；是否啟用由配置顯式決定。
    """
    if horizon < 1:
        raise ValueError(f"horizon 須 ≥ 1，得到 {horizon}")
    if max_span_sessions is not None and max_span_sessions < horizon + 1:
        raise ValueError(
            f"max_span_sessions({max_span_sessions}) 不得小於 horizon+1({horizon + 1})"
            "：出場列至少在 h+1 個開市日之後，設得更小會讓所有標籤變 null"
        )
    px = _return_price(adjusted, prices.columns)
    entry = px.shift(-1).over("stock_id")
    exit_ = px.shift(-(1 + horizon)).over("stock_id")
    if max_span_sessions is not None:
        prices = session_index(prices)
        sidx = pl.col("_sidx").cast(pl.Int64)
        span = sidx.shift(-(1 + horizon)).over("stock_id", order_by="date") - sidx
        exit_ = pl.when(span <= max_span_sessions).then(exit_).otherwise(None)
    return (
        prices.sort("stock_id", "date")
        .with_columns(
            # schema 允許 open >= 0，故 0 是合法值。未防護時 open(t+1)=0 → +inf，
            # 而 excess_rank_labels 的 mean().over("date") 會把 inf 擴散到整個橫斷面，
            # 讓當日所有 label 塌成同一個常數。成本感知路徑早有同一道防護。
            pl.when((entry > 0.0) & (exit_ > 0.0))
            .then(exit_ / entry - 1.0)
            .otherwise(pl.lit(None, dtype=pl.Float64))
            .alias("fwd_ret")
        )
        .select("date", "stock_id", "fwd_ret")
        .sort("date", "stock_id")
    )


def excess_rank_labels(fwd: pl.DataFrame) -> pl.DataFrame:
    """同日超額（減宇宙均值）→ 橫斷面百分位 label ∈ (0, 1]；fwd_ret null → label null。

    rank 轉換下超額化不改變順位；保留此步是讓 label.transform（SPEC-010 §1）
    日後可換成非 rank 目標時語意不變。
    """
    return (
        fwd.with_columns(
            (pl.col("fwd_ret") - pl.col("fwd_ret").mean().over("date")).alias("_excess")
        )
        .with_columns(
            (
                pl.col("_excess").rank(method="average").over("date")
                / pl.col("_excess").count().over("date")
            ).alias("label")
        )
        .select("date", "stock_id", "label")
        .sort("date", "stock_id")
    )


def cost_adjusted_return_labels(
    prices: pl.DataFrame,
    *,
    horizon: int,
    adv_window_sessions: int,
    position_notional_twd: float,
    downside_penalty: float,
    cost_cfg: CostConfig,
    adjusted: bool = False,
) -> pl.DataFrame:
    """建立 H 日成本後超額報酬標籤；ADV 僅含訊號日及以前資料。

    `adjusted=True` 時毛報酬改用還原價（總報酬，T-098／D-054），但**成本仍用真實
    成交價**——滑價的半價差是價格相依的（`costs.py:_half_spread_frac`），股數也由
    真實價決定；用還原價會同時算錯這兩項。
    """
    required = {"date", "stock_id", "open", "amount"}
    missing = sorted(required - set(prices.columns))
    if missing:
        raise ValueError(f"成本感知標籤缺行情欄位：{', '.join(missing)}")
    if horizon < 1:
        raise ValueError(f"horizon 須 >= 1，得到 {horizon}")
    if adv_window_sessions < 1:
        raise ValueError(f"adv_window_sessions 須 >= 1，得到 {adv_window_sessions}")
    if position_notional_twd <= 0:
        raise ValueError(f"position_notional_twd 須 > 0，得到 {position_notional_twd}")
    if downside_penalty < 0:
        raise ValueError(f"downside_penalty 須 >= 0，得到 {downside_penalty}")

    def estimated_cost_rate(row: dict[str, float | None]) -> float | None:
        entry_raw, exit_raw, adv_raw = row["_entry"], row["_exit"], row["_adv"]
        if entry_raw is None or exit_raw is None or adv_raw is None:
            return None
        entry, exit_, adv = float(entry_raw), float(exit_raw), float(adv_raw)
        if entry <= 0.0 or exit_ <= 0.0 or adv <= 0.0:
            return None
        shares = int(position_notional_twd // entry)
        if shares < 1:
            return None
        participation = position_notional_twd / adv
        buy = trade_cost(
            side="buy", price=entry, shares=shares, participation=participation, cfg=cost_cfg
        )
        sell = trade_cost(
            side="sell", price=exit_, shares=shares, participation=participation, cfg=cost_cfg
        )
        return (buy.total + sell.total) / (entry * shares)

    px_ret = _return_price(adjusted, prices.columns)
    prepared = (
        prices.sort("stock_id", "date")
        .with_columns(
            pl.col("open").shift(-1).over("stock_id").alias("_entry"),
            pl.col("open").shift(-(1 + horizon)).over("stock_id").alias("_exit"),
            # 報酬基準價：adjusted 時為還原價，否則與 _entry/_exit 同值
            px_ret.shift(-1).over("stock_id").alias("_entry_r"),
            px_ret.shift(-(1 + horizon)).over("stock_id").alias("_exit_r"),
            pl.col("amount")
            .rolling_median(window_size=adv_window_sessions, min_samples=adv_window_sessions)
            .over("stock_id")
            .alias("_adv"),
        )
        .with_columns(
            # schema 允許 open >= 0，故 0 是合法值；但 SPEC-011 §2 要求「價格無效」時
            # label 為 null。少了這道防護，除以 0 會得到 inf，再被下一步的當日均值
            # 擴散到整個橫斷面（成本路徑早已有相同判斷，見 estimated_cost_rate）。
            pl.when(
                (pl.col("_entry") > 0.0)
                & (pl.col("_exit") > 0.0)
                & (pl.col("_entry_r") > 0.0)
                & (pl.col("_exit_r") > 0.0)
            )
            .then(pl.col("_exit_r") / pl.col("_entry_r") - 1.0)
            .otherwise(pl.lit(None, dtype=pl.Float64))
            .alias("_gross"),
            pl.struct("_entry", "_exit", "_adv")
            .map_elements(estimated_cost_rate, return_dtype=pl.Float64)
            .alias("_cost"),
        )
        # mean() 略過 null，故均值即 SPEC-011 §2 的「當日有效宇宙」平均。
        .with_columns((pl.col("_gross") - pl.col("_gross").mean().over("date")).alias("_excess"))
        .with_columns((pl.col("_excess") - pl.col("_cost")).alias("_net"))
    )
    return (
        prepared.with_columns(
            pl.when(pl.col("_net") < 0.0)
            .then(pl.col("_net") * (1.0 + downside_penalty))
            .otherwise(pl.col("_net"))
            .alias("label")
        )
        .select("date", "stock_id", "label")
        .sort("date", "stock_id")
    )


def cross_rank_pct(frame: pl.DataFrame, value_col: str, out_col: str) -> pl.DataFrame:
    """同日橫斷面百分位 (0,1]；null 傳遞、不丟列（`ml score` 寫 rank_pct 用，SPEC-010 §5）。"""
    return frame.with_columns(
        (
            pl.col(value_col).rank(method="average").over("date")
            / pl.col(value_col).count().over("date")
        ).alias(out_col)
    )


def assert_feature_coverage(
    features: Mapping[str, pl.DataFrame], *, min_non_null_share: float = 0.0
) -> dict[str, float]:
    """訓練前的特徵覆蓋閘門（T-088 AC-3）：任一特徵無列或非空比例 ≤ 門檻即 raise，回傳各特徵非空比例。

    Phase 1 的 25 個 pin 特徵裡有 2 個在 factor_values 從未有值（D-045）、1 個全 null（T-082），
    LightGBM 照樣「成功」訓練——全空特徵在模型裡是白佔的欄位，而沒有任何一關報錯。
    只掛在訓練路徑；生產 `ml score` 單日缺某因子屬正常，不得套用。
    """
    if not 0.0 <= min_non_null_share < 1.0:
        raise ValueError(f"min_non_null_share 須介於 [0, 1)，得到 {min_non_null_share}")
    shares: dict[str, float] = {}
    dead: list[str] = []
    for name, df in features.items():
        n = df.height
        share = float(df["value"].is_not_null().sum() / n) if n else 0.0
        shares[name] = share
        if n == 0 or share <= min_non_null_share:
            dead.append(f"{name}（{n:,} 列、非空 {share:.1%}）")
    if dead:
        raise ValueError(
            "特徵覆蓋不足，拒絕訓練："
            + "；".join(dead)
            + f"。門檻 non-null > {min_non_null_share:.0%}。全空或近空的特徵在模型裡是白佔的欄位"
            "（Phase 1 曾以 3 個空特徵訓練並回報成功）——先補資料或從配置移除，不得靜默訓練。"
        )
    return shares


def assemble(features: Mapping[str, pl.DataFrame], labels: pl.DataFrame) -> pl.DataFrame:
    """特徵長表（各為 (date, stock_id, value)）outer join 成寬表，label 以 left join 掛上。

    缺值一律保留 null（LightGBM 原生處理；黃金規則 5 禁止填 0）。
    輸出欄位 (date, stock_id, <特徵名...>, label)，依 (date, stock_id) 排序。
    """
    if not features:
        raise ValueError("features 為空：至少需要一個特徵")
    wide: pl.DataFrame | None = None
    for name, df in features.items():
        part = df.select("date", "stock_id", pl.col("value").alias(name))
        wide = (
            part
            if wide is None
            else wide.join(part, on=["date", "stock_id"], how="full", coalesce=True)
        )
    assert wide is not None  # features 非空已檢查
    return wide.join(
        labels.select("date", "stock_id", "label"), on=["date", "stock_id"], how="left"
    ).sort("date", "stock_id")
