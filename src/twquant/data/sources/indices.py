"""指數轉接器（SPEC-001）：加權指數 TAIEX（TWSE）、櫃買指數 TPEX（TPEx）。

兩來源皆「一次一整月」（回整月每個交易日一列），與月營收同屬期批擷取（非每日 Source
協定）。TWSE 指數歷史端點（MI_5MINS_HIST）不受 D-015 個股端點封鎖影響，可安全回補全期；
TPEx tradingIndex 需帶瀏覽器 headers（見 browser_client）。回傳以位置命名（c0、c1…）的
原始字串 DF——不依賴中文欄名（避免編碼/欄序變動），清洗層以位置取欄。
"""

from __future__ import annotations

import json
from typing import Any

import polars as pl

from twquant.data.http import HttpClient
from twquant.data.sources import raw_frame
from twquant.errors import DataError

_TWSE_URL = "https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST"
_TPEX_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingIndex"


def _positional_frame(rows: list[list[Any]]) -> pl.DataFrame:
    """把 rows 轉成位置命名（c0、c1…）的原始字串 DF（不用來源中文欄名）。"""
    width = max((len(r) for r in rows), default=0)
    return raw_frame([f"c{i}" for i in range(width)], rows)


class TwseIndexSource:
    """加權指數（發行量加權股價指數）月批：回整月日 OHLC（c0=日期,c1..c4=開高低收）。"""

    name = "twse_index"

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    def fetch_month(self, year: int, month: int) -> pl.DataFrame:
        params = {"date": f"{year}{month:02d}01", "response": "json"}
        resp = self.client.get(_TWSE_URL, params=params)
        try:
            payload = json.loads(resp.text)
        except json.JSONDecodeError as exc:
            raise DataError("index_daily", None, f"TWSE 指數回應非 JSON：{exc}") from exc
        if payload.get("stat") != "OK":
            raise DataError(
                "index_daily", None, f"TWSE 加權指數 stat={payload.get('stat')!r}（{year}/{month}）"
            )
        rows = payload.get("data", [])
        if not rows:
            raise DataError("index_daily", None, f"TWSE 加權指數 {year}/{month} 無資料")
        return _positional_frame(rows)


class TpexIndexSource:
    """櫃買指數月批：回整月「日成交量值指數」（c0=日期,c4=收盤指數）。需瀏覽器 headers。"""

    name = "tpex_index"

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    def fetch_month(self, year: int, month: int) -> pl.DataFrame:
        params = {"date": f"{year}/{month:02d}/01", "response": "json"}
        resp = self.client.get(_TPEX_URL, params=params)
        try:
            payload = json.loads(resp.text)
        except json.JSONDecodeError as exc:
            raise DataError(
                "index_daily", None, f"TPEx 指數回應非 JSON（可能被 anti-bot 擋）：{exc}"
            ) from exc
        for tb in payload.get("tables", []):
            rows = tb.get("data", [])
            if rows:
                return _positional_frame(rows)
        raise DataError("index_daily", None, f"TPEx 櫃買指數 {year}/{month} 無資料")
