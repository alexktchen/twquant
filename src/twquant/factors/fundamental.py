"""基本面因子（SPEC-002 / STRATEGY §1-4、§4 的 P0 月營收動能）。

月營收是台股獨有的月頻基本面優勢（STRATEGY §1-4）。月營收於次月約 10 日公布，
因子必須用 available_at（公布日）以 as-of join 映射到每個交易日——只在營收「已公布」
後才可見（PEAD：公布後持有至下次公布）。以月份對齊會偷看未公布營收，屬前視錯誤。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from twquant.factors.registry import factor

if TYPE_CHECKING:
    from twquant.data.warehouse import PitView


def _daily_spine(view: PitView) -> pl.LazyFrame:
    """交易日骨架 (stock_id, date)，供月頻營收 as-of 映射到日頻。"""
    return view.table("prices_daily").select("stock_id", "date").unique().sort(["stock_id", "date"])


def _asof_map(spine: pl.LazyFrame, rev: pl.LazyFrame, value_col: str) -> pl.DataFrame:
    """把每檔的月營收欄位以公布日 as-of（backward）映射到每個交易日。"""
    import warnings

    joined = spine.join_asof(
        rev, left_on="date", right_on="_avail", by="stock_id", strategy="backward"
    )
    with warnings.catch_warnings():
        # 附 by 群組時 polars 無法驗證排序旗標；我們已明確 sort（屬 polars 限制，非資料問題）
        warnings.filterwarnings("ignore", message="Sortedness of columns cannot be checked")
        return (
            joined.select("date", "stock_id", pl.col(value_col).alias("value"))
            .drop_nulls("value")
            .collect()
        )


@factor(
    name="fundamental.revenue_yoy",
    category="fundamental",
    direction=1,
    tables=["monthly_revenue", "prices_daily"],
    lookback_days=430,
    params={},
)
def revenue_yoy(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """最新已公布月營收 YoY（正向：營收年增動能，STRATEGY §1-4 的 PEAD 基礎）。"""
    rev = (
        view.table("monthly_revenue")
        .select("stock_id", "available_at", "yoy")
        .drop_nulls("yoy")
        .with_columns(_avail=pl.col("available_at").dt.date())
        .select("stock_id", "_avail", "yoy")
        .sort(["stock_id", "_avail"])
    )
    return _asof_map(_daily_spine(view), rev, "yoy")


@factor(
    name="fundamental.revenue_accel",
    category="fundamental",
    direction=1,
    tables=["monthly_revenue", "prices_daily"],
    lookback_days=460,
    params={},
)
def revenue_accel(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """月營收 YoY 加速度：最新公布 YoY − 前一月 YoY（正向：成長加速，§1-4）。"""
    rev = (
        view.table("monthly_revenue")
        .select("stock_id", "available_at", "yoy")
        .drop_nulls("yoy")
        .with_columns(_avail=pl.col("available_at").dt.date())
        .sort(["stock_id", "_avail"])
        .with_columns(_prev=pl.col("yoy").shift(1).over("stock_id", order_by="_avail"))
        .with_columns(_accel=pl.col("yoy") - pl.col("_prev"))
        .drop_nulls("_accel")
        .select("stock_id", "_avail", "_accel")
        .sort(["stock_id", "_avail"])
    )
    return _asof_map(_daily_spine(view), rev, "_accel")
