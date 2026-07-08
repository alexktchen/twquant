"""擷取層的來源／儲存埠（D-017）。

兩個 Protocol，讓資料來源與儲存後端可獨立抽換：

* **SourcePort**（來源）：自足的來源轉接器——`load(table, period)` = 抓取＋清洗＋
  驗證成 canonical DataFrame。既有 twse/tpex 以 `FetchCleanSource` 包裝現行
  `Source` + `clean.py` 的 cleaner，零改寫即成 SourcePort。
* **IngestSink**（儲存）：唯寫 + 擷取期探索（write／partition_exists／source_dates／
  latest_date）。方法名與簽名逐字對齊 `Warehouse`，故 `Warehouse` 以結構型別零改動
  即滿足。`source` 為不透明的 disjoint-writer 標籤；實體如 part-<source>.parquet 或
  DB 交易 DELETE+INSERT 屬 adapter 細節。**分析讀取側**（scan/sql/view/PitView）刻意
  不納入此埠、維持具體 Warehouse（D-008 PIT 憲法邊界，抽象化屬未來另案）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import polars as pl

    from twquant.data.sources import Source

CleanFn = Callable[["pl.DataFrame", dt.date], "pl.DataFrame"]


@runtime_checkable
class IngestSink(Protocol):
    """擷取寫入 + 擷取期探索埠（唯寫，不含分析讀取）。Warehouse 結構即滿足。"""

    def write(self, layer: str, table: str, df: pl.DataFrame, source: str | None = None) -> None:
        """把清洗後資料寫入 (table, source) 分片（分區級替換，同片重寫=最後一次）。"""
        ...

    def partition_exists(
        self, layer: str, table: str, date: dt.date, source: str | None = None
    ) -> bool:
        """(table, date, source) 分片是否已有資料（供 backfill 續傳跳過）。"""
        ...

    def source_dates(self, table: str, source: str, layer: str = "clean") -> list[dt.date]:
        """某來源在該表已存資料的日期（排序）。供擷取狀態/缺口檢視。"""
        ...

    def latest_date(self, table: str, layer: str = "clean") -> dt.date | None:
        """該表最新一個有資料的日期。"""
        ...


class SourcePort(Protocol):
    """自足來源轉接器：load() = 抓取 + 清洗 + 驗證成 canonical DataFrame。"""

    @property
    def name(self) -> str:
        """儲存來源標籤（唯讀；frozen dataclass 欄位與一般屬性皆滿足）。"""
        ...

    @property
    def tables(self) -> list[str]:
        """本來源可供應的 clean 表名。"""
        ...

    def load(self, table: str, period: dt.date) -> pl.DataFrame:
        """抓 period 當期 table 並清洗驗證成 canonical DataFrame。失敗 raise DataError。"""
        ...


@dataclass(frozen=True)
class FetchCleanSource:
    """把既有『Source.fetch（raw）+ 分離 cleaner』包成自足 SourcePort（零改寫既有來源）。"""

    name: str
    inner: Source
    cleaners: Mapping[str, CleanFn]

    @property
    def tables(self) -> list[str]:
        return [t for t in self.inner.tables if t in self.cleaners]

    def load(self, table: str, period: dt.date) -> pl.DataFrame:
        return self.cleaners[table](self.inner.fetch(table, period), period)
