"""權證條款與日行情（T-385，2026-10-18；D-191 方向 4）。

| 什麼 | 上市 | 上櫃 |
|---|---|---|
| 條款（存續中全表、每天同一份） | 證交所 OpenAPI `opendata/t187ap37_L` | 櫃買 OpenAPI `mopsfin_t187ap37_O` |
| 日行情 | 證交所 `MI_INDEX?type=0999`（認購，不含牛證）＋`type=0999P`（認售，不含熊證） | 櫃買 OpenAPI `tpex_warrant_daily_quts`（**只給最新一天**，認購認售都在） |

- 條款表沒有已到期權證的歷史（2026-09-28 查過，D-167 §三），所以從今天起累積；條款只有變了才多存一版（`valid_from`）。
- 條款的「標的證券/指數」是**名稱**；日行情才有標的代號。摘要用日行情的代號，不用名稱對代號。
- 成交量單位：兩市場都是「單位」（2026-10-16 實測 成交金額 ÷（收盤 × 成交量）中位數 = 1.0）。
- 「最新標的履約配發數量(每仟單位權證)」÷ 1000 ＝ 一單位權證對應幾股標的（`ratio`）。
- 證交所休市日 MI_INDEX 回 stat OK 但 0 列；櫃買只回最新一天，日期不等於要的那天 → raise（不能回補）。
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

import polars as pl

from twquant.conventions import TZ
from twquant.data.http import HttpClient
from twquant.errors import DataError

TERMS_TABLE = "warrant_terms"
DAILY_TABLE = "warrant_daily"
TWSE_TERMS_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap37_L"
TPEX_TERMS_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap37_O"
TWSE_DAILY_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TPEX_DAILY_URL = "https://www.tpex.org.tw/openapi/v1/tpex_warrant_daily_quts"
TWSE_DAILY_TYPES = {"0999": "call", "0999P": "put"}

_ROC_7 = re.compile(r"^(\d{3})(\d{2})(\d{2})$")
_TAG = re.compile(r"<[^>]+>")
_KIND = {"認購": "call", "認售": "put"}
_K_TERMS = (
    "出表日期",
    "權證代號",
    "權證簡稱",
    "權證類型",
    "類別",
    "履約開始日",
    "最後交易日",
    "履約截止日",
    "發行單位數量(仟單位)",
    "結算方式(詳附註編號說明)",
    "標的證券/指數",
    "最新標的履約配發數量(每仟單位權證)",
    "最新履約價格(元)/履約指數",
    "最新上限價格(元)/上限指數",
    "最新下限價格(元)/下限指數",
)
_TWSE_DAILY_FIELDS = (
    "證券代號",
    "成交股數",
    "成交筆數",
    "成交金額",
    "收盤價",
    "最後揭示買價",
    "最後揭示賣價",
    "標的代號",
    "標的收盤價/指數",
)
_K_TPEX_DAILY = (
    "Date",
    "Code",
    "Name",
    "Close",
    "TradeVol.",
    "No.OfTransactions",
    "TradeValue",
    "UnderlyingStockCode",
    "UnderlyingStockClosePrice",
)

TERMS_SCHEMA: dict[str, pl.DataType] = {
    "valid_from": pl.Date(),  # 這一版條款第一次被看到的日子
    "warrant_id": pl.Utf8(),
    "market": pl.Utf8(),  # twse／tpex
    "underlying_id": pl.Utf8(),  # 條款表沒有代號；留 null，摘要用日行情的代號
    "underlying_name": pl.Utf8(),
    "kind": pl.Utf8(),  # call／put
    "style": pl.Utf8(),  # 結算方式代碼（原文）
    "category": pl.Utf8(),  # 類別：一般型、重設型…
    "expiry_date": pl.Date(),  # 履約截止日
    "last_trade_date": pl.Date(),
    "strike": pl.Float64(),  # 最新履約價
    "ratio": pl.Float64(),  # 一單位權證對應幾股標的（配發數量／1000）
    "cap": pl.Float64(),  # 最新上限價（0＝沒有）
    "floor": pl.Float64(),
    "issued_k_units": pl.Float64(),
    "available_at": pl.Datetime("us", "Asia/Taipei"),
}
# 這些欄位任一變了就算新版本
TERMS_VERSION_FIELDS = (
    "kind",
    "category",
    "expiry_date",
    "last_trade_date",
    "strike",
    "ratio",
    "cap",
    "floor",
    "issued_k_units",
)

DAILY_SCHEMA: dict[str, pl.DataType] = {
    "date": pl.Date(),
    "warrant_id": pl.Utf8(),
    "market": pl.Utf8(),
    "underlying_id": pl.Utf8(),  # 四碌股票、ETF 代號或指數代碼（IX0001）
    "kind": pl.Utf8(),
    "close": pl.Float64(),  # 沒成交是 null
    "volume_units": pl.Float64(),  # 單位
    "trade_value": pl.Float64(),  # 元
    "trades": pl.Float64(),
    "last_bid": pl.Float64(),
    "last_ask": pl.Float64(),
    "underlying_close": pl.Float64(),
    "available_at": pl.Datetime("us", "Asia/Taipei"),
}


def _roc(text: str, where: str) -> dt.date:
    m = _ROC_7.match(text.strip())
    if not m:
        raise DataError(TERMS_TABLE, None, f"{where} 日期格式不認得：{text!r}")
    y, mo, d = (int(x) for x in m.groups())
    return dt.date(y + 1911, mo, d)


def _num(raw: Any, where: str, *, table: str) -> float | None:
    """空白、「--」記 None；其他必須是數字（可帶千分位、HTML 標籤），否則 raise。"""
    if raw is None:
        return None
    text = _TAG.sub("", str(raw)).strip().replace(",", "")
    if text in ("", "--"):
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise DataError(table, None, f"{where} 不是數字：{raw!r}") from exc


def _need(row: dict[str, Any], keys: tuple[str, ...], where: str, table: str) -> None:
    missing = [k for k in keys if k not in row]
    if missing:
        raise DataError(table, None, f"{where} 缺鍵 {missing}；有 {sorted(row)}")


def parse_terms(
    rows: list[dict[str, Any]], *, market: str, fetched_at: dt.datetime
) -> list[dict[str, object]]:
    """t187ap37（兩市場同鍵名）→ 條款列；valid_from 由呼叫端比對後決定，這裡先填出表日。"""
    if not isinstance(rows, list):
        raise DataError(TERMS_TABLE, None, f"{market} 權證條款回應不是陣列：{type(rows).__name__}")
    out: list[dict[str, object]] = []
    for r in rows:
        _need(r, _K_TERMS, f"{market} 權證條款", TERMS_TABLE)
        wid = str(r["權證代號"]).strip()
        where = f"{market} {wid}"
        kind = _KIND.get(str(r["權證類型"]).strip())
        if not wid or kind is None:
            raise DataError(TERMS_TABLE, None, f"{where} 代號或權證類型不認得：{r['權證類型']!r}")
        per_k = _num(
            r["最新標的履約配發數量(每仟單位權證)"], f"{where} 配發數量", table=TERMS_TABLE
        )
        strike = _num(r["最新履約價格(元)/履約指數"], f"{where} 履約價", table=TERMS_TABLE)
        if per_k is None or strike is None:
            raise DataError(TERMS_TABLE, None, f"{where} 配發數量或履約價空白")
        out.append(
            {
                "valid_from": _roc(str(r["出表日期"]), where),
                "warrant_id": wid,
                "market": market,
                "underlying_id": None,
                "underlying_name": str(r["標的證券/指數"]).strip() or None,
                "kind": kind,
                "style": str(r["結算方式(詳附註編號說明)"]).strip() or None,
                "category": str(r["類別"]).strip() or None,
                "expiry_date": _roc(str(r["履約截止日"]), where),
                "last_trade_date": _roc(str(r["最後交易日"]), where),
                "strike": strike,
                "ratio": per_k / 1000.0,
                "cap": _num(r["最新上限價格(元)/上限指數"], f"{where} 上限價", table=TERMS_TABLE),
                "floor": _num(r["最新下限價格(元)/下限指數"], f"{where} 下限價", table=TERMS_TABLE),
                "issued_k_units": _num(
                    r["發行單位數量(仟單位)"], f"{where} 發行量", table=TERMS_TABLE
                ),
                "available_at": fetched_at,
            }
        )
    return out


def terms_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    """條款列 → DataFrame；同一代號重複 → raise。"""
    df = pl.DataFrame(rows, schema=TERMS_SCHEMA, orient="row")
    dup = df.filter(pl.col("warrant_id").is_duplicated())
    if not dup.is_empty():
        raise DataError(
            TERMS_TABLE, None, f"權證代號重複：{dup['warrant_id'].unique().to_list()[:5]}"
        )
    return df


def new_versions(fresh: pl.DataFrame, latest: pl.DataFrame, day: dt.date) -> pl.DataFrame:
    """今天抓到的條款 vs PG 裡每檔最新一版：沒看過的或關鍵欄位變了的 → 新版本（valid_from=day）。

    latest 欄位同 TERMS_SCHEMA（每檔一列、最新 valid_from）。沒有 latest（表空）→ 全部都是新版本。
    """
    fresh = fresh.with_columns(pl.lit(day).alias("valid_from"))
    if latest.is_empty():
        return fresh
    key = list(TERMS_VERSION_FIELDS)
    old = latest.select("warrant_id", *key).rename({k: f"{k}_old" for k in key})
    j = fresh.join(old, on="warrant_id", how="left")
    changed = pl.col(f"{key[0]}_old").is_null()
    for k in key:
        a, b = pl.col(k), pl.col(f"{k}_old")
        changed = changed | (a.is_null() != b.is_null()) | (a != b).fill_null(False)
    return j.filter(changed).select(list(TERMS_SCHEMA))


def fetch_terms(twse: HttpClient, tpex: HttpClient, fetched_at: dt.datetime) -> pl.DataFrame:
    """兩市場存續中條款合成一張表（valid_from 暫填出表日，呼叫端再用 new_versions 決定）。"""
    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at 必須帶時區")
    at = fetched_at.astimezone(TZ)
    r1 = twse.get(TWSE_TERMS_URL)
    if r1.status_code != 200:
        raise DataError(TERMS_TABLE, None, f"證交所權證條款 HTTP {r1.status_code}")
    r2 = tpex.get(TPEX_TERMS_URL)
    if r2.status_code != 200:
        raise DataError(TERMS_TABLE, None, f"櫃買權證條款 HTTP {r2.status_code}")
    return terms_frame(
        parse_terms(json.loads(r1.text), market="twse", fetched_at=at)
        + parse_terms(json.loads(r2.text), market="tpex", fetched_at=at)
    )


def parse_twse_daily(
    payload: dict[str, Any], *, kind: str, day: dt.date, fetched_at: dt.datetime
) -> list[dict[str, object]]:
    """MI_INDEX 0999／0999P → 行情列。stat 不是 OK → raise；沒有資料表（休市）→ []。"""
    if str(payload.get("stat", "")).upper() != "OK":
        raise DataError(DAILY_TABLE, day, f"證交所權證行情回應異常：stat={payload.get('stat')!r}")
    tables = [t for t in payload.get("tables") or [] if t.get("data")]
    if not tables:
        return []
    out: list[dict[str, object]] = []
    for t in tables:
        fields = list(t.get("fields") or [])
        idx = {name: fields.index(name) for name in _TWSE_DAILY_FIELDS if name in fields}
        missing = [n for n in _TWSE_DAILY_FIELDS if n not in idx]
        if missing:
            raise DataError(DAILY_TABLE, day, f"證交所權證行情缺欄位 {missing}；現有 {fields}")
        for r in t["data"]:
            wid = str(r[idx["證券代號"]]).strip()
            where = f"證交所 {wid}"
            out.append(
                {
                    "date": day,
                    "warrant_id": wid,
                    "market": "twse",
                    "underlying_id": str(r[idx["標的代號"]]).strip() or None,
                    "kind": kind,
                    "close": _num(r[idx["收盤價"]], f"{where} 收盤", table=DAILY_TABLE),
                    "volume_units": _num(r[idx["成交股數"]], f"{where} 成交量", table=DAILY_TABLE),
                    "trade_value": _num(r[idx["成交金額"]], f"{where} 成交金額", table=DAILY_TABLE),
                    "trades": _num(r[idx["成交筆數"]], f"{where} 成交筆數", table=DAILY_TABLE),
                    "last_bid": _num(r[idx["最後揭示買價"]], f"{where} 買價", table=DAILY_TABLE),
                    "last_ask": _num(r[idx["最後揭示賣價"]], f"{where} 賣價", table=DAILY_TABLE),
                    "underlying_close": _num(
                        r[idx["標的收盤價/指數"]], f"{where} 標的收盤", table=DAILY_TABLE
                    ),
                    "available_at": fetched_at,
                }
            )
    return out


def parse_tpex_daily(
    rows: list[dict[str, Any]], *, day: dt.date, fetched_at: dt.datetime
) -> list[dict[str, object]]:
    """tpex_warrant_daily_quts（只給最新一天）→ 行情列。資料日期 ≠ day → raise（回補不可能）。空陣列 → []。"""
    if not isinstance(rows, list):
        raise DataError(DAILY_TABLE, day, f"櫃買權證行情回應不是陣列：{type(rows).__name__}")
    out: list[dict[str, object]] = []
    for r in rows:
        _need(r, _K_TPEX_DAILY, "櫃買權證行情", DAILY_TABLE)
        got = _roc(str(r["Date"]), "櫃買權證行情")
        if got != day:
            raise DataError(DAILY_TABLE, day, f"櫃買權證行情只給最新一天 {got}，不是要的 {day}")
        wid = str(r["Code"]).strip()
        name = str(r["Name"])
        kind = "put" if "售" in name else "call" if "購" in name else None
        if not wid or kind is None:
            raise DataError(DAILY_TABLE, day, f"櫃買 {wid} 名稱分不出認購認售：{name!r}")
        where = f"櫃買 {wid}"
        out.append(
            {
                "date": day,
                "warrant_id": wid,
                "market": "tpex",
                "underlying_id": str(r["UnderlyingStockCode"]).strip() or None,
                "kind": kind,
                "close": _num(r["Close"], f"{where} 收盤", table=DAILY_TABLE),
                "volume_units": _num(r["TradeVol."], f"{where} 成交量", table=DAILY_TABLE),
                "trade_value": _num(r["TradeValue"], f"{where} 成交金額", table=DAILY_TABLE),
                "trades": _num(r["No.OfTransactions"], f"{where} 成交筆數", table=DAILY_TABLE),
                "last_bid": None,  # 櫃買這份沒有最後買賣價
                "last_ask": None,
                "underlying_close": _num(
                    r["UnderlyingStockClosePrice"], f"{where} 標的收盤", table=DAILY_TABLE
                ),
                "available_at": fetched_at,
            }
        )
    return out


def daily_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    """行情列 → DataFrame；(日期, 代號) 重複 → raise。"""
    df = pl.DataFrame(rows, schema=DAILY_SCHEMA, orient="row")
    dup = df.filter(pl.struct("date", "warrant_id").is_duplicated())
    if not dup.is_empty():
        raise DataError(
            DAILY_TABLE, None, f"(日期, 權證代號) 重複：{dup['warrant_id'].unique().to_list()[:5]}"
        )
    return df


def fetch_daily(
    twse: HttpClient, tpex: HttpClient, day: dt.date, fetched_at: dt.datetime
) -> pl.DataFrame:
    """day 的兩市場權證行情。證交所兩個 type 都 0 列（休市）→ 空表、不問櫃買。"""
    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at 必須帶時區")
    at = fetched_at.astimezone(TZ)
    rows: list[dict[str, object]] = []
    for type_code, kind in TWSE_DAILY_TYPES.items():
        r = twse.get(
            TWSE_DAILY_URL,
            params={"date": day.strftime("%Y%m%d"), "type": type_code, "response": "json"},
        )
        if r.status_code != 200:
            raise DataError(DAILY_TABLE, day, f"證交所權證行情 {type_code} HTTP {r.status_code}")
        rows += parse_twse_daily(json.loads(r.text), kind=kind, day=day, fetched_at=at)
    if not rows:
        return daily_frame([])
    r2 = tpex.get(TPEX_DAILY_URL)
    if r2.status_code != 200:
        raise DataError(DAILY_TABLE, day, f"櫃買權證行情 HTTP {r2.status_code}")
    rows += parse_tpex_daily(json.loads(r2.text), day=day, fetched_at=at)
    return daily_frame(rows)
