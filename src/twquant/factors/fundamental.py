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


def _valuation(view: PitView, col: str, *, positive_only: bool, reciprocal: bool) -> pl.DataFrame:
    """`stock_valuation` 的單欄取值；PIT 由 view 保證（僅回 as_of 可見的列）。

    positive_only：非正值先轉 null 再取倒數——少了這道防護，PBR=0 會產生 inf
    （T-079 的教訓：`ml/dataset.py` 同樣的缺漏讓 open=0 毀掉整個模型）。
    """
    base = pl.col(col)
    if positive_only:
        base = pl.when(pl.col(col) > 0.0).then(pl.col(col)).otherwise(None)
    expr = (1.0 / base) if reciprocal else base
    return (
        view.table("stock_valuation")
        .select("date", "stock_id", expr.alias("value"))
        .drop_nulls("value")
        .collect()
    )


@factor(
    name="fundamental.earnings_yield",
    category="fundamental",
    direction=1,
    tables=["stock_valuation"],
    lookback_days=1,
    params={},
)
def earnings_yield(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """盈餘收益率 E/P ＝ 1/PER（正向：越便宜越看多）。

    用倒數而非 PER 本身：實測 PER 最大 24,950、最小 −1.0，直接用會被極端值主導；
    E/P 在 PER→∞ 時趨近 0、虧損時為負（有意義的方向）。負 PER 不轉 null。
    """
    return _valuation(view, "per", positive_only=False, reciprocal=True)


@factor(
    name="fundamental.book_yield",
    category="fundamental",
    direction=1,
    tables=["stock_valuation"],
    lookback_days=1,
    params={},
)
def book_yield(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """淨值收益率 B/P ＝ 1/PBR（正向：越便宜越看多）。PBR <= 0 為無效值 → null。"""
    return _valuation(view, "pbr", positive_only=True, reciprocal=True)


@factor(
    name="fundamental.dividend_yield",
    category="fundamental",
    direction=1,
    tables=["stock_valuation"],
    lookback_days=1,
    params={},
)
def dividend_yield(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """現金殖利率（正向）。來源映射層已轉小數（黃金規則 3），此處不再換算。"""
    return _valuation(view, "dividend_yield", positive_only=False, reciprocal=False)
