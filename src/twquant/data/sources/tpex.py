"""TPEX（上櫃）轉接器（SPEC-001 / DECISIONS D-014）。

第一版聚焦上櫃「日行情」（afterTrading/dailyQuotes，支援歷史日期，且附次日漲跌停價）；
T-313 加上個股本益比／殖利率／淨值比（afterTrading/peQryDate，依日期查詢）。
TPEX 有 anti-bot，需帶完整瀏覽器 headers。

上櫃三大法人與融資融券（2026-10-17 補）：原本規劃由 FinMind 提供（T-010，舊版 OpenAPI 只回最新一天），
但每日 pipeline 從沒接上 FinMind，上櫃這兩張表 2026-07 起幾乎整段缺。櫃買新網站的 `insti/dailyTrade`
（sect=EW＝上櫃股票）與 `margin/balance` 都吃日期、回當天那份，2026-09-04 與 FinMind 補的資料逐檔比對
法人 767/767、融資 802/802 完全相同，於是直接接進每日擷取。
"""

from __future__ import annotations

import datetime as dt
import json

import polars as pl

from twquant.data.http import HttpClient
from twquant.data.sources import raw_frame
from twquant.errors import DataError

_SITE = "https://www.tpex.org.tw/www/zh-tw"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
_HEADERS = {"Accept": "application/json", "Referer": "https://www.tpex.org.tw/"}

# 表 → (路徑, 額外參數, 個股資料表必含的欄位)。都是 tables[] 結構、日期寫 2026/09/18。
_ENDPOINTS: dict[str, tuple[str, dict[str, str], str]] = {
    "prices_daily": ("afterTrading/dailyQuotes", {}, "代號"),
    # T-313：個股本益比、殖利率及股價淨值比（依日期查詢）
    "stock_valuation": ("afterTrading/peQryDate", {}, "股票代號"),
    # 三大法人買賣明細（sect=EW＝上櫃股票）：七組「買進／賣出／買賣超股數」＋合計，清洗層驗加總
    "institutional_daily": ("insti/dailyTrade", {"type": "Daily", "sect": "EW"}, "代號"),
    # 上櫃股票融資融券餘額（單位：張）
    "margin_daily": ("margin/balance", {}, "代號"),
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
                f"tpex 只支援 {list(_ENDPOINTS)}",
            )
        path, extra, id_field = _ENDPOINTS[table]
        params = {**extra, "date": date.strftime("%Y/%m/%d"), "response": "json"}
        resp = self.client.get(f"{_SITE}/{path}", params=params)
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
