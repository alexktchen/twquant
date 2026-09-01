"""FinMind 大量歷史回補（T-010、D-015、D-044）。

為什麼不是 `Source` 協定：`Source.fetch(table, date)` 是**逐日**存取，而 FinMind 的
存取單位是**逐檔全期**（一次請求即回該檔數年份）。硬套協定會退化成每日一請求，
失去它相對官方端點的唯一優勢。故本模組走 D-014 預留的「專屬回補指令」路徑。

為什麼需要它：D-015 實測官方 TWSE rwd 端點在累積約 390 請求後即封鎖 IP，
即使限流在 3 req/5s 內；2026-09-02 重測仍然如此（第 41 天起全部回 HTTP 428/307）。

映射已對既有 TWSE 資料逐位元驗證（2026-08-28 全市場抽樣 25/25 全等）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from typing import Any

from twquant.errors import DataError

API = "https://api.finmindtrade.com/api/v4/data"

INSTITUTIONAL_DATASET = "TaiwanStockInstitutionalInvestorsBuySell"
MARGIN_DATASET = "TaiwanStockMarginPurchaseShortSale"

# clean.py:59 的定義：外資＝外陸資(不含自營)＋外資自營商；自營＝自行買賣＋避險。
# 未列出的類別一律 raise——來源新增類別而靜默忽略，會讓某一欄悄悄少一條腿。
_FOREIGN = ("Foreign_Investor", "Foreign_Dealer_Self")
_TRUST = ("Investment_Trust",)
_DEALER = ("Dealer_self", "Dealer_Hedging")
_KNOWN = frozenset(_FOREIGN + _TRUST + _DEALER)

_LOT_SHARES = 1_000  # FinMind 融資券餘額以「張」計；本專案一律「股」（黃金規則 3）

_MARGIN_FIELDS = {
    "margin_balance": "MarginPurchaseTodayBalance",
    "short_balance": "ShortSaleTodayBalance",
}


def _date(row: Mapping[str, Any], table: str) -> dt.date:
    raw = row.get("date")
    if not isinstance(raw, str):
        raise DataError(table, None, f"FinMind 回應缺 date 欄或型別非字串：{raw!r}")
    return dt.date.fromisoformat(raw)


def institutional_net(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """FinMind 五類投資人買賣股數 → `institutional_daily` 的三欄淨買賣超（股）。

    某 (日, 股) 缺某類別＝當日該類別無交易，記 0；不丟列。
    """
    acc: dict[tuple[dt.date, str], dict[str, int]] = {}
    for row in rows:
        name = row.get("name")
        if name not in _KNOWN:
            raise DataError(
                "institutional_daily",
                _date(row, "institutional_daily"),
                f"FinMind 出現未知投資人類別 {name!r}（已知：{sorted(_KNOWN)}）——"
                "來源可能改版；靜默忽略會讓對應欄位少算一條腿，故中止。",
            )
        key = (_date(row, "institutional_daily"), str(row["stock_id"]))
        bucket = acc.setdefault(key, {"foreign_net": 0, "trust_net": 0, "dealer_net": 0})
        net = int(row["buy"]) - int(row["sell"])
        if name in _FOREIGN:
            bucket["foreign_net"] += net
        elif name in _TRUST:
            bucket["trust_net"] += net
        else:
            bucket["dealer_net"] += net
    return [{"date": d, "stock_id": sid, **vals} for (d, sid), vals in sorted(acc.items())]


def margin_balances(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """FinMind 融資券（張）→ `margin_daily` 的餘額（股）。缺欄即 raise，不當 0。"""
    out: list[dict[str, Any]] = []
    for row in rows:
        day = _date(row, "margin_daily")
        vals: dict[str, Any] = {"date": day, "stock_id": str(row["stock_id"])}
        for col, src in _MARGIN_FIELDS.items():
            if src not in row:
                raise DataError(
                    "margin_daily",
                    day,
                    f"FinMind 回應缺欄位 {src}（來源改版？）——缺欄當 0 會讓餘額憑空歸零。",
                )
            vals[col] = int(row[src]) * _LOT_SHARES
        out.append(vals)
    out.sort(key=lambda r: (r["date"], r["stock_id"]))
    return out
