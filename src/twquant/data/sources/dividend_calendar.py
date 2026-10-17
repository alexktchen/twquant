"""除權除息預告（2026-10-17）：首頁行事曆用的「接下來要除權息的股票」。

原本行事曆讀 `corporate_events`，但那張表來自 FinMind `TaiwanStockDividendResult`，是除權息**之後**才有的結果，
「未來三天」永遠查不到東西。改抓兩份官方預告（都列到大約三週後，含前幾天已除權息的）：

| 市場 | 端點 | 日期寫法 | 種類 |
|---|---|---|---|
| 上市 | 證交所 `rwd/zh/exRight/TWT48U`（除權除息預告表） | `115年10月20日` | 息／權／權息 |
| 上櫃 | 櫃買中心 OpenAPI `tpex_exright_prepost` | `1151020` | 除息／除權／除權息 |

「權」不一定是配股：現金增資也會除權（例：1711 永光 2026-10-20，無償配股率 0、現增配股率 0.0913、認購價 34），
所以現增配股率與認購價也存。只供首頁看，不進因子、不進模型（還原權值仍用 corporate_events）。
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

TABLE = "dividend_calendar"
TWSE_URL = "https://www.twse.com.tw/rwd/zh/exRight/TWT48U"
TPEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost"
_TWSE_FIELDS = (
    "除權除息日期",
    "股票代號",
    "名稱",
    "除權息",
    "無償配股率",
    "現金增資配股率",
    "現金增資認購價",
    "現金股利",
)
_TWSE_KIND = {"息": "cash", "權": "stock", "權息": "both"}
_TPEX_KIND = {"除息": "cash", "除權": "stock", "除權息": "both"}
_ROC_CN = re.compile(r"^(\d{2,3})年(\d{1,2})月(\d{1,2})日$")
_ROC_7 = re.compile(r"^(\d{3})(\d{2})(\d{2})$")
_CODE = re.compile(r"^[0-9A-Z]{4,6}$")
_TAG = re.compile(r"<[^>]+>")
# 金額欄的「沒有數字」寫法（2026-10-17 兩份即時回應全部列出來只有這三種）：記 None。其他看不懂的照樣 raise。
#   證交所 ETF 現金股利「待公告實際收益分配金額」（外包 <p>）、現增認購價「尚未公告」、櫃買「尚未公告」與附註「註4」
_NO_NUMBER = re.compile(r"(待|尚未)公告|^註\d+$")

SCHEMA: dict[str, pl.DataType] = {
    "date": pl.Date(),
    "stock_id": pl.Utf8(),
    "market": pl.Utf8(),
    "name": pl.Utf8(),
    "kind": pl.Utf8(),  # cash 除息／stock 除權／both 除權息
    "cash_dividend": pl.Float64(),  # 每股現金股利（元）
    "stock_ratio": pl.Float64(),  # 無償配股率（每股配幾股）
    "rights_ratio": pl.Float64(),  # 現金增資配股率
    "rights_price": pl.Float64(),  # 現金增資認購價
    "available_at": pl.Datetime("us", "Asia/Taipei"),
}


def _roc(text: str, pattern: re.Pattern[str], where: str) -> dt.date:
    m = pattern.match(text.strip())
    if not m:
        raise DataError(TABLE, None, f"{where} 日期格式不認得：{text!r}")
    y, mo, d = (int(x) for x in m.groups())
    return dt.date(y + 1911, mo, d)


def _num(raw: Any, where: str) -> float | None:
    """空字串、「待公告／尚未公告」、附註「註N」都回 None；其他必須是數字，否則 raise。"""
    if raw is None:
        return None
    text = _TAG.sub("", str(raw)).strip().replace(",", "")
    if text == "" or _NO_NUMBER.search(text):
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise DataError(TABLE, None, f"{where} 不是數字：{raw!r}") from exc


def parse_twse(payload: dict[str, Any], fetched_at: dt.datetime) -> list[dict[str, object]]:
    """證交所 TWT48U → 列。stat 不是 OK 或欄位不對 → raise。"""
    if str(payload.get("stat", "")).upper() != "OK":
        raise DataError(TABLE, None, f"證交所除權除息預告回應異常：stat={payload.get('stat')!r}")
    fields = list(payload.get("fields") or [])
    idx = {name: fields.index(name) for name in _TWSE_FIELDS if name in fields}
    missing = [n for n in _TWSE_FIELDS if n not in idx]
    if missing:
        raise DataError(TABLE, None, f"證交所除權除息預告缺欄位 {missing}；現有 {fields}")
    out: list[dict[str, object]] = []
    for r in payload.get("data") or []:
        sid = str(r[idx["股票代號"]]).strip()
        where = f"證交所 {sid}"
        kind = _TWSE_KIND.get(str(r[idx["除權息"]]).strip())
        if not _CODE.match(sid) or kind is None:
            raise DataError(TABLE, None, f"{where} 代號或種類不認得：{r[:4]}")
        out.append(
            {
                "date": _roc(str(r[idx["除權除息日期"]]), _ROC_CN, where),
                "stock_id": sid,
                "market": "TWSE",
                "name": str(r[idx["名稱"]]).strip() or None,
                "kind": kind,
                "cash_dividend": _num(r[idx["現金股利"]], f"{where} 現金股利"),
                "stock_ratio": _num(r[idx["無償配股率"]], f"{where} 無償配股率"),
                "rights_ratio": _num(r[idx["現金增資配股率"]], f"{where} 現增配股率"),
                "rights_price": _num(r[idx["現金增資認購價"]], f"{where} 認購價"),
                "available_at": fetched_at,
            }
        )
    return out


def parse_tpex(rows: list[dict[str, Any]], fetched_at: dt.datetime) -> list[dict[str, object]]:
    """櫃買 OpenAPI tpex_exright_prepost → 列。缺鍵或種類不認得 → raise。"""
    if not isinstance(rows, list):
        raise DataError(TABLE, None, f"櫃買除權除息預告回應不是陣列：{type(rows).__name__}")
    out: list[dict[str, object]] = []
    for r in rows:
        try:
            sid = str(r["SecuritiesCompanyCode"]).strip()
            kind_raw = str(r["ExRrightsExDividend"]).strip()
            date_raw = str(r["ExRrightsExDividendDate"])
        except KeyError as exc:
            raise DataError(TABLE, None, f"櫃買除權除息預告缺鍵 {exc}；有 {sorted(r)}") from exc
        where = f"櫃買 {sid}"
        kind = _TPEX_KIND.get(kind_raw)
        if not _CODE.match(sid) or kind is None:
            raise DataError(TABLE, None, f"{where} 代號或種類不認得：{sid!r} {kind_raw!r}")
        out.append(
            {
                "date": _roc(date_raw, _ROC_7, where),
                "stock_id": sid,
                "market": "TPEX",
                "name": str(r.get("CompanyName") or "").strip() or None,
                "kind": kind,
                "cash_dividend": _num(r.get("CashDividend"), f"{where} 現金股利"),
                "stock_ratio": _num(r.get("StockDividendRatio"), f"{where} 無償配股率"),
                "rights_ratio": _num(
                    r.get("SubscriptionRatioToNewSharesIssued"), f"{where} 現增配股率"
                ),
                "rights_price": _num(r.get("SubscriptionPricePerShare"), f"{where} 認購價"),
                "available_at": fetched_at,
            }
        )
    return out


def to_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    """列 → 與 SCHEMA 同名同序；(日期, 代號) 重複 → raise。"""
    df = pl.DataFrame(rows, schema=SCHEMA, orient="row")
    dup = df.filter(pl.struct("date", "stock_id").is_duplicated())
    if not dup.is_empty():
        raise DataError(
            TABLE, None, f"(日期, 代號) 重複：{dup.select('date', 'stock_id').rows()[:5]}"
        )
    return df


def fetch(twse: HttpClient, tpex: HttpClient, fetched_at: dt.datetime) -> pl.DataFrame:
    """抓兩份預告並合成一張表。任一份 HTTP 不是 200 → raise。"""
    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at 必須帶時區")
    at = fetched_at.astimezone(TZ)
    r1 = twse.get(TWSE_URL, params={"response": "json"})
    if r1.status_code != 200:
        raise DataError(TABLE, None, f"證交所除權除息預告 HTTP {r1.status_code}")
    r2 = tpex.get(TPEX_URL)
    if r2.status_code != 200:
        raise DataError(TABLE, None, f"櫃買除權除息預告 HTTP {r2.status_code}")
    return to_frame(parse_twse(json.loads(r1.text), at) + parse_tpex(json.loads(r2.text), at))
