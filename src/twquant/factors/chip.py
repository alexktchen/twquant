"""籌碼類因子（SPEC-002 / STRATEGY §1-3、§1-2、§4 的 P0 籌碼）。

以現有免費資料可算的 P0 籌碼因子：投信/外資買超（占成交量比）、投信連買天數、
散戶密集度（以融資變化為代理）。**尚未含**主力分點集中度（chip.concentration_*，
需 broker_trades 分點資料，屬 T-016）；投信主動/被動拆分需 ETF 持股資料（待補）。

正規化原則（STRATEGY §3.3）：原始買賣超不直接進模型，除以成交量成為尺度不變比值。
所有因子皆為滾動或當日運算，值只依賴 t 日（含）以前的列（反前視由 assert_no_lookahead
驗證）。注意：滾動視窗以「列數」計，資料有缺口時視窗會跨越缺口（與 price.mom_20d
同一限制），故 IC 應在連續期間解讀。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from twquant.factors.registry import factor

if TYPE_CHECKING:
    from twquant.data.warehouse import PitView


def _flow_over_volume(view: PitView, flow_col: str, window: int) -> pl.DataFrame:
    """N 日法人淨買超（股）÷ N 日成交量（股）。回傳 (date, stock_id, value)。"""
    inst = view.table("institutional_daily").select("date", "stock_id", flow_col)
    px = view.table("prices_daily").select("date", "stock_id", "volume")
    return (
        inst.join(px, on=["date", "stock_id"], how="inner")
        .sort(["stock_id", "date"])
        .with_columns(
            _net=pl.col(flow_col).rolling_sum(window).over("stock_id"),
            _vol=pl.col("volume").rolling_sum(window).over("stock_id"),
        )
        .with_columns(
            value=pl.when(pl.col("_vol") > 0).then(pl.col("_net") / pl.col("_vol")).otherwise(None)
        )
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .collect()
    )


@factor(
    name="chip.trust_net_10d",
    category="chip",
    direction=1,
    tables=["institutional_daily", "prices_daily"],
    lookback_days=15,
    params={"window": 10},
)
def trust_net_10d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """投信 10 日累計買超占成交量比（先驗正向：投信作帳/群聚買進，STRATEGY §1-3）。

    ⚠️ 2025-07~2026-06 實測（含缺口前連續期）RankIC 穩健為**負**（10 日 t≈-11），
    即投信占量比高者反而反轉——呼應 §1-2 投信擁擠出場風險。direction 仍暫掛 +1
    （文獻先驗），實際符號待 T-019 walk-forward 以更長樣本定奪。
    """
    raw = params.get("window", 10)
    window = int(raw) if isinstance(raw, int | float | str) else 10
    return _flow_over_volume(view, "trust_net", window)


@factor(
    name="chip.foreign_net_10d",
    category="chip",
    direction=1,
    tables=["institutional_daily", "prices_daily"],
    lookback_days=15,
    params={"window": 10},
)
def foreign_net_10d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """外資 10 日累計買超占成交量比（訊號較投信弱，供「投信買+外資賣」象限，§1-3）。"""
    raw = params.get("window", 10)
    window = int(raw) if isinstance(raw, int | float | str) else 10
    return _flow_over_volume(view, "foreign_net", window)


@factor(
    name="chip.trust_streak",
    category="chip",
    direction=1,
    tables=["institutional_daily"],
    lookback_days=30,
    params={},
)
def trust_streak(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """投信連續買超天數（截至 t 日；連買越久訊號越強，STRATEGY §1-3）。

    註：全期 IC 看似正向（20 日 t≈+3.5），但在缺口前連續期崩至不顯著（t≈+1.4），
    全期數字有缺口灌水成分，勿高估。
    """
    inst = view.table("institutional_daily").select("date", "stock_id", "trust_net")
    return (
        inst.sort(["stock_id", "date"])
        .with_columns(_buy=(pl.col("trust_net") > 0))
        # _blk：每遇一個非買超日就 +1，把序列切成「連買區塊」
        .with_columns(_blk=(~pl.col("_buy")).cast(pl.Int32).cum_sum().over("stock_id"))
        # 區塊內買超日的累計數 = 連買天數（非買超日為 0）
        .with_columns(value=pl.col("_buy").cast(pl.Float64).cum_sum().over(["stock_id", "_blk"]))
        .select("date", "stock_id", "value")
        .collect()
    )


def _to_next_session(df: pl.LazyFrame, view: PitView) -> pl.LazyFrame:
    """把每列的 date 前移到**下一個交易日**（D-008：融資 available_at 21:00 晚於 20:00 截點，
    故融資以 T-1 進當日訊號）。交易日序列取自 PitView 內的 prices_daily（已按截點過濾，PIT-safe）。
    沒有下一個交易日的列（最新一列之後）會被丟掉——那筆資料在任何 as_of 都還不能用。

    T-088／D-045：舊實作把值標成融資列自己的日期，materialize 只留 dates=[as_of]，
    於是兩個融資因子在 factor_values 整張表歷史中從未產出任何一列。
    """
    sessions = view.table("prices_daily").select(pl.col("date").alias("_s")).unique().sort("_s")
    return (
        df.with_columns(pl.col("date").dt.offset_by("1d").alias("_after"))
        .sort("_after")
        .join_asof(sessions, left_on="_after", right_on="_s", strategy="forward")
        .drop_nulls("_s")
        .drop("date", "_after")
        .rename({"_s": "date"})
    )


@factor(
    name="chip.margin_velocity_5d",
    category="chip",
    direction=-1,
    tables=["margin_daily", "prices_daily"],
    lookback_days=10,
    params={"window": 5},
)
def margin_velocity_5d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """散戶密集度代理：5 日融資餘額淨增 ÷ 5 日成交量（負向：散戶加碼後傾向落後，§1-2）。

    註：STRATEGY 的「散戶密集度」原指集保股權分散（TDCC，尚未接），此處以融資變化代理。
    """
    raw = params.get("window", 5)
    window = int(raw) if isinstance(raw, int | float | str) else 5
    m = view.table("margin_daily").select("date", "stock_id", "margin_balance")
    px = view.table("prices_daily").select("date", "stock_id", "volume")
    out = (
        m.join(px, on=["date", "stock_id"], how="inner")
        .sort(["stock_id", "date"])
        .with_columns(
            _prev=pl.col("margin_balance").shift(window).over("stock_id", order_by="date"),
            _vol=pl.col("volume").rolling_sum(window).over("stock_id"),
        )
        .with_columns(_delta=pl.col("margin_balance") - pl.col("_prev"))
        .with_columns(
            value=pl.when(pl.col("_vol") > 0)
            .then(pl.col("_delta") / pl.col("_vol"))
            .otherwise(None)
        )
        .select("date", "stock_id", "value")
        .drop_nulls("value")
    )
    # 計算在融資自己的日期序列上完成後，才把標籤前移到下一交易日（D-008）
    return _to_next_session(out, view).select("date", "stock_id", "value").collect()


@factor(
    name="chip.foreign_streak",
    category="chip",
    direction=1,
    tables=["institutional_daily"],
    lookback_days=30,
    params={},
)
def foreign_streak(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """外資連續買超天數（截至 t 日；連買越久訊號越強，對照 chip.trust_streak）。"""
    inst = view.table("institutional_daily").select("date", "stock_id", "foreign_net")
    return (
        inst.sort(["stock_id", "date"])
        .with_columns(_buy=(pl.col("foreign_net") > 0))
        .with_columns(_blk=(~pl.col("_buy")).cast(pl.Int32).cum_sum().over("stock_id"))
        .with_columns(value=pl.col("_buy").cast(pl.Float64).cum_sum().over(["stock_id", "_blk"]))
        .select("date", "stock_id", "value")
        .collect()
    )


@factor(
    name="chip.inst_net_10d",
    category="chip",
    direction=1,
    tables=["institutional_daily", "prices_daily"],
    lookback_days=15,
    params={"window": 10},
)
def inst_net_10d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """三大法人合計 10 日買超占成交量比（外資+投信+自營；正向：法人同買偏多）。"""
    raw = params.get("window", 10)
    window = int(raw) if isinstance(raw, int | float | str) else 10
    inst = view.table("institutional_daily").select(
        "date", "stock_id", "foreign_net", "trust_net", "dealer_net"
    )
    px = view.table("prices_daily").select("date", "stock_id", "volume")
    return (
        inst.join(px, on=["date", "stock_id"], how="inner")
        .sort(["stock_id", "date"])
        .with_columns(_net=pl.col("foreign_net") + pl.col("trust_net") + pl.col("dealer_net"))
        .with_columns(
            _netsum=pl.col("_net").rolling_sum(window).over("stock_id"),
            _vol=pl.col("volume").rolling_sum(window).over("stock_id"),
        )
        .with_columns(
            value=pl.when(pl.col("_vol") > 0)
            .then(pl.col("_netsum") / pl.col("_vol"))
            .otherwise(None)
        )
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .collect()
    )


@factor(
    name="chip.short_margin_ratio",
    category="chip",
    direction=1,
    tables=["margin_daily", "prices_daily"],  # prices_daily 供交易日序列（_to_next_session）
    lookback_days=3,
    params={},
)
def short_margin_ratio(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """券資比 = 融券餘額 / 融資餘額（當日）。先驗 +1（高券資比＝軋空燃料），實際符號待 IC。"""
    m = view.table("margin_daily").select("date", "stock_id", "margin_balance", "short_balance")
    out = (
        m.with_columns(
            value=pl.when(pl.col("margin_balance") > 0)
            .then(pl.col("short_balance") / pl.col("margin_balance"))
            .otherwise(None)
        )
        .select("date", "stock_id", "value")
        .drop_nulls("value")
    )
    return _to_next_session(out, view).select("date", "stock_id", "value").collect()
