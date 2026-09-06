"""TWSE ISIN 個股基本資料轉接器（SPEC-001）：代號、中文名、市場別、產業別、上市日。

C_public.jsp 為 Big5 編碼 HTML 表格；strMode=2 上市、strMode=4 上櫃。緩變維度表
（stock_meta），供中文名 / 族群（產業別）顯示與板塊聚合（資金移動板塊）之基礎。
一次抓一個市場（整份清單），非每日 Source 協定。
"""

from __future__ import annotations

import re

import polars as pl

from twquant.data.http import HttpClient
from twquant.data.sources import raw_frame
from twquant.errors import DataError

_URL = "https://isin.twse.com.tw/isin/C_public.jsp"

# C_public.jsp 每列 7 欄：代號名稱 / ISIN / 上市日 / 市場別 / 產業別 / CFICode / 備註
_FIELDS = ["code_name", "isin", "list_date", "market", "industry", "cfi", "note"]

_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TAG = re.compile(r"<[^>]+>")
_ROW = re.compile(r"^[0-9]{4,6}[A-Z]?　")  # 代號 + 全形空格 + 名稱（排除分節標題列）


# 「碁」（U+7881）等字**不在標準 Big5** 的映射內，只存在於 CP950／Big5-HKSCS 擴充。
# 舊版用 decode("big5", errors="replace") → 14 檔含「碁」的名稱（含 2353 宏碁）被靜默
# 換成 U+FFFD，而且四年沒被發現，因為沒有任何錯誤（T-117）。TWSE 是台灣站台，故用 cp950。
_ISIN_ENCODING = "cp950"


def decode_isin_html(content: bytes) -> str:
    """把 ISIN 頁面的位元組解成文字。無法對映即 raise，**不得靜默替換**。

    errors="replace" 正是那個缺陷存活的原因：名字壞了卻沒有任何訊號，
    而 `reconcile_stock_meta`（T-111）刻意不覆蓋既有列，於是一路被保留（黃金規則 5）。
    """
    try:
        return content.decode(_ISIN_ENCODING)
    except UnicodeDecodeError as exc:
        raise DataError(
            "stock_meta",
            None,
            f"ISIN 頁面以 {_ISIN_ENCODING} 解碼失敗（位元組 {exc.start}~{exc.end}）：{exc.reason}"
            "——不得改用 errors='replace' 繞過，那會把中文名靜默換成替換字元。",
        ) from exc


def _cell(html: str) -> str:
    return _TAG.sub("", html).replace("&nbsp;", "").strip()


class StockMetaSource:
    """TWSE ISIN 個股基本資料來源。緩變維度，一次抓整個市場。"""

    name = "twse_isin"

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    def fetch(self, str_mode: int) -> pl.DataFrame:
        """抓某市場全清單（strMode=2 上市 / 4 上櫃）。回原始 7 欄字串 DF。"""
        resp = self.client.get(_URL, params={"strMode": str(str_mode)})
        html = decode_isin_html(resp.content)
        rows: list[list[str]] = []
        for tr in _TR.findall(html):
            cells = [_cell(c) for c in _TD.findall(tr)]
            if len(cells) >= 7 and _ROW.match(cells[0]):
                rows.append(cells[:7])
        if not rows:
            raise DataError("stock_meta", None, f"ISIN strMode={str_mode} 無資料")
        return raw_frame(_FIELDS, rows)
