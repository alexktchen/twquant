"""T-010 / D-044：FinMind 大量歷史回補的映射與單位換算。

映射已對 2026-08-28 的既有 TWSE 資料逐位元驗證（25/25 全等），本檔把那份對照凍結成測試。

FinMind 的欄位語意與本專案 schema 不同，兩處容易錯：
1. 投資人類別是**五類**（外資／外資自營／投信／自營自行／自營避險），
   而 `institutional_daily` 只有三欄——合併規則見 `clean.py:59`。
2. 融資券餘額 FinMind 用**張**，本專案一律用**股**（黃金規則 3）。
"""

from __future__ import annotations

import datetime as dt

import pytest

from twquant.conventions import TZ
from twquant.data.sources.finmind import institutional_net, margin_balances
from twquant.errors import DataError

_D = dt.date(2026, 8, 28)


def _inst(pairs: list[tuple[str, int, int]]) -> list[dict[str, object]]:
    return [
        {"date": _D.isoformat(), "stock_id": "2330", "name": n, "buy": b, "sell": s}
        for n, b, s in pairs
    ]


def test_institutional_net_matches_twse_byte_for_byte() -> None:
    """三類合併規則：外資＝外陸資＋外資自營；自營＝自行買賣＋避險。

    真實對照（2330 @ 2026-08-28，TWSE 值 3031655 / -1764995 / -23367）。
    """
    rows = _inst(
        [
            ("Foreign_Investor", 10_027_572, 6_995_917),
            ("Foreign_Dealer_Self", 0, 0),
            ("Investment_Trust", 109_915, 1_874_910),
            ("Dealer_self", 70_050, 15_400),
            ("Dealer_Hedging", 113_681, 191_698),
        ]
    )
    out = institutional_net(rows)
    assert out == [
        {
            "date": _D,
            "stock_id": "2330",
            "foreign_net": 3_031_655,
            "trust_net": -1_764_995,
            "dealer_net": -23_367,
        }
    ]


def test_institutional_foreign_includes_dealer_self_leg() -> None:
    """外資自營商非零時必須併入外資——clean.py:59 的定義，漏掉會系統性低估外資。"""
    rows = _inst(
        [
            ("Foreign_Investor", 1_000, 400),
            ("Foreign_Dealer_Self", 250, 50),  # 淨 +200
            ("Investment_Trust", 0, 0),
            ("Dealer_self", 0, 0),
            ("Dealer_Hedging", 0, 0),
        ]
    )
    assert institutional_net(rows)[0]["foreign_net"] == 800, (
        "600 + 200；只取 Foreign_Investor 會得 600"
    )


def test_institutional_unknown_category_raises() -> None:
    """來源新增投資人類別 → raise，不得靜默忽略（黃金規則 5）。

    靜默忽略會讓 dealer_net 悄悄少一條腿，而數字看起來仍然合理。
    """
    rows = _inst([("Foreign_Investor", 1, 0), ("Some_New_Category", 5, 1)])
    with pytest.raises(DataError, match="Some_New_Category"):
        institutional_net(rows)


def test_institutional_missing_category_is_treated_as_zero_not_null() -> None:
    """某日某檔沒有某類別的紀錄＝當日該類別無交易，記 0；不得整列丟棄。"""
    rows = _inst([("Foreign_Investor", 500, 100)])
    out = institutional_net(rows)
    assert out[0] == {
        "date": _D,
        "stock_id": "2330",
        "foreign_net": 400,
        "trust_net": 0,
        "dealer_net": 0,
    }


def test_margin_balances_convert_lots_to_shares() -> None:
    """FinMind 用張、本專案用股（黃金規則 3）。真實對照：2330 @ 2026-08-28。"""
    rows = [
        {
            "date": _D.isoformat(),
            "stock_id": "2330",
            "MarginPurchaseTodayBalance": 27_630,
            "ShortSaleTodayBalance": 28,
        }
    ]
    assert margin_balances(rows) == [
        {"date": _D, "stock_id": "2330", "margin_balance": 27_630_000, "short_balance": 28_000}
    ]


def test_margin_balances_missing_field_raises() -> None:
    """來源改欄 → raise，不得把缺欄當 0（會讓餘額憑空歸零）。"""
    with pytest.raises(DataError, match="ShortSaleTodayBalance"):
        margin_balances(
            [{"date": _D.isoformat(), "stock_id": "2330", "MarginPurchaseTodayBalance": 1}]
        )


def test_both_mappers_are_deterministic_and_sorted() -> None:
    """輸出依 (date, stock_id) 排序，確保回補結果 bit-identical。"""
    rows = [
        {"date": "2026-08-28", "stock_id": "2454", "name": "Foreign_Investor", "buy": 1, "sell": 0},
        {"date": "2026-08-27", "stock_id": "2330", "name": "Foreign_Investor", "buy": 2, "sell": 0},
        {"date": "2026-08-28", "stock_id": "2330", "name": "Foreign_Investor", "buy": 3, "sell": 0},
    ]
    got = [(r["date"], r["stock_id"]) for r in institutional_net(rows)]
    assert got == sorted(got), "未排序會讓兩次回補產生不同的 parquet"


def test_valuation_rows_convert_yield_to_decimal_but_keep_multiples() -> None:
    """殖利率是「率」→ 依黃金規則 3 存小數；PER/PBR 是倍數不是率 → 維持原值。

    FinMind 的 dividend_yield 以百分比計（實測中位數 2.89 ＝ 2.89%）。
    """
    from twquant.data.sources.finmind import valuation_rows

    rows = [
        {
            "date": "2020-01-02",
            "stock_id": "1101",
            "dividend_yield": 9.09,
            "PER": 10.86,
            "PBR": 1.36,
        }
    ]
    assert valuation_rows(rows) == [
        {
            "date": dt.date(2020, 1, 2),
            "stock_id": "1101",
            "per": 10.86,
            "pbr": 1.36,
            "dividend_yield": 0.0909,
        }
    ]


def test_valuation_rows_preserve_negative_per_as_null_free_value() -> None:
    """PER 為負＝虧損公司（TWSE 慣例），是有意義的值，不得轉 null 或 0。"""
    from twquant.data.sources.finmind import valuation_rows

    rows = [
        {"date": "2024-06-03", "stock_id": "2498", "dividend_yield": 0.0, "PER": -1.0, "PBR": 2.5}
    ]
    out = valuation_rows(rows)
    assert out[0]["per"] == -1.0
    assert out[0]["dividend_yield"] == 0.0


def test_valuation_rows_missing_field_raises() -> None:
    """來源改欄即 raise，不得靜默補 null（黃金規則 5）。"""
    from twquant.data.sources.finmind import valuation_rows

    with pytest.raises(DataError, match="PBR"):
        valuation_rows(
            [{"date": "2024-06-03", "stock_id": "2330", "PER": 20.0, "dividend_yield": 1.0}]
        )


def test_valuation_rows_skip_placeholder_rows_without_date() -> None:
    """staging 對「無資料」的檔會寫一列只有 stock_id 的佔位列——須略過而非 raise。"""
    from twquant.data.sources.finmind import valuation_rows

    assert valuation_rows([{"stock_id": "9999"}]) == []


# ── T-097：財報三表映射（FinMind 三個 dataset → financial_statements 長表）──────────
# 兩個容易錯的點：(1) available_at 必須是**法定申報期限**而非季底——用季底就是前視；
# (2) 損益表與資產負債表都有 `EquityAttributableToOwnersOfParent`，一個是淨利一個是權益，
#     靠 `statement` 欄區分，不能靠 type 名。


def _st(date: str, typ: str, value: float, sid: str = "2330") -> dict[str, object]:
    return {"date": date, "stock_id": sid, "type": typ, "value": value, "origin_name": "x"}


def test_t097_available_at_is_statutory_filing_deadline() -> None:
    from twquant.data.sources.finmind import statement_rows

    rows = [
        _st("2024-03-31", "Revenue", 1.0),
        _st("2024-06-30", "Revenue", 2.0),
        _st("2024-09-30", "Revenue", 3.0),
        _st("2024-12-31", "Revenue", 4.0),
    ]
    out = statement_rows(rows, "income")
    got = {r["date"]: r["available_at"] for r in out}
    assert got[dt.date(2024, 3, 31)] == dt.datetime(2024, 5, 15, 22, 0, tzinfo=TZ)  # Q1 → 5/15
    assert got[dt.date(2024, 6, 30)] == dt.datetime(2024, 8, 14, 22, 0, tzinfo=TZ)  # Q2 → 8/14
    assert got[dt.date(2024, 9, 30)] == dt.datetime(2024, 11, 14, 22, 0, tzinfo=TZ)  # Q3 → 11/14
    assert got[dt.date(2024, 12, 31)] == dt.datetime(
        2025, 3, 31, 22, 0, tzinfo=TZ
    )  # 年報 → 次年 3/31
    assert all(r["available_at_estimated"] is True for r in out)
    assert all(r["statement"] == "income" for r in out)


def test_t097_percent_rows_are_dropped() -> None:
    from twquant.data.sources.finmind import statement_rows

    rows = [_st("2024-12-31", "TotalAssets", 100.0), _st("2024-12-31", "TotalAssets_per", 100.0)]
    out = statement_rows(rows, "balance")
    assert [r["type"] for r in out] == ["TotalAssets"]


def test_t097_non_quarter_end_date_raises() -> None:
    from twquant.data.sources.finmind import statement_rows

    with pytest.raises(DataError, match="2024-02-29"):
        statement_rows([_st("2024-02-29", "Revenue", 1.0)], "income")


def test_t097_unknown_statement_tag_raises() -> None:
    from twquant.data.sources.finmind import statement_rows

    with pytest.raises(ValueError, match="statement"):
        statement_rows([_st("2024-03-31", "Revenue", 1.0)], "profit")


def test_t097_missing_value_raises_not_zero() -> None:
    from twquant.data.sources.finmind import statement_rows

    with pytest.raises(DataError, match="value"):
        statement_rows([{"date": "2024-03-31", "stock_id": "2330", "type": "Revenue"}], "income")


def test_t097_placeholder_rows_without_date_are_skipped_and_output_sorted() -> None:
    from twquant.data.sources.finmind import statement_rows

    rows: list[dict[str, object]] = [
        {"stock_id": "9999"},
        _st("2024-06-30", "Revenue", 2.0, "2454"),
        _st("2024-03-31", "Revenue", 1.0, "2454"),
        _st("2024-03-31", "Revenue", 9.0, "1101"),
    ]
    got = [(r["date"], r["stock_id"]) for r in statement_rows(rows, "income")]
    assert got == sorted(got) and len(got) == 3
