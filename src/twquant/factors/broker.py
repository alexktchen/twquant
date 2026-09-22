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


def daytrade_events(view: PitView) -> pl.LazyFrame:
    """每個分點在各結算日的**當日**買超事件數與隔日沖次數（尚未累計）。

    拆出來是為了讓分段計算可行（T-271）：`broker_trades` 有 5,770 萬列，
    一次展開會把 PostgreSQL 連同 Docker 一起壓垮（2026-09-22 實際發生）。
    分段時每段各自算事件（本函數，純區域聚合），再把各段接起來做一次累計
    （`cumulate_events`）——累計是對一個「每分點每日一列」的小表做，撐得住。
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
    )
    return events


def cumulate_events(events: pl.LazyFrame) -> pl.LazyFrame:
    """把逐日事件累計成 expanding 統計：(broker_id, resolve_date, n_buys, n_flip, ratio)。

    只看過去（`cum_sum` 沿 resolve_date 遞增），故逐日算與整批算等價。
    """
    return (
        events.sort(["broker_id", "resolve_date"])
        .with_columns(
            n_buys=pl.col("_b").cum_sum().over("broker_id"),
            n_flip=pl.col("_f").cum_sum().over("broker_id"),
        )
        .with_columns(ratio=pl.col("n_flip") / pl.col("n_buys"))
        .select("broker_id", "resolve_date", "n_buys", "n_flip", "ratio")
    )


def _daytrade_state(view: PitView) -> pl.LazyFrame:
    """至各結算日為止的 expanding 隔日沖統計（＝事件 → 累計）。"""
    return cumulate_events(daytrade_events(view))


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


def clean_daily(
    view: PitView, params: dict[str, object], *, state: pl.LazyFrame | None = None
) -> pl.LazyFrame:
    """排除隔日沖分點後，每檔每日的主力淨買超、買方／賣方分點數。

    `state` 可注入（T-271 分段計算）：分段時每段只讀自己那一段的 `broker_trades`，
    但隔日沖判定要用**全期**累計統計，否則早期分段會把還沒累積夠事件的分點誤判成正常。
    不給就當場從 `view` 算（既有行為）。
    """
    min_ratio = _num(params, "daytrade_min_ratio", 0.5)
    min_events = _num(params, "daytrade_min_events", 30)
    t = _trades(view).sort("date")
    state = (state if state is not None else _daytrade_state(view)).sort("resolve_date")
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


# ── 面板數學（T-271）：因子本體與分段腳本共用同一份，避免兩套實作漂移 ──────
# 輸入一律是 `clean_daily` 的輸出（每檔每日一列），故這三個函數與資料量無關。


def concentration_values(daily: pl.LazyFrame, vol: pl.LazyFrame, window: int) -> pl.LazyFrame:
    """Σwindow 日主力淨買超 ÷ Σwindow 日成交量。"""
    return (
        daily.join(vol, on=["date", "stock_id"], how="inner")
        .sort(["stock_id", "date"])
        .with_columns(
            _n=pl.col("_net").rolling_sum(window).over("stock_id"),
            _v=pl.col("volume").rolling_sum(window).over("stock_id"),
        )
        .with_columns(
            value=pl.when(pl.col("_v") > 0).then(pl.col("_n") / pl.col("_v")).otherwise(None)
        )
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .sort(["date", "stock_id"])
    )


def count_diff_values(daily: pl.LazyFrame, window: int) -> pl.LazyFrame:
    """Σwindow 日（賣方分點數 − 買方分點數）÷ Σwindow 日總分點數。"""
    return (
        daily.sort(["stock_id", "date"])
        .with_columns(
            _d=(pl.col("_ns") - pl.col("_nb")).rolling_sum(window).over("stock_id"),
            _t=(pl.col("_ns") + pl.col("_nb")).rolling_sum(window).over("stock_id"),
        )
        .with_columns(
            value=pl.when(pl.col("_t") > 0).then(pl.col("_d") / pl.col("_t")).otherwise(None)
        )
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .sort(["date", "stock_id"])
    )


def persistence_values(daily: pl.LazyFrame, window: int) -> pl.LazyFrame:
    """近 window 日主力淨買超為正的天數比例。"""
    return (
        daily.sort(["stock_id", "date"])
        .with_columns(
            value=(pl.col("_net") > 0).cast(pl.Float64).rolling_mean(window).over("stock_id")
        )
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .sort(["date", "stock_id"])
    )


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
    vol = view.table("prices_daily").select("date", "stock_id", "volume")
    return concentration_values(clean_daily(view, params), vol, w).collect()


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
    return count_diff_values(clean_daily(view, params), w).collect()


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
    return persistence_values(clean_daily(view, params), w).collect()
