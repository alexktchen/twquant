"""來源轉接器（SPEC-001 §2）。

每個轉接器實作 Source 協定：抓一天/一期的原始資料，回傳原始欄名的 polars
DataFrame，不做單位/型別轉換（那是清洗層 T-011 的職責）。失敗 raise DataError。
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Protocol

import polars as pl


class Source(Protocol):
    """資料來源轉接器協定。"""

    name: str

    @property
    def tables(self) -> list[str]: ...

    def fetch(self, table: str, date: dt.date) -> pl.DataFrame:
        """抓 date 當日 table 的原始資料（原始欄名，未轉換）。失敗 raise DataError。"""
        ...


def dedupe_fields(fields: list[str]) -> list[str]:
    """欄名去重：重複者附 _2、_3…（polars 不允許重複欄名）。"""
    seen: dict[str, int] = {}
    out: list[str] = []
    for f in fields:
        seen[f] = seen.get(f, 0) + 1
        out.append(f if seen[f] == 1 else f"{f}_{seen[f]}")
    return out


def raw_frame(fields: list[str], rows: list[list[Any]]) -> pl.DataFrame:
    """把 (fields, rows) 轉成全字串的原始 DataFrame（去重欄名、逐列補齊到欄數）。"""
    cols = dedupe_fields(fields)
    data: dict[str, list[str | None]] = {c: [] for c in cols}
    for row in rows:
        for i, c in enumerate(cols):
            data[c].append(str(row[i]) if i < len(row) else None)
    return pl.DataFrame(data, schema={c: pl.String for c in cols})
