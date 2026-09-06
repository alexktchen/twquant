"""反前視護欄（SPEC-002 §5）。

每個因子的測試檔必須呼叫 assert_no_lookahead：對多個切點 t，用 as_of=t 的視圖算出的
因子值，必須等於用完整視圖算出後過濾到 <= t 的部分。偷看未來的因子會被抓出。
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

import polars as pl

from twquant.factors.registry import get

if TYPE_CHECKING:
    from twquant.data.warehouse import Warehouse

_KEYS = ["date", "stock_id"]


def _run(
    name: str, warehouse: Warehouse, as_of: dt.date, params: dict[str, object] | None = None
) -> pl.DataFrame:
    fdef = get(name)
    assert fdef.fn is not None, f"因子 {name} 無實作"
    out = fdef.fn(warehouse.view(as_of), {**fdef.params, **(params or {})})
    if isinstance(out, pl.LazyFrame):
        out = out.collect()
    return out.sort(_KEYS)


def assert_no_lookahead(
    name: str,
    warehouse: Warehouse,
    cut_dates: list[dt.date],
    params: dict[str, object] | None = None,
) -> None:
    """驗證因子無前視。cut_dates 為切點；warehouse 需含足夠歷史。

    params 覆寫 registry 預設（如 smooth_days），讓非預設參數路徑也能被反前視護欄檢查。
    """
    full = _run(name, warehouse, max(cut_dates), params)
    for t in cut_dates:
        truncated = _run(name, warehouse, t, params)
        expected = full.filter(pl.col("date") <= t).sort(_KEYS)
        if not truncated.equals(expected):
            diff = truncated.join(expected, on=_KEYS, how="anti")
            first = diff.head(1).to_dicts()
            raise AssertionError(
                f"因子 {name} 在切點 {t} 有前視：as_of={t} 的結果 ≠ 完整結果過濾至 {t}。"
                f"首個不一致：{first[0] if first else '(列數/值不同)'}"
            )
