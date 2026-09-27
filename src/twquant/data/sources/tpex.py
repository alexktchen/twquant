"""TPEX（上櫃）轉接器（SPEC-001 / DECISIONS D-014）。

第一版聚焦上櫃「日行情」（afterTrading/dailyQuotes，支援歷史日期，且附次日漲跌停價）；
T-313 加上個股本益比／殖利率／淨值比（afterTrading/peQryDate，依日期查詢）。
TPEX 有 anti-bot，需帶完整瀏覽器 headers。上櫃法人/融資券的歷史資料改由 FinMind
（T-010，統一 TWSE+TPEX）提供，避免與 TPEX 僅回最新日的 OpenAPI 產生日期不符前視。
"""

from __future__ import annotations

import datetime as dt
import json

import polars as pl

from twquant.data.http import HttpClient
from twquant.data.sources import raw_frame
from twquant.errors import DataError

_BASE = "https://www.tpex.org.tw/www/zh-tw/afterTrading"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
_HEADERS = {"Accept": "application/json", "Referer": "https://www.tpex.org.tw/"}

# 表 → (端點, 個股資料表必含的欄位)。兩個端點都是 tables[] 結構、日期寫 2026/09/18。
_ENDPOINTS: dict[str, tuple[str, str]] = {
    "prices_daily": ("dailyQuotes", "代號"),
    # T-313：個股本益比、殖利率及股價淨值比（依日期查詢）；網頁 mainboard/trading/info/daily-pe.html
    "stock_valuation": ("peQryDate", "股票代號"),
}


def browser_client(rate: object, **kwargs: object) -> HttpClient:
    """建立帶瀏覽器 headers 的 HttpClient（繞過 TPEX anti-bot）。"""
    from twquant.config import SourceRate

    assert isinstance(rate, SourceRate)
    return HttpClient(source="tpex", rate=rate, user_agent=_UA, headers=dict(_HEADERS), **kwargs)  # type: ignore[arg-type]


class TpexSource:
    """TPEX 上櫃來源轉接器。"""

    name = "tpex"

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    @property
    def tables(self) -> list[str]:
        return list(_ENDPOINTS)

    def fetch(self, table: str, date: dt.date) -> pl.DataFrame:
        if table not in _ENDPOINTS:
            raise DataError(
                table,
                date,
                f"tpex 只支援 {list(_ENDPOINTS)}（{table} 走 FinMind，T-010）",
            )
        path, id_field = _ENDPOINTS[table]
        params = {"date": date.strftime("%Y/%m/%d"), "response": "json"}
        resp = self.client.get(f"{_BASE}/{path}", params=params)
        try:
            payload = json.loads(resp.text)
        except json.JSONDecodeError as exc:
            raise DataError(table, date, f"回應非 JSON（可能被 anti-bot 擋）：{exc}") from exc

        for tb in payload.get("tables", []):
            fields = tb.get("fields", [])
            if id_field in fields:
                rows = tb.get("data", [])
                if not rows:
                    raise DataError(table, date, "上櫃資料表為空（非交易日或尚未公布）")
                return raw_frame(fields, rows)
        raise DataError(table, date, f"回應中找不到含「{id_field}」的上櫃資料表")
