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
