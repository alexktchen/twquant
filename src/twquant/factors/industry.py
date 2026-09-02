"""產業層因子（T-099）。

| 因子 | 出處 | 公式 | 方向 |
|---|---|---|---|
| industry_mom_6m | Moskowitz–Grinblatt 1999 | 所屬產業成員 close(t−5)/close(t−126)−1 的等權平均（跳過最近一週） | + |
| industry_rs_20d | — | 產業 20 日等權報酬 − 加權指數（TAIEX）20 日報酬 | + |

產業別取 `stock_meta.industry`（緩變維度、現值套用全歷史——與 residual_reversal_5d 同一近似）。
無產業別的股票不產值；單一成員產業的「產業報酬」就是自己的報酬（保留，不視為缺值）。
窗內資料不足即 null（列基位移），不得用較短窗充數。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from twquant.factors.registry import factor

if TYPE_CHECKING:
    from twquant.data.warehouse import PitView

_TAIEX = "TAIEX"


def _int(params: dict[str, object], key: str, default: int) -> int:
    raw = params.get(key, default)
    return int(raw) if isinstance(raw, int | float | str) else default


def _industry_mean_return(view: PitView, *, window: int, skip: int) -> pl.LazyFrame:
    """每檔 close(t−skip)/close(t−window) − 1，再對 (date, industry) 取等權平均，回傳成員列。"""
    px = view.table("prices_daily").select("date", "stock_id", "close").sort(["stock_id", "date"])
    ret = (
        pl.col("close").shift(skip).over("stock_id", order_by="date")
        / pl.col("close").shift(window).over("stock_id", order_by="date")
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
        .with_columns(_ind=pl.col("_r").mean().over(["date", "industry"]))
        .select("date", "stock_id", "_ind")
    )


@factor(
    name="price.industry_mom_6m",
    category="price",
    direction=1,
    tables=["prices_daily", "stock_meta"],
    lookback_days=140,
    params={"window": 126, "skip": 5},
)
def industry_mom_6m(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """Moskowitz–Grinblatt 1999：產業 6 個月動能，跳過最近一週以避開短期反轉。"""
    lf = _industry_mean_return(
        view, window=_int(params, "window", 126), skip=_int(params, "skip", 5)
    )
    return lf.rename({"_ind": "value"}).sort(["date", "stock_id"]).collect()


@factor(
    name="price.industry_rs_20d",
    category="price",
    direction=1,
    tables=["prices_daily", "stock_meta", "index_daily"],
    lookback_days=25,
    params={"window": 20},
)
def industry_rs_20d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """產業 20 日等權報酬 − 加權指數 20 日報酬（產業相對強弱）。TAIEX 缺日則該日無值。"""
    window = _int(params, "window", 20)
    ind = _industry_mean_return(view, window=window, skip=0)
    taiex = (
        view.table("index_daily")
        .filter(pl.col("index_id") == _TAIEX)
        .select("date", "close")
        .sort("date")
        .with_columns(_mkt=pl.col("close") / pl.col("close").shift(window) - 1.0)
        .drop_nulls("_mkt")
        .select("date", "_mkt")
    )
    return (
        ind.join(taiex, on="date", how="inner")
        .with_columns(value=pl.col("_ind") - pl.col("_mkt"))
        .select("date", "stock_id", "value")
        .sort(["date", "stock_id"])
        .collect()
    )
