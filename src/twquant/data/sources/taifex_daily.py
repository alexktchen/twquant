"""期交所盤後統計（T-375／D-188）：三大法人臺股期貨未平倉、選擇權買賣權比。

兩個下載端點都吃日期區間、回 Big5 的 CSV：

| 端點 | 內容 | 一列＝ |
|---|---|---|
| `cht/3/futContractsDateDown`（commodityId=TXF） | 三大法人（自營商／投信／外資及陸資）臺股期貨交易與未平倉口數、契約金額 | 一天 × 一個身份別 |
| `cht/3/pcRatioDown` | 選擇權賣權／買權成交量與未平倉量、比率% | 一天 |

**錯誤不是用 HTTP 狀態碼回，而是一頁 UTF-8 的 HTML：`alert("…")` 後導回查詢頁**（2026-10-17 實測）：

- `查無資料`：那段期間沒有交易（週末、假日）→ 當成「沒有列」。
- `日期時間錯誤 DateTime error`：**三大法人期貨**的迄日超過最新公布日（例：週六查到當天）就整個拒絕，連前面有資料的日子也不給 →
  `fetch_range` 把迄日往前退一天重查（買賣權比那個端點不會這樣）。
- 其他任何 alert 或非 CSV → raise。第一版把所有 alert 都當「查無資料」，迄日落在週六時外資期貨整欄被靜默留空，就是這樣抓到的。

`available_at`＝盤後公布（system.yaml `taifex_daily.available_time`，期交所約 15:00 後陸續更新）。
只供戰情室看，不進因子、不進模型。
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re

import polars as pl

from twquant.conventions import TZ
from twquant.data.http import HttpClient
from twquant.errors import DataError

TABLE = "taifex_daily"
FUT_URL = "https://www.taifex.com.tw/cht/3/futContractsDateDown"
PC_URL = "https://www.taifex.com.tw/cht/3/pcRatioDown"
COMMODITY = "TXF"
_PRODUCT = "臺股期貨"
_ALERT = re.compile(r'alert\("([^"]*)"\)')
_NO_DATA = "查無資料"
_DATE_ERROR = "DateTime error"
_FUT_HEADER = "日期,商品名稱,身份別"
_PC_HEADER = "日期,賣權成交量,買權成交量"
_ROLES = {"自營商": "dealer", "投信": "trust", "外資及陸資": "foreign", "外資": "foreign"}

SCHEMA: dict[str, pl.DataType] = {
    "date": pl.Date(),
    "fut_dealer_net_oi": pl.Int64(),
    "fut_trust_net_oi": pl.Int64(),
    "fut_foreign_net_oi": pl.Int64(),
    "fut_foreign_long_oi": pl.Int64(),
    "fut_foreign_short_oi": pl.Int64(),
    "pc_vol_ratio": pl.Float64(),  # 買賣權成交量比率（%）
    "pc_oi_ratio": pl.Float64(),  # 買賣權未平倉量比率（%）
    "put_oi": pl.Int64(),
    "call_oi": pl.Int64(),
    "available_at": pl.Datetime("us", "Asia/Taipei"),
}


class TaifexDateError(DataError):
    """迄日超過期交所最新公布日（三大法人期貨會整段拒絕）。"""


def _is_html(raw: bytes) -> bool:
    head = raw[:600].lstrip().lower()
    return head.startswith(b"<") or b"<html" in head or b"alert(" in raw[:2000]


def _text(raw: bytes) -> str:
    """CSV 是 Big5；錯誤頁是 UTF-8（2026-10-17 錄）。"""
    return raw.decode("utf-8", errors="replace") if _is_html(raw) else raw.decode("cp950")


def _date(text: str, what: str) -> dt.date:
    try:
        y, m, d = (int(x) for x in text.strip().split("/"))
        return dt.date(y, m, d)
    except ValueError as exc:
        raise DataError(TABLE, None, f"{what} 的日期不是 yyyy/mm/dd：{text!r}") from exc


def _int(text: str, what: str, day: dt.date) -> int:
    try:
        return int(text.strip().replace(",", ""))
    except ValueError as exc:
        raise DataError(TABLE, day, f"{what} 不是整數：{text!r}") from exc


def _float(text: str, what: str, day: dt.date) -> float:
    try:
        return float(text.strip().replace(",", ""))
    except ValueError as exc:
        raise DataError(TABLE, day, f"{what} 不是數字：{text!r}") from exc


def _rows(raw: bytes, header: str, what: str) -> list[list[str]] | None:
    """CSV 的資料列；查無資料頁 → None；其他非 CSV → raise。"""
    text = _text(raw)
    if not text.lstrip().startswith(header):
        alert = _ALERT.search(text)
        message = alert.group(1) if alert else None
        if message is not None and _NO_DATA in message:
            return None
        if message is not None and _DATE_ERROR in message:
            raise TaifexDateError(TABLE, None, f"期交所 {what}：{message}")
        shown = message if message is not None else text[:80]
        raise DataError(TABLE, None, f"期交所 {what} 回應不是 CSV（{shown!r}）")
    rows = [r for r in csv.reader(io.StringIO(text)) if r and r[0].strip()]
    return rows[1:]


def parse_fut_oi(raw: bytes) -> dict[dt.date, dict[str, int]]:
    """三大法人臺股期貨 → {日期: {dealer/trust/foreign 的未平倉淨額、外資多空口數}}。"""
    rows = _rows(raw, _FUT_HEADER, "三大法人期貨")
    out: dict[dt.date, dict[str, int]] = {}
    for r in rows or []:
        if len(r) < 14:
            raise DataError(TABLE, None, f"三大法人期貨列欄位不足 14：{r}")
        if r[1].strip() != _PRODUCT:
            continue
        day = _date(r[0], "三大法人期貨")
        role = _ROLES.get(r[2].strip())
        if role is None:
            raise DataError(TABLE, day, f"不認得的身份別：{r[2]!r}")
        rec = out.setdefault(day, {})
        rec[f"{role}_net"] = _int(r[13], f"{role} 多空未平倉口數淨額", day)
        if role == "foreign":
            rec["foreign_long"] = _int(r[9], "外資多方未平倉口數", day)
            rec["foreign_short"] = _int(r[11], "外資空方未平倉口數", day)
    for day, rec in out.items():
        missing = [k for k in ("dealer_net", "trust_net", "foreign_net") if k not in rec]
        if missing:
            raise DataError(TABLE, day, f"三大法人期貨缺身份別：{missing}")
    return out


def parse_pc_ratio(raw: bytes) -> dict[dt.date, dict[str, float]]:
    """選擇權買賣權比 → {日期: {pc_vol_ratio, pc_oi_ratio, put_oi, call_oi}}。"""
    rows = _rows(raw, _PC_HEADER, "買賣權比")
    out: dict[dt.date, dict[str, float]] = {}
    for r in rows or []:
        if len(r) < 7:
            raise DataError(TABLE, None, f"買賣權比列欄位不足 7：{r}")
        day = _date(r[0], "買賣權比")
        out[day] = {
            "pc_vol_ratio": _float(r[3], "買賣權成交量比率", day),
            "put_oi": float(_int(r[4], "賣權未平倉量", day)),
            "call_oi": float(_int(r[5], "買權未平倉量", day)),
            "pc_oi_ratio": _float(r[6], "買賣權未平倉量比率", day),
        }
    return out


def combine(
    fut: dict[dt.date, dict[str, int]], pc: dict[dt.date, dict[str, float]], available_time: dt.time
) -> pl.DataFrame:
    """兩份依日期合成一列；只在一邊有的日子，另一邊留空（不補 0）。"""
    days = sorted(set(fut) | set(pc))
    rows = []
    for day in days:
        f = fut.get(day, {})
        p = pc.get(day, {})
        rows.append(
            {
                "date": day,
                "fut_dealer_net_oi": f.get("dealer_net"),
                "fut_trust_net_oi": f.get("trust_net"),
                "fut_foreign_net_oi": f.get("foreign_net"),
                "fut_foreign_long_oi": f.get("foreign_long"),
                "fut_foreign_short_oi": f.get("foreign_short"),
                "pc_vol_ratio": p.get("pc_vol_ratio"),
                "pc_oi_ratio": p.get("pc_oi_ratio"),
                "put_oi": int(p["put_oi"]) if "put_oi" in p else None,
                "call_oi": int(p["call_oi"]) if "call_oi" in p else None,
                "available_at": dt.datetime.combine(day, available_time, tzinfo=TZ),
            }
        )
    return pl.DataFrame(rows, schema=SCHEMA, orient="row")


def _range(start: dt.date, end: dt.date) -> dict[str, str]:
    return {"queryStartDate": start.strftime("%Y/%m/%d"), "queryEndDate": end.strftime("%Y/%m/%d")}


def _get_fut(
    client: HttpClient, start: dt.date, end: dt.date
) -> tuple[dict[dt.date, dict[str, int]], dt.date]:
    """三大法人期貨：迄日超過最新公布日就往前退一天重查，退到起日還不行 → raise。回 (資料, 實際迄日)。"""
    cur = end
    while True:
        resp = client.get(FUT_URL, params={**_range(start, cur), "commodityId": COMMODITY})
        if resp.status_code != 200:
            raise DataError(TABLE, None, f"期交所三大法人期貨 HTTP {resp.status_code}")
        try:
            return parse_fut_oi(resp.content), cur
        except TaifexDateError:
            if cur <= start:
                raise
            cur -= dt.timedelta(days=1)


def fetch_range(
    client: HttpClient, start: dt.date, end: dt.date, available_time: dt.time
) -> pl.DataFrame:
    """抓 [start, end] 的兩份統計並合成；任一份 HTTP 不是 200 → raise。"""
    fut, _ = _get_fut(client, start, end)
    r2 = client.get(PC_URL, params=_range(start, end))
    if r2.status_code != 200:
        raise DataError(TABLE, None, f"期交所買賣權比 HTTP {r2.status_code}")
    return combine(fut, parse_pc_ratio(r2.content), available_time)
