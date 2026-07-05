"""MOPS 月營收轉接器（SPEC-001）。

月營收是台股獨有的月頻基本面（STRATEGY P0 因子）。來源 t21sc03 為 Big5 編碼的
HTML 表格，逐月一頁（民國年_月），上市 sii / 上櫃 otc 各一。回傳原始欄名的 DF。
"""

from __future__ import annotations

import re

import polars as pl

from twquant.data.http import HttpClient
from twquant.data.sources import raw_frame
from twquant.errors import DataError

_BASE = "https://mopsov.twse.com.tw/nas/t21"

# t21sc03 固定 10 欄
_FIELDS = [
    "公司代號",
    "公司名稱",
    "當月營收",
    "上月營收",
    "去年當月營收",
    "上月比較增減",
    "去年同月增減",
    "當月累計營收",
    "去年累計營收",
    "前期比較增減",
]

_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TAG = re.compile(r"<[^>]+>")
_STOCK = re.compile(r"^\d{4,6}[A-Z]?$")


def _cell(html: str) -> str:
    return _TAG.sub("", html).replace("&nbsp;", "").strip()


class MopsSource:
    """MOPS 月營收來源。月頻，不走每日 Source 協定。"""

    name = "mops"

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    def fetch_month(self, roc_year: int, month: int, market: str = "sii") -> pl.DataFrame:
        """抓某民國年月的月營收（market: sii 上市 / otc 上櫃）。原始 10 欄字串 DF。"""
        url = f"{_BASE}/{market}/t21sc03_{roc_year}_{month}_0.html"
        resp = self.client.get(url)
        html = resp.content.decode("big5", errors="replace")
        rows: list[list[str]] = []
        for tr in _TR.findall(html):
            cells = [_cell(c) for c in _TD.findall(tr)]
            if len(cells) >= 10 and _STOCK.match(cells[0]):
                rows.append(cells[:10])
        if not rows:
            raise DataError("monthly_revenue", None, f"{market} {roc_year}/{month} 無月營收資料")
        return raw_frame(_FIELDS, rows)
