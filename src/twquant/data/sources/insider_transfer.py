"""內部人持股轉讓事前申報（T-384，2026-10-18）：董監事、經理人、大股東「預告要賣股票」的公開申報。

使用者 2026-10-17：不相信策略、要更多資訊輔佐、在名單裡自己挑。這是第一條「給人看、不進模型」的新資料：
內部人要轉讓持股（不論一般交易、鉅額、贈與、信託）依法要在三天前申報，當天就會出現在這兩份表上。

| 市場 | 端點 | 鍵名 |
|---|---|---|
| 上市 | 證交所 OpenAPI `opendata/t187ap12_L` | 中文鍵（`公司代號`、`申報人身分`…） |
| 上櫃 | 櫃買中心 OpenAPI `mopsfin_t187ap12_O` | `Date`／`SecuritiesCompanyCode`／`CompanyName`／`申請人身分`，其餘同上市 |

兩份都是**當天的快照**（`出表日期`／`Date` 是出表那天，週末重抓會拿到同一天），所以每天跑一次、整份 upsert 就會累積成歷史。
2026-09-28 的事件研究（D-166）時只抓過一天（表裡只有 2 列）；之前的歷史免費拿不到，從本任務開始每日累積。

櫃買那份「當天沒有人申報」時回一列全部空字串的占位列（2026-10-16 實見），視為 0 筆；代號空但其他欄有值則 raise。
金額欄位空字串記 None（贈與沒有「轉讓股數」，用轉讓前後持股差算 `planned_shares`）。單位：股。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from typing import Any

import polars as pl

from twquant.conventions import TZ
from twquant.data.http import HttpClient
from twquant.errors import DataError

TABLE = "insider_transfer_notices"
TWSE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap12_L"
TPEX_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap12_O"

_ROC_7 = re.compile(r"^(\d{3})(\d{2})(\d{2})$")
_PERIOD = re.compile(r"^(\d{7})\s*~\s*(\d{7})$")
_CODE = re.compile(r"^[0-9A-Z]{4,6}$")

# 兩份表共用的中文鍵（櫃買把前三個換成英文鍵）
_K_ROLE_TWSE = "申報人身分"
_K_ROLE_TPEX = "申請人身分"
_K_PERSON = "姓名"
_K_METHOD = "預定轉讓方式及股數-轉讓方式"
_K_PLANNED = "預定轉讓方式及股數-轉讓股數"
_K_DAILY_MAX = "每日於盤中交易最大得轉讓股數"
_K_TRANSFEREE = "受讓人"
_K_HELD_OWN = "目前持有股數-自有持股"
_K_HELD_TRUST = "目前持有股數-保留運用決定權信託股數"
_K_AFTER_OWN = "預定轉讓後持股-自有持股"
_K_AFTER_TRUST = "預定轉讓後持股-保留運用決定權信託股數"
_K_PERIOD = "有效轉讓期間"
_REQUIRED_COMMON = (
    _K_PERSON,
    _K_METHOD,
    _K_PLANNED,
    _K_DAILY_MAX,
    _K_TRANSFEREE,
    _K_HELD_OWN,
    _K_HELD_TRUST,
    _K_AFTER_OWN,
    _K_AFTER_TRUST,
    _K_PERIOD,
)

SCHEMA: dict[str, pl.DataType] = {
    "date": pl.Date(),  # 出表日期（申報公開的那天）
    "stock_id": pl.Utf8(),
    "row_key": pl.Utf8(),  # 同一天同一檔可能多筆：身分＋姓名＋方式＋股數＋期間的 md5
    "market": pl.Utf8(),  # twse／tpex
    "company_name": pl.Utf8(),
    "role": pl.Utf8(),  # 申報人身分（董事本人、經理人本人、董事配偶…）
    "person": pl.Utf8(),
    "method": pl.Utf8(),  # 一般交易、鉅額逐筆交易、贈與、信託…
    "planned_shares": pl.Float64(),  # 預定轉讓股數（股）；表上空白時用轉讓前後持股差
    "daily_max_shares": pl.Float64(),  # 一般交易每日盤中最多可轉讓股數（股）
    "transferee": pl.Utf8(),  # 受讓人（贈與、信託才有）
    "held_own": pl.Float64(),  # 目前自有持股（股）
    "held_trust": pl.Float64(),  # 目前保留運用決定權信託股數（股）
    "after_own": pl.Float64(),
    "after_trust": pl.Float64(),
    "period_start": pl.Date(),  # 有效轉讓期間
    "period_end": pl.Date(),
    "available_at": pl.Datetime("us", "Asia/Taipei"),
}


def _roc(text: str, where: str) -> dt.date:
    m = _ROC_7.match(text.strip())
    if not m:
        raise DataError(TABLE, None, f"{where} 日期格式不認得：{text!r}")
    y, mo, d = (int(x) for x in m.groups())
    return dt.date(y + 1911, mo, d)


def _period(text: str, where: str) -> tuple[dt.date | None, dt.date | None]:
    t = text.strip()
    if t == "":
        return None, None
    m = _PERIOD.match(t)
    if not m:
        raise DataError(TABLE, None, f"{where} 有效轉讓期間格式不認得：{text!r}")
    return _roc(m.group(1), where), _roc(m.group(2), where)


def _num(raw: Any, where: str) -> float | None:
    """空字串記 None；其他必須是數字（可帶千分位），否則 raise。"""
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if text == "":
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise DataError(TABLE, None, f"{where} 不是數字：{raw!r}") from exc


def _text(raw: Any) -> str | None:
    t = str(raw if raw is not None else "").strip()
    return t or None


def _planned(row: dict[str, Any], where: str) -> float | None:
    """轉讓股數；表上空白（贈與、信託常見）→ 轉讓前持股減轉讓後持股；都算不出來 → None。"""
    direct = _num(row[_K_PLANNED], f"{where} 轉讓股數")
    if direct is not None:
        return direct
    before = [_num(row[k], f"{where} {k}") for k in (_K_HELD_OWN, _K_HELD_TRUST)]
    after = [_num(row[k], f"{where} {k}") for k in (_K_AFTER_OWN, _K_AFTER_TRUST)]
    if any(v is None for v in before + after):
        return None
    diff = sum(v for v in before if v is not None) - sum(v for v in after if v is not None)
    return diff if diff > 0 else None


def _row_key(*parts: object) -> str:
    return hashlib.md5("|".join("" if p is None else str(p) for p in parts).encode()).hexdigest()


def _parse(
    rows: list[dict[str, Any]],
    *,
    market: str,
    k_date: str,
    k_code: str,
    k_name: str,
    k_role: str,
    fetched_at: dt.datetime,
) -> list[dict[str, object]]:
    if not isinstance(rows, list):
        raise DataError(TABLE, None, f"{market} 內部人申讓回應不是陣列：{type(rows).__name__}")
    out: list[dict[str, object]] = []
    for r in rows:
        missing = [k for k in (k_date, k_code, k_name, k_role, *_REQUIRED_COMMON) if k not in r]
        if missing:
            raise DataError(TABLE, None, f"{market} 內部人申讓缺鍵 {missing}；有 {sorted(r)}")
        sid = str(r[k_code]).strip()
        if sid == "":
            # 櫃買「今天沒有申報」的占位列：除了日期全部空白
            if any(str(r[k]).strip() for k in (k_name, k_role, *_REQUIRED_COMMON)):
                raise DataError(TABLE, None, f"{market} 內部人申讓代號空白但其他欄有值：{r}")
            continue
        where = f"{market} {sid}"
        if not _CODE.match(sid):
            raise DataError(TABLE, None, f"{where} 代號不認得")
        day = _roc(str(r[k_date]), where)
        role, person, method = _text(r[k_role]), _text(r[_K_PERSON]), _text(r[_K_METHOD])
        if role is None or method is None:
            raise DataError(TABLE, None, f"{where} 申報人身分或轉讓方式空白：{r}")
        start, end = _period(str(r[_K_PERIOD]), where)
        planned = _planned(r, where)
        out.append(
            {
                "date": day,
                "stock_id": sid,
                "row_key": _row_key(role, person, method, planned, start, end),
                "market": market,
                "company_name": _text(r[k_name]),
                "role": role,
                "person": person,
                "method": method,
                "planned_shares": planned,
                "daily_max_shares": _num(r[_K_DAILY_MAX], f"{where} 每日最大"),
                "transferee": _text(r[_K_TRANSFEREE]),
                "held_own": _num(r[_K_HELD_OWN], f"{where} 自有持股"),
                "held_trust": _num(r[_K_HELD_TRUST], f"{where} 信託持股"),
                "after_own": _num(r[_K_AFTER_OWN], f"{where} 轉讓後自有"),
                "after_trust": _num(r[_K_AFTER_TRUST], f"{where} 轉讓後信託"),
                "period_start": start,
                "period_end": end,
                "available_at": fetched_at,
            }
        )
    return out


def parse_twse(rows: list[dict[str, Any]], fetched_at: dt.datetime) -> list[dict[str, object]]:
    """證交所 t187ap12_L → 列。缺鍵、代號或日期不認得 → raise。"""
    return _parse(
        rows,
        market="twse",
        k_date="出表日期",
        k_code="公司代號",
        k_name="公司名稱",
        k_role=_K_ROLE_TWSE,
        fetched_at=fetched_at,
    )


def parse_tpex(rows: list[dict[str, Any]], fetched_at: dt.datetime) -> list[dict[str, object]]:
    """櫃買 mopsfin_t187ap12_O → 列。全空占位列視為 0 筆。"""
    return _parse(
        rows,
        market="tpex",
        k_date="Date",
        k_code="SecuritiesCompanyCode",
        k_name="CompanyName",
        k_role=_K_ROLE_TPEX,
        fetched_at=fetched_at,
    )


def to_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    """列 → 與 SCHEMA 同名同序；(日期, 代號, row_key) 重複 → raise（同一人同一筆被列兩次是來源壞了）。"""
    df = pl.DataFrame(rows, schema=SCHEMA, orient="row")
    dup = df.filter(pl.struct("date", "stock_id", "row_key").is_duplicated())
    if not dup.is_empty():
        raise DataError(
            TABLE,
            None,
            f"(日期, 代號, 內容) 重複：{dup.select('date', 'stock_id', 'person').rows()[:5]}",
        )
    return df


def fetch(twse: HttpClient, tpex: HttpClient, fetched_at: dt.datetime) -> pl.DataFrame:
    """抓兩份當日申報並合成一張表。任一份 HTTP 不是 200 → raise。"""
    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at 必須帶時區")
    at = fetched_at.astimezone(TZ)
    r1 = twse.get(TWSE_URL)
    if r1.status_code != 200:
        raise DataError(TABLE, None, f"證交所內部人申讓 HTTP {r1.status_code}")
    r2 = tpex.get(TPEX_URL)
    if r2.status_code != 200:
        raise DataError(TABLE, None, f"櫃買內部人申讓 HTTP {r2.status_code}")
    return to_frame(parse_twse(json.loads(r1.text), at) + parse_tpex(json.loads(r2.text), at))
