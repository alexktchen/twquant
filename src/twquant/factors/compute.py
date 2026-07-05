"""因子計算引擎（SPEC-002 §2）。

compute 對一組因子名，用給定的 PitView 計算，回傳 long format
(date, stock_id, factor, value)。因子函數負責只看回看窗內的資料（反前視由
assert_no_lookahead 驗證）。
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

import polars as pl

from twquant.errors import TwquantError
from twquant.factors.registry import get

if TYPE_CHECKING:
    from twquant.data.warehouse import PitView

_OUT_COLS = ["date", "stock_id", "factor", "value"]


def _as_df(result: object, name: str) -> pl.DataFrame:
    if isinstance(result, pl.LazyFrame):
        result = result.collect()
    if not isinstance(result, pl.DataFrame):
        raise TwquantError(f"因子 {name} 回傳非 DataFrame：{type(result)}")
    missing = {"date", "stock_id", "value"} - set(result.columns)
    if missing:
        raise TwquantError(f"因子 {name} 回傳缺欄位：{missing}")
    return result


def compute(
    names: list[str],
    view: PitView,
    dates: list[dt.date] | None = None,
) -> pl.DataFrame:
    """計算因子，回傳 (date, stock_id, factor, value)。dates 給定時只保留這些日期。"""
    frames: list[pl.DataFrame] = []
    for name in names:
        fdef = get(name)
        if fdef.fn is None:
            raise TwquantError(f"因子 {name} 無實作函數")
        df = _as_df(fdef.fn(view, fdef.params), name)
        df = df.with_columns(pl.lit(name).alias("factor")).select(_OUT_COLS)
        frames.append(df)

    if not frames:
        return pl.DataFrame(
            schema={
                "date": pl.Date,
                "stock_id": pl.String,
                "factor": pl.String,
                "value": pl.Float64,
            }
        )
    out = pl.concat(frames)
    if dates is not None:
        out = out.filter(pl.col("date").is_in(dates))
    return out.sort(["factor", "date", "stock_id"])
