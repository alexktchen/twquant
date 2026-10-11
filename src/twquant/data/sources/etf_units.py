"""ETF 已發行單位數的兩個來源（SPEC-016 §2.2，T-335）。

| 用途 | 來源 | 一次拿到 |
|---|---|---|
| 每日 | TWSE `fund/MI_QFIIS?selectType=0099P`（外資持股統計，ETF 類） | 某一天全部上市 ETF |
| 歷史 | FinMind `TaiwanStockShareholding.NumberOfSharesIssued`（同一份統計） | 某一檔 ETF 的整段歷史 |

T-334 盤點：兩者同日同值（2026-10-08 的 0050 22,251,000,000、00981A 9,025,209,000）。
「發行股數」對 ETF 就是已發行受益權單位數；單位是**單位（股）**，不換算。
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterable, Mapping
from typing import Any

from twquant.data.http import HttpClient
from twquant.data.sources.twse_rwd import _BASE, _extract
from twquant.errors import DataError

TABLE = "etf_units_daily"
QFIIS_PATH = "fund/MI_QFIIS"
QFIIS_PARAMS = {"selectType": "0099P", "response": "json"}
_ID = "證券代號"
_UNITS = "發行股數"


def _to_units(raw: Any, day: dt.date, etf_id: str, src: str) -> int:
    """'22,251,000,000' 或 22251000000 → int；缺值、負數、非整數一律 raise（不得當 0）。"""
    if isinstance(raw, bool) or raw is None:
        raise DataError(TABLE, day, f"{etf_id}：{src} 缺值或型別錯：{raw!r}")
    if isinstance(raw, str):
        text = raw.replace(",", "").strip()
        if not text.isdigit():
            raise DataError(TABLE, day, f"{etf_id}：{src} 不是非負整數：{raw!r}")
        return int(text)
    if isinstance(raw, int | float) and float(raw).is_integer() and raw >= 0:
        return int(raw)
    raise DataError(TABLE, day, f"{etf_id}：{src} 不是非負整數：{raw!r}")


def qfiis_units_rows(payload: Mapping[str, Any], day: dt.date) -> list[dict[str, Any]]:
    """TWSE MI_QFIIS（ETF 類）JSON → [{date, etf_id, units}]（依代號排序；代號重複 raise）。"""
    fields, rows = _extract(dict(payload), _ID, TABLE, day)
    if _UNITS not in fields:
        raise DataError(TABLE, day, f"MI_QFIIS 缺欄位「{_UNITS}」（來源改版？）；fields={fields}")
    reported = str(payload.get("date", ""))
    if reported != day.strftime("%Y%m%d"):
        raise DataError(TABLE, day, f"MI_QFIIS 回的是 {reported!r} 的資料，不是要的那天")
    i_id, i_units = fields.index(_ID), fields.index(_UNITS)
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        etf_id = str(r[i_id]).strip()
        if etf_id in out:
            raise DataError(TABLE, day, f"MI_QFIIS 同一天 {etf_id} 出現兩次")
        out[etf_id] = {
            "date": day,
            "etf_id": etf_id,
            "units": _to_units(r[i_units], day, etf_id, _UNITS),
        }
    if not out:
        raise DataError(TABLE, day, "MI_QFIIS 資料表為空（非交易日或當日尚未公布）")
    return [out[k] for k in sorted(out)]


def fetch_qfiis_units(client: HttpClient, day: dt.date) -> list[dict[str, Any]]:
    """抓 TWSE MI_QFIIS（ETF 類）某一天 → [{date, etf_id, units}]。"""
    params = {**QFIIS_PARAMS, "date": day.strftime("%Y%m%d")}
    resp = client.get(f"{_BASE}/{QFIIS_PATH}", params=params)
    try:
        payload = json.loads(resp.text)
    except json.JSONDecodeError as exc:
        raise DataError(TABLE, day, f"MI_QFIIS 回應非 JSON：{exc}") from exc
    return qfiis_units_rows(payload, day)


def finmind_units_rows(rows: Iterable[Mapping[str, Any]], field: str) -> list[dict[str, Any]]:
    """FinMind TaiwanStockShareholding → [{date, etf_id, units}]（依日期、代號排序；同鍵重複 raise）。"""
    out: dict[tuple[dt.date, str], dict[str, Any]] = {}
    for row in rows:
        try:
            day = dt.date.fromisoformat(str(row["date"]))
        except (KeyError, ValueError) as exc:
            raise DataError(TABLE, None, f"FinMind 列缺 date 或格式錯：{row!r}") from exc
        sid = row.get("stock_id")
        if not isinstance(sid, str) or not sid.strip():
            raise DataError(TABLE, day, f"FinMind 列缺 stock_id：{row!r}")
        etf_id = sid.strip()
        if field not in row:
            raise DataError(TABLE, day, f"{etf_id}：FinMind 回應缺欄位 {field}（來源改版？）")
        key = (day, etf_id)
        if key in out:
            raise DataError(TABLE, day, f"FinMind 同一天 {etf_id} 出現兩次")
        out[key] = {
            "date": day,
            "etf_id": etf_id,
            "units": _to_units(row[field], day, etf_id, field),
        }
    return [out[k] for k in sorted(out)]
