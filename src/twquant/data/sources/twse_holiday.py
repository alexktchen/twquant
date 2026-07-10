"""TWSE 官方「市場開休市日期」轉接器（SPEC-001）。

來源為 TWSE OpenAPI（`openapi.twse.com.tw`），回傳一個 JSON 陣列，每筆為
{Name, Date, Weekday, Description}；Date 為民國封裝格式（"1150101" = 民國 115=2026 年
1 月 1 日）。**此端點無年度參數**，只回「當前已公布年度」的整年開休市註記——包含大多數
休市日，也含少數「開始/最後交易日」的純交易日註記（非休市）；分類交由清洗層。

回傳原始欄名的全字串 DataFrame（未轉型），失敗 raise DataError（黃金規則 5：不靜默）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import polars as pl

from twquant.data.http import HttpClient
from twquant.data.sources import raw_frame
from twquant.errors import DataError

_URL = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"
_REQUIRED = ("Name", "Date", "Weekday", "Description")


@dataclass
class TwseHolidaySource:
    """TWSE 官方開休市日曆來源轉接器（單一 table：market_holidays）。"""

    client: HttpClient
    name: str = "twse_holiday"

    @property
    def tables(self) -> list[str]:
        return ["market_holidays"]

    def fetch(self) -> pl.DataFrame:
        """抓官方開休市日曆（整年）→ 原始欄名 DataFrame。缺欄或空回應即 raise。"""
        resp = self.client.get(_URL)
        try:
            payload: Any = json.loads(resp.text)
        except json.JSONDecodeError as exc:
            raise DataError("market_holidays", None, f"官方日曆回應非 JSON：{exc}") from exc
        if not isinstance(payload, list) or not payload:
            raise DataError("market_holidays", None, "官方開休市日曆回空（來源異常或維護中）")
        missing = [f for f in _REQUIRED if f not in payload[0]]
        if missing:  # 來源改欄 → 大聲失敗，不靜默套用錯誤解析
            raise DataError("market_holidays", None, f"官方日曆缺必要欄位：{missing}")
        rows = [[str(rec.get(f, "")) for f in _REQUIRED] for rec in payload]
        return raw_frame(list(_REQUIRED), rows)
