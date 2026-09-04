"""T-098／D-054：除權息事件表（corporate_events）的來源對映與落地規格。

契約（由本測試凍結）：
- `dividend_rows(rows)`：FinMind `TaiwanStockDividendResult` → corporate_events 六欄。
  還原因子一律 `after_price / before_price`——**不用** `stock_and_cache_dividend`
  （它是除權息價格落差，只有現金事件時才等於每股現金）也**不用** `reference_price`
  （651 筆股票股利事件它只是複製 before_price、未還原）。
- 上市與上櫃兩套詞彙都要對映（只比對一套會靜默丟掉 4,175 筆上櫃現金事件）。
- `available_at` 必為 tz-aware Asia/Taipei；缺這欄會讓 `warehouse.py` 跳過 PIT 過濾。
- 價格 ≤ 0 → raise（黃金規則 5，不得靜默補值）。
"""

from __future__ import annotations

import datetime as dt

import pytest

from twquant.conventions import TZ
from twquant.errors import DataError


def test_ac2_dividend_rows_uses_after_over_before_as_adj_factor() -> None:
    """還原因子 = after/before。實測此式對全部 12,149 筆事件都成立（最大殘差 0.01）。"""
    from twquant.data.sources.finmind import dividend_rows

    got = {
        r["stock_id"]: r
        for r in dividend_rows(
            [
                # 2412 中華電 2024-07-04 純現金：125.5 → 120.74
                {
                    "date": "2024-07-04",
                    "stock_id": "2412",
                    "before_price": 125.5,
                    "after_price": 120.74,
                    "stock_and_cache_dividend": 4.758,
                    "stock_or_cache_dividend": "息",
                    "reference_price": 120.74,
                },
                # 6669 2026-09-02 純配股、零現金：7800 → 2614.99（約 3:1）
                {
                    "date": "2026-09-02",
                    "stock_id": "6669",
                    "before_price": 7800.0,
                    "after_price": 2614.99,
                    "stock_and_cache_dividend": 5185.002441,
                    "stock_or_cache_dividend": "權",
                    "reference_price": 2614.99,
                },
            ]
        )
    }
    assert got["2412"]["adj_factor"] == pytest.approx(120.74 / 125.5)
    assert got["6669"]["adj_factor"] == pytest.approx(2614.99 / 7800.0)
    # ref_price 取 after_price（真正的除權息參考價），不取 reference_price
    assert got["6669"]["ref_price"] == pytest.approx(2614.99)
    assert got["2412"]["date"] == dt.date(2024, 7, 4)


def test_ac2_dividend_rows_maps_both_market_vocabularies() -> None:
    """上市用 {息, 權, 權息}、上櫃用 {除息, 除權, 除權息}。只認一套會靜默丟掉半個市場。"""
    from twquant.data.sources.finmind import dividend_rows

    rows = [
        {
            "date": "2024-07-01",
            "stock_id": f"{i:04d}",
            "before_price": 100.0,
            "after_price": 97.0,
            "stock_and_cache_dividend": 3.0,
            "stock_or_cache_dividend": v,
            "reference_price": 97.0,
        }
        for i, v in enumerate(["息", "除息", "權", "除權", "權息", "除權息"], start=1101)
    ]
    got = {r["stock_id"]: r["event_type"] for r in dividend_rows(rows)}
    assert got["1101"] == got["1102"] == "cash_dividend"
    assert got["1103"] == got["1104"] == "stock_dividend"
    assert got["1105"] == got["1106"] == "cash_and_stock_dividend"


def test_ac2_dividend_rows_raises_on_unknown_event_type() -> None:
    """來源新增詞彙必須 raise——靜默歸類會讓還原因子套錯一整類事件。"""
    from twquant.data.sources.finmind import dividend_rows

    with pytest.raises(DataError, match="未知除權息類別"):
        dividend_rows(
            [
                {
                    "date": "2024-07-01",
                    "stock_id": "1101",
                    "before_price": 100.0,
                    "after_price": 97.0,
                    "stock_and_cache_dividend": 3.0,
                    "stock_or_cache_dividend": "現金減資",
                    "reference_price": 97.0,
                }
            ]
        )


def test_ac2_dividend_rows_keeps_negative_and_zero_gaps() -> None:
    """實測 5 筆為負（增資價高於市價，合法）、4 筆為零。不得用 `div > 0` 過濾掉。"""
    from twquant.data.sources.finmind import dividend_rows

    got = {
        r["stock_id"]: r["adj_factor"]
        for r in dividend_rows(
            [
                # 1312 2026-06-11：before 13.2 → after 13.4（負的價格落差）
                {
                    "date": "2026-06-11",
                    "stock_id": "1312",
                    "before_price": 13.2,
                    "after_price": 13.4,
                    "stock_and_cache_dividend": -0.209613,
                    "stock_or_cache_dividend": "權",
                    "reference_price": 13.2,
                },
                {
                    "date": "2024-07-01",
                    "stock_id": "1101",
                    "before_price": 50.0,
                    "after_price": 50.0,
                    "stock_and_cache_dividend": 0.0,
                    "stock_or_cache_dividend": "息",
                    "reference_price": 50.0,
                },
            ]
        )
    }
    assert got["1312"] > 1.0
    assert got["1101"] == pytest.approx(1.0)


def test_ac2_dividend_rows_raises_on_nonpositive_price() -> None:
    """價格 ≤ 0 會讓還原因子變成 0、inf 或負數，並經累乘污染該股全部歷史。"""
    from twquant.data.sources.finmind import dividend_rows

    with pytest.raises(DataError, match="除權息價格"):
        dividend_rows(
            [
                {
                    "date": "2024-07-01",
                    "stock_id": "1101",
                    "before_price": 0.0,
                    "after_price": 97.0,
                    "stock_and_cache_dividend": 3.0,
                    "stock_or_cache_dividend": "息",
                    "reference_price": 97.0,
                }
            ]
        )


def test_ac2_dividend_rows_available_at_is_tz_aware_taipei() -> None:
    """PitView 對**沒有** available_at 欄的表直接跳過過濾（warehouse.py），
    故這欄缺漏等於開一個靜默的前視破口。除息日事先數週公告，同日 18:00 是保守值。"""
    from twquant.data.sources.finmind import dividend_rows

    (row,) = dividend_rows(
        [
            {
                "date": "2024-07-04",
                "stock_id": "2412",
                "before_price": 125.5,
                "after_price": 120.74,
                "stock_and_cache_dividend": 4.758,
                "stock_or_cache_dividend": "息",
                "reference_price": 120.74,
            }
        ]
    )
    at = row["available_at"]
    assert at.tzinfo is not None
    assert at == dt.datetime(2024, 7, 4, 18, 0, tzinfo=TZ)


def test_ac2_corporate_events_is_a_mirror_table() -> None:
    """沒有 TableSpec 就無法 upsert 進 PG，也不會被 serving rebuild 認得。"""
    from twquant.data.serving import schema as sch

    assert "corporate_events" in sch.MIRROR_TABLES
    spec = next(s for s in sch.MIRROR_SPECS if s.name == "corporate_events")
    assert spec.pk == ("date", "stock_id")
    assert dict(spec.columns)["adj_factor"] == "double precision"
