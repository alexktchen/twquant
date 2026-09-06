"""主力分點因子（T-016）：富邦 `broker_trades`（每檔每日 top-15 買超／賣超，母公司與分點混層、無均價）。

| 因子 | 公式 | 方向 |
|---|---|---|
| broker_concentration_20d | Σ20 日主力淨買超（買超列 − 賣超列）÷ Σ20 日成交量 | + |
| broker_count_diff_5d | Σ5 日（賣方分點數 − 買方分點數）÷ Σ5 日總分點數（少數人吸多數人的籌碼） | + |
| broker_persistence_10d | 近 10 日主力淨買超為正的天數比例 | + |

**隔日沖分點是清潔劑，不是因子**：台股當沖／隔日沖佔成交量三到四成，top-15 買超裡混著明天就要
倒出來的貨。三個因子的分子一律排除「已達門檻」的隔日沖分點。

**PIT**：隔日沖比率是 **expanding**（只用過去事件），且「今買明賣」事件記在**賣出日**（次一交易日）——
那天的資料進來才知道昨天那筆是隔日沖。用全歷史算好的清單套回過去是前視，`assert_no_lookahead` 會抓。
門檻（比率、最少事件數）是參數，由配置注入。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from twquant.factors.registry import factor

if TYPE_CHECKING:
    from twquant.data.warehouse import PitView

_TABLES = ["broker_trades", "prices_daily"]
_DEFAULTS: dict[str, object] = {"daytrade_min_ratio": 0.5, "daytrade_min_events": 30}
# 隔日沖比率是 expanding 統計，需要 broker_trades 全歷史；lookback_days 以 1000 交易日（≈4 年）
# 誠實宣告——這也是它們被列入 cloud.exclude_factors 的原因（雲端只留 10 日）。
_LOOKBACK = 1000


def _num(params: dict[str, object], key: str, default: float) -> float:
    raw = params.get(key, default)
    return float(raw) if isinstance(raw, int | float | str) else default


def _sessions(view: PitView) -> pl.LazyFrame:
    return view.table("prices_daily").select(pl.col("date").alias("_s")).unique().sort("_s")


def _trades(view: PitView) -> pl.LazyFrame:
    return (
        view.table("broker_trades")
        .select("date", "stock_id", "broker_id", "buy_shares", "sell_shares")
        .with_columns(_net=pl.col("buy_shares") - pl.col("sell_shares"))
    )


def _daytrade_state(view: PitView) -> pl.LazyFrame:
    """每個分點的 expanding 隔日沖統計，以事件的**結算日**（買進後的次一交易日）為時間鍵。

    回傳 (broker_id, resolve_date, n_buys, n_flip, ratio)：至 resolve_date 為止累計的
    「買超事件數」與「次一交易日在同檔轉為賣超」的次數。
    """
    t = _trades(view)
    sess = _sessions(view)
    # 每筆買超列的「次一交易日」
    buys = (
        t.filter(pl.col("_net") > 0)
        .select("date", "stock_id", "broker_id")
        .with_columns(_after=pl.col("date").dt.offset_by("1d"))
        .sort("_after")
        .join_asof(sess, left_on="_after", right_on="_s", strategy="forward")
        .drop_nulls("_s")
        .rename({"_s": "resolve_date"})
        .drop("_after")
    )
    sells = t.filter(pl.col("_net") < 0).select(
        pl.col("date").alias("resolve_date"), "stock_id", "broker_id", pl.lit(1).alias("_flip")
    )
    events = (
        buys.join(sells, on=["resolve_date", "stock_id", "broker_id"], how="left")
        .with_columns(pl.col("_flip").fill_null(0))
        .group_by(["broker_id", "resolve_date"])
        .agg(pl.len().alias("_b"), pl.col("_flip").sum().alias("_f"))
        .sort(["broker_id", "resolve_date"])
        .with_columns(
            n_buys=pl.col("_b").cum_sum().over("broker_id"),
            n_flip=pl.col("_f").cum_sum().over("broker_id"),
        )
        .with_columns(ratio=pl.col("n_flip") / pl.col("n_buys"))
        .select("broker_id", "resolve_date", "n_buys", "n_flip", "ratio")
    )
    return events


def daytrade_ratio(view: PitView) -> pl.DataFrame:
    """每個分點截至 view 截點的最新隔日沖統計（供健檢／屬性表用）。"""
    return (
        _daytrade_state(view)
        .sort(["broker_id", "resolve_date"])
        .group_by("broker_id", maintain_order=True)
        .last()
        .collect()
        .sort("broker_id")
    )


def _clean_daily(view: PitView, params: dict[str, object]) -> pl.LazyFrame:
    """排除隔日沖分點後，每檔每日的主力淨買超、買方／賣方分點數。"""
    min_ratio = _num(params, "daytrade_min_ratio", 0.5)
    min_events = _num(params, "daytrade_min_events", 30)
    t = _trades(view).sort("date")
    state = _daytrade_state(view).sort("resolve_date")
    flagged = (
        t.join_asof(
            state,
            left_on="date",
            right_on="resolve_date",
            by="broker_id",
            strategy="backward",
        )
        .with_columns(
            _dt=(pl.col("n_buys").fill_null(0) >= min_events)
            & (pl.col("ratio").fill_null(0.0) >= min_ratio)
        )
        .filter(~pl.col("_dt"))
    )
    # 分點數用 Int64：布林 sum 是 UInt32，`_ns − _nb` 在買方多於賣方時會下溢（真實資料跑 IC 時整個因子崩掉）
    return flagged.group_by(["date", "stock_id"]).agg(
        pl.col("_net").sum().alias("_net"),
        (pl.col("_net") > 0).sum().cast(pl.Int64).alias("_nb"),
        (pl.col("_net") < 0).sum().cast(pl.Int64).alias("_ns"),
    )


def _int(params: dict[str, object], key: str, default: int) -> int:
    raw = params.get(key, default)
    return int(raw) if isinstance(raw, int | float | str) else default


@factor(
    name="chip.broker_concentration_20d",
    category="chip",
    direction=1,
    tables=_TABLES,
    lookback_days=_LOOKBACK,
    params={"window": 20, **_DEFAULTS},
)
def broker_concentration_20d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """Σ20 日主力淨買超 ÷ Σ20 日成交量（分子排除隔日沖分點）。"""
    w = _int(params, "window", 20)
    daily = _clean_daily(view, params)
    vol = view.table("prices_daily").select("date", "stock_id", "volume")
    return (
        daily.join(vol, on=["date", "stock_id"], how="inner")
        .sort(["stock_id", "date"])
        .with_columns(
            _n=pl.col("_net").rolling_sum(w).over("stock_id"),
            _v=pl.col("volume").rolling_sum(w).over("stock_id"),
        )
        .with_columns(
            value=pl.when(pl.col("_v") > 0).then(pl.col("_n") / pl.col("_v")).otherwise(None)
        )
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .sort(["date", "stock_id"])
        .collect()
    )


@factor(
    name="chip.broker_count_diff_5d",
    category="chip",
    direction=1,
    tables=_TABLES,
    lookback_days=_LOOKBACK,
    params={"window": 5, **_DEFAULTS},
)
def broker_count_diff_5d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """Σ5 日（賣方分點數 − 買方分點數）÷ Σ5 日總分點數：少數人吸多數人的籌碼為正。"""
    w = _int(params, "window", 5)
    return (
        _clean_daily(view, params)
        .sort(["stock_id", "date"])
        .with_columns(
            _d=(pl.col("_ns") - pl.col("_nb")).rolling_sum(w).over("stock_id"),
            _t=(pl.col("_ns") + pl.col("_nb")).rolling_sum(w).over("stock_id"),
        )
        .with_columns(
            value=pl.when(pl.col("_t") > 0).then(pl.col("_d") / pl.col("_t")).otherwise(None)
        )
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .sort(["date", "stock_id"])
        .collect()
    )


@factor(
    name="chip.broker_persistence_10d",
    category="chip",
    direction=1,
    tables=_TABLES,
    lookback_days=_LOOKBACK,
    params={"window": 10, **_DEFAULTS},
)
def broker_persistence_10d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """近 10 日主力淨買超為正的天數比例（0～1）。"""
    w = _int(params, "window", 10)
    return (
        _clean_daily(view, params)
        .sort(["stock_id", "date"])
        .with_columns(value=(pl.col("_net") > 0).cast(pl.Float64).rolling_mean(w).over("stock_id"))
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .sort(["date", "stock_id"])
        .collect()
    )
