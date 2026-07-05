"""TWSE rwd 轉接器（SPEC-001）：日 K（MI_INDEX）、三大法人（T86）、融資融券（MI_MARGN）。

回傳原始中文欄名的 polars DataFrame（全 Utf8，未轉型/未換單位）。TWSE 部分端點把
資料包在 tables[] 內（一個 response 多張表），本轉接器以「欄位含股票代號欄」挑出
個股資料表；T86 為扁平 fields/data。重複欄名（MI_MARGN 資/券同名）自動去重。
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any

import polars as pl

from twquant.data.http import HttpClient
from twquant.data.sources import raw_frame
from twquant.errors import DataError

_BASE = "https://www.twse.com.tw/rwd/zh"


@dataclass(frozen=True)
class _Endpoint:
    path: str
    params: dict[str, str]
    id_field: str  # 個股資料表必含的欄位（用來從 tables[] 挑對表；也作空資料判定）


_ENDPOINTS: dict[str, _Endpoint] = {
    "prices_daily": _Endpoint(
        "afterTrading/MI_INDEX", {"type": "ALLBUT0999", "response": "json"}, "證券代號"
    ),
    "institutional_daily": _Endpoint(
        "fund/T86", {"selectType": "ALLBUT0999", "response": "json"}, "證券代號"
    ),
    "margin_daily": _Endpoint(
        "marginTrading/MI_MARGN", {"selectType": "ALL", "response": "json"}, "代號"
    ),
}


def _extract(
    payload: dict[str, Any], id_field: str, table: str, date: dt.date
) -> tuple[list[str], list[list[Any]]]:
    """從 TWSE JSON 取出 (fields, data)：扁平或 tables[] 兩種結構皆可。"""
    if payload.get("stat") != "OK":
        raise DataError(table, date, f"TWSE 回應 stat={payload.get('stat')!r}（非交易日或無資料）")
    # 扁平結構（T86）
    if id_field in payload.get("fields", []):
        return payload["fields"], payload.get("data", [])
    # tables[] 結構（MI_INDEX / MI_MARGN）：挑欄位含 id_field 的資料表
    for tb in payload.get("tables", []):
        fields = tb.get("fields", [])
        if id_field in fields:
            return fields, tb.get("data", [])
    raise DataError(table, date, f"回應中找不到含「{id_field}」的資料表")


@dataclass
class TwseRwdSource:
    """TWSE rwd 來源轉接器。"""

    client: HttpClient
    name: str = "twse_rwd"

    @property
    def tables(self) -> list[str]:
        return list(_ENDPOINTS)

    def fetch(self, table: str, date: dt.date) -> pl.DataFrame:
        if table not in _ENDPOINTS:
            raise DataError(table, date, f"twse_rwd 不支援此表：{table}")
        ep = _ENDPOINTS[table]
        params = {**ep.params, "date": date.strftime("%Y%m%d")}
        resp = self.client.get(f"{_BASE}/{ep.path}", params=params)
        try:
            payload = json.loads(resp.text)
        except json.JSONDecodeError as exc:
            raise DataError(table, date, f"回應非 JSON：{exc}") from exc

        fields, rows = _extract(payload, ep.id_field, table, date)
        if not rows:
            raise DataError(table, date, "資料表為空（非交易日或當日尚未公布）")
        return raw_frame(fields, rows)
