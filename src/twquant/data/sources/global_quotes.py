"""晨報用的全球行情快照（T-377／D-188）：Yahoo Finance 日線（美股指數、VIX、美債、匯率、台積電 ADR）與期交所臺指期夜盤。

與網站 `frontend/functions/_lib/yahoo.ts`／`taifex.ts` 讀同兩個介面、同一套規則（最新一根對前一根；
不用 chartPreviousClose；期交所 "0.00" 當沒有），這裡是 Python 版，給早上 06:30 的晨報用。
兩個都不是正式開放的介面：格式不對就 raise，呼叫端決定要不要降級。純解析函數可離線測。
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from twquant.conventions import TZ
from twquant.data.http import HttpClient
from twquant.errors import DataError

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
TAIFEX_URL = "https://mis.taifex.com.tw/futures/api/getQuoteList"
_SPOT_RE = re.compile(r"^TXF-[SP]$")
_CONTRACT_RE = re.compile(r"^TXF[A-L]\d-[FM]$")


@dataclass(frozen=True)
class Quote:
    """One instrument's latest daily bar versus the previous one."""

    symbol: str
    name: str
    date: str  # 最新一根的當地日期 YYYY-MM-DD
    last: float
    prev: float
    chg: float  # last / prev − 1
    open: bool  # 還在交易中（數字是盤中的）
    time: str | None  # 最後更新，台北 HH:MM


@dataclass(frozen=True)
class NightFuture:
    """臺指期夜盤近月：最後成交、對當天日盤結算價的漲跌、對加權收盤的價差。"""

    symbol: str
    date: str  # 期交所標的交易日
    time: str | None
    last: float | None
    ref: float | None
    chg: float | None
    spot_close: float | None  # 夜盤那份的「參考價」＝當天加權收盤
    gap_pts: float | None  # last − spot_close
    volume: int | None


def yahoo_url(symbol: str) -> str:
    from urllib.parse import quote

    return f"{YAHOO_URL}{quote(symbol, safe='')}?range=5d&interval=1d"


def parse_yahoo(body: Any, symbol: str, name: str) -> Quote:
    """Yahoo chart 回應 → 最新一根對前一根；收盤為 null 的那根跳過；不到兩根 → raise。"""
    try:
        r = body["chart"]["result"][0]
        meta = r["meta"]
    except (KeyError, IndexError, TypeError) as exc:
        why = body.get("chart", {}).get("error") if isinstance(body, dict) else None
        raise DataError("global_quotes", None, f"Yahoo {symbol} 回應異常：{why!r}") from exc
    ts = r.get("timestamp") or []
    closes = (r.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    bars = [
        (t, c) for t, c in zip(ts, closes, strict=False) if isinstance(c, (int, float)) and c > 0
    ]
    if len(bars) < 2:
        raise DataError("global_quotes", None, f"Yahoo {symbol} 日線不到兩根")
    tz = ZoneInfo(meta.get("exchangeTimezoneName") or "America/New_York")
    (t_last, last), (_, prev) = bars[-1], bars[-2]
    at = meta.get("regularMarketTime")
    reg = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    start, end = reg.get("start"), reg.get("end")
    is_open = (
        isinstance(at, (int, float))
        and isinstance(start, (int, float))
        and isinstance(end, (int, float))
        and start <= at < end
    )
    return Quote(
        symbol=str(meta.get("symbol") or symbol),
        name=name,
        date=dt.datetime.fromtimestamp(t_last, tz).strftime("%Y-%m-%d"),
        last=float(last),
        prev=float(prev),
        chg=float(last) / float(prev) - 1,
        open=bool(is_open),
        time=dt.datetime.fromtimestamp(at, TZ).strftime("%H:%M")
        if isinstance(at, (int, float))
        else None,
    )


def fetch_yahoo(client: HttpClient, symbol: str, name: str) -> Quote:
    resp = client.get(yahoo_url(symbol))
    if resp.status_code != 200:
        raise DataError("global_quotes", None, f"Yahoo {symbol} HTTP {resp.status_code}")
    return parse_yahoo(json.loads(resp.text), symbol, name)


def taifex_body(session: str) -> dict[str, str]:
    return {
        "MarketType": "1" if session == "night" else "0",
        "SymbolType": "F",
        "KindID": "1",
        "CID": "TXF",
        "ExpireMonth": "",
        "RowSize": "全部",
        "PageNo": "",
        "SortColumn": "",
        "AscDesc": "A",
    }


def _price(v: Any) -> float | None:
    if not isinstance(v, str) or v == "":
        return None
    try:
        n = float(v)
    except ValueError:
        return None
    return n if n > 0 else None


def _ymd(v: Any) -> str | None:
    return (
        f"{v[:4]}-{v[4:6]}-{v[6:8]}" if isinstance(v, str) and re.fullmatch(r"\d{8}", v) else None
    )


def _hms(v: Any) -> str | None:
    return (
        f"{v[:2]}:{v[2:4]}:{v[4:6]}" if isinstance(v, str) and re.fullmatch(r"\d{6}", v) else None
    )


def parse_taifex_night(body: Any) -> NightFuture:
    """期交所 getQuoteList（夜盤）→ 近月合約＋當天加權收盤（現貨列的參考價）。RtCode 不是 "0" → raise。"""
    if (
        not isinstance(body, dict)
        or body.get("RtCode") != "0"
        or not isinstance((body.get("RtData") or {}).get("QuoteList"), list)
    ):
        raise DataError(
            "global_quotes",
            None,
            f"期交所行情回應異常（RtCode {body.get('RtCode') if isinstance(body, dict) else '?'}）",
        )
    spot_close: float | None = None
    near: NightFuture | None = None
    for raw in body["RtData"]["QuoteList"]:
        sid = raw.get("SymbolID")
        date = _ymd(raw.get("CDate"))
        if not isinstance(sid, str) or date is None:
            continue
        if spot_close is None and _SPOT_RE.match(sid):
            spot_close = _price(raw.get("CRefPrice"))
        elif near is None and _CONTRACT_RE.match(sid):
            last, ref = _price(raw.get("CLastPrice")), _price(raw.get("CRefPrice"))
            vol = raw.get("CTotalVolume")
            near = NightFuture(
                symbol=sid.split("-")[0],
                date=date,
                time=_hms(raw.get("CTime")),
                last=last,
                ref=ref,
                chg=last / ref - 1 if last is not None and ref is not None else None,
                spot_close=None,
                gap_pts=None,
                volume=int(vol) if isinstance(vol, str) and vol.isdigit() else None,
            )
    if near is None:
        raise DataError("global_quotes", None, "期交所夜盤沒有近月合約")
    gap = near.last - spot_close if near.last is not None and spot_close is not None else None
    return NightFuture(**{**near.__dict__, "spot_close": spot_close, "gap_pts": gap})


def fetch_taifex_night(post_json: Any) -> NightFuture:
    """`post_json(url, payload) -> dict`：由呼叫端注入（HttpClient 只有 GET）。"""
    return parse_taifex_night(post_json(TAIFEX_URL, taifex_body("night")))
