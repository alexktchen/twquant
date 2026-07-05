"""來源轉接器（SPEC-001 §2）。

每個轉接器實作 Source 協定：抓一天/一期的原始資料，回傳原始欄名的 polars
DataFrame，不做單位/型別轉換（那是清洗層 T-011 的職責）。失敗 raise DataError。
"""

from __future__ import annotations

import datetime as dt
from typing import Protocol

import polars as pl


class Source(Protocol):
    """資料來源轉接器協定。"""

    name: str
    tables: list[str]

    def fetch(self, table: str, date: dt.date) -> pl.DataFrame:
        """抓 date 當日 table 的原始資料（原始欄名，未轉換）。失敗 raise DataError。"""
        ...
