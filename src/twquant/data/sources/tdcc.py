"""TDCC 集保戶股權分散表轉接器（SPEC-001 / D-017）。

opendata `getOD.ashx?id=1-5`：單一全市場週快照 CSV（utf-8-sig）。實作自足 SourcePort
（load = fetch → raw_frame → clean_shareholding_dispersion）；同時保留 `fetch`（raw）
以便以假 transport 離線測試傳輸/解析。

覆蓋範圍：id=1-5 目前為 6 碼證券（ETF/受益證券/債券）；四碼普通股的集保分散表僅
TDCC 互動查詢站提供（表單 POST），屬未來另一個 SourcePort——正是 D-017 可插拔設計
要讓其一檔＋一筆註冊即可加入的情境。
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from dataclasses import dataclass

import polars as pl

from twquant.data.clean import clean_shareholding_dispersion
from twquant.data.http import HttpClient
from twquant.data.sources import raw_frame
from twquant.errors import DataError

_URL = "https://opendata.tdcc.com.tw/getOD.ashx"
_TABLE = "shareholding_dispersion"


@dataclass
class TdccDispersionSource:
    """TDCC 集保戶股權分散來源（自足 SourcePort，亦相容 Source.fetch）。"""

    client: HttpClient
    name: str = "tdcc"

    @property
    def tables(self) -> list[str]:
        return [_TABLE]

    def fetch(self, table: str, date: dt.date) -> pl.DataFrame:
        """抓 id=1-5 全市場 CSV → raw（原始欄名，全 Utf8）。date 未用（單一週快照）。"""
        if table != _TABLE:
            raise DataError(table, date, f"tdcc 僅支援 {_TABLE}")
        resp = self.client.get(_URL, params={"id": "1-5"})
        text = resp.content.decode("utf-8-sig")  # opendata CSV 帶 BOM
        rows = list(csv.reader(io.StringIO(text)))
        if len(rows) < 2:
            raise DataError(table, date, "TDCC 回應無資料列（僅表頭或空）")
        header, data = rows[0], rows[1:]
        return raw_frame(header, data)

    def load(self, table: str, period: dt.date) -> pl.DataFrame:
        """SourcePort：抓取 + 清洗 + 驗證成 canonical shareholding_dispersion。"""
        return clean_shareholding_dispersion(self.fetch(table, period), period)
