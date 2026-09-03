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

from twquant.conventions import TZ
from twquant.errors import DataError

API = "https://api.finmindtrade.com/api/v4/data"

INSTITUTIONAL_DATASET = "TaiwanStockInstitutionalInvestorsBuySell"
MARGIN_DATASET = "TaiwanStockMarginPurchaseShortSale"
VALUATION_DATASET = "TaiwanStockPER"
STATEMENT_DATASETS = {
    "income": "TaiwanStockFinancialStatements",
    "balance": "TaiwanStockBalanceSheet",
    "cashflow": "TaiwanStockCashFlowsStatement",
}
# 損益表的 `EquityAttributableToOwnersOfParent` 有一小群列（實測 2019–2026：235 個股票季、41 檔）
# 的 `origin_name` 是「綜合損益總額歸屬於母公司業主」——那是**綜合損益**（含其他綜合損益），
# 不是淨利。FinMind 只在 origin_name 分辨，映射進長表後就看不出來，會讓 ROE／應計冒用錯誤的分子。
# 故在映射時改名保留（不丟列），讓它無法冒充淨利。這 41 檔同季沒有正常淨利列，故不會撞主鍵。
_COMPREHENSIVE_MARK = "綜合損益"
_PARENT_INCOME = "EquityAttributableToOwnersOfParent"
_PARENT_COMPREHENSIVE = "ComprehensiveIncomeAttributableToOwnersOfParent"

# clean.py:59 的定義：外資＝外陸資(不含自營)＋外資自營商；自營＝自行買賣＋避險。
# 未列出的類別一律 raise——來源新增類別而靜默忽略，會讓某一欄悄悄少一條腿。
_FOREIGN = ("Foreign_Investor", "Foreign_Dealer_Self")
_TRUST = ("Investment_Trust",)
# 興櫃／部分上櫃日的法人資料只有合併的 "Dealer"（無自行買賣／避險拆分、也無外資自營）——
# 實測 2020–2026 staging：27,562 列、280 檔，同 (日, 股) 從未與拆分類別並存。並存時無法判斷是否重複計算，故 raise。
_DEALER = ("Dealer_self", "Dealer_Hedging", "Dealer")
_DEALER_COMBINED = "Dealer"
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
    """FinMind 投資人買賣股數 → `institutional_daily` 的三欄淨買賣超（股）。

    某 (日, 股) 缺某類別＝當日該類別無交易，記 0；不丟列。
    自營商可為拆分（自行買賣＋避險）或合併（"Dealer"，興櫃日），同鍵兩者並存即 raise。
    """
    acc: dict[tuple[dt.date, str], dict[str, int]] = {}
    dealer_kind: dict[tuple[dt.date, str], str] = {}
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
            kind = "combined" if name == _DEALER_COMBINED else "split"
            if dealer_kind.setdefault(key, kind) != kind:
                raise DataError(
                    "institutional_daily",
                    key[0],
                    f"{key[1]} 同日同時出現合併 {_DEALER_COMBINED!r} 與拆分自營商類別——"
                    "無法判斷是否重複計算，故中止。",
                )
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


# 殖利率是「率」→ 黃金規則 3 要求存小數；PER/PBR 是倍數（非率），維持原值。
_VALUATION_FIELDS = {"per": "PER", "pbr": "PBR", "dividend_yield": "dividend_yield"}
_PERCENT_FIELDS = frozenset({"dividend_yield"})


def valuation_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """FinMind `TaiwanStockPER` → `stock_valuation`。

    PER 可為負（虧損公司，TWSE 慣例）——是有意義的值，不轉 null 或 0。
    無 `date` 的列是 staging 對「該檔無資料」寫的佔位列，略過。
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("date") is None:
            continue
        day = _date(row, "stock_valuation")
        vals: dict[str, Any] = {"date": day, "stock_id": str(row["stock_id"])}
        for col, src in _VALUATION_FIELDS.items():
            if src not in row:
                raise DataError(
                    "stock_valuation",
                    day,
                    f"FinMind 回應缺欄位 {src}（來源改版？）——缺欄補 null 會讓評價因子悄悄失效。",
                )
            v = float(row[src])
            vals[col] = v / 100.0 if col in _PERCENT_FIELDS else v
        out.append(vals)
    out.sort(key=lambda r: (r["date"], r["stock_id"]))
    return out


# 法定申報期限（金管會）：Q1 5/15、Q2 8/14、Q3 11/14、年報次年 3/31。available_at 用期限而非季底
# ——季底當可見日就是前視（財報要 1.5～3 個月後才公告）。用期限而非實際公告日是保守估計，
# 標 available_at_estimated=True（比照 monthly_revenue）。
_FILING_DEADLINE: dict[int, tuple[int, int, int]] = {
    3: (0, 5, 15),
    6: (0, 8, 14),
    9: (0, 11, 14),
    12: (1, 3, 31),
}
_FILING_HOUR = 22


def _filing_available_at(period_end: dt.date) -> dt.datetime:
    try:
        year_offset, month, day = _FILING_DEADLINE[period_end.month]
    except KeyError as exc:
        raise DataError(
            "financial_statements",
            period_end,
            f"財報期間末日 {period_end} 不是季底（3/6/9/12 月）——來源改版或資料錯位，不得猜期限。",
        ) from exc
    return dt.datetime(period_end.year + year_offset, month, day, _FILING_HOUR, 0, tzinfo=TZ)


def statement_rows(rows: Iterable[Mapping[str, Any]], statement: str) -> list[dict[str, Any]]:
    """FinMind 三表（`TaiwanStockFinancialStatements`／`BalanceSheet`／`CashFlowsStatement`）
    → `financial_statements` 長表。

    - `_per` 後綴的百分比列剔除（資產負債表附帶的佔比欄，不是金額）。
    - 損益表中 origin_name 標示「綜合損益」的母公司列改名為
      `ComprehensiveIncomeAttributableToOwnersOfParent`，不讓它冒充淨利。
    - 值一律 float（元）；缺 `value` 即 raise，不當 0（黃金規則 5）。
    - 無 `date` 的列是 staging 的佔位列，略過。
    """
    if statement not in STATEMENT_DATASETS:
        raise ValueError(f"statement 須為 {sorted(STATEMENT_DATASETS)} 之一，得到 {statement!r}")
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("date") is None:
            continue
        typ = row.get("type")
        if not isinstance(typ, str) or not typ:
            raise DataError("financial_statements", None, f"FinMind 回應缺 type 欄：{dict(row)!r}")
        if typ.endswith("_per"):
            continue
        if statement == "income" and typ == _PARENT_INCOME:
            origin = row.get("origin_name")
            if isinstance(origin, str) and _COMPREHENSIVE_MARK in origin:
                typ = _PARENT_COMPREHENSIVE
        day = _date(row, "financial_statements")
        if "value" not in row or row["value"] is None:
            raise DataError(
                "financial_statements",
                day,
                f"FinMind 回應缺 value（{typ}）——缺值補 0 會讓比率因子悄悄失真。",
            )
        out.append(
            {
                "date": day,
                "stock_id": str(row["stock_id"]),
                "statement": statement,
                "type": typ,
                "value": float(row["value"]),
                "available_at": _filing_available_at(day),
                "available_at_estimated": True,
            }
        )
    out.sort(key=lambda r: (r["date"], r["stock_id"], r["type"]))
    return out
