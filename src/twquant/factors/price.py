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
    sret = pl.col("close") / pl.col("close").shift(window).over("stock_id", order_by="date") - 1.0
    idx = (
        view.table("index_daily")
        .filter(pl.col("index_id") == "TAIEX")
        .select("date", "close")
        .sort("date")
    )
    idx = idx.with_columns(_mret=pl.col("close") / pl.col("close").shift(window) - 1.0).select(
        "date", "_mret"
    )
    return (
        px.with_columns(_sret=sret)
        .join(idx, on="date", how="inner")
        .with_columns(value=pl.col("_sret") - pl.col("_mret"))
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .collect()
    )
