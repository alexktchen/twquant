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

import polars as pl
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


# ── 複查發現的四個缺口（2026-09-04 對抗式複查，8/19 通過驗證）──────────────


def test_ac2_inconsistent_events_flags_factor_contradicted_by_prices() -> None:
    """還原因子若與行情矛盾，會製造反向的「公司行動日曆」洩漏——正是 D-054 要消滅的東西。

    實測 6 筆（6225／2429／1475／9105／3321／2718）如此，其中 3 筆在宣稱的除權日
    其實是**漲停**。單筆的假影響 = 1/adj_factor（6225 為 +47.8%），會讓該股連續 h 個
    訊號日排在約 1,800 檔橫斷面的前 3%。
    """
    import datetime as dt

    from twquant.data.clean import inconsistent_events

    ev = pl.DataFrame(
        [
            # 6225：宣稱 44.4 → 30.04（−32%），但當日其實漲停到 48.8
            {"date": dt.date(2026, 8, 18), "stock_id": "6225", "adj_factor": 30.04 / 44.4},
            # 正常現金除息：前收 125.5 → 開盤 119（參考價 120.74，落在漲跌幅內）
            {"date": dt.date(2024, 7, 4), "stock_id": "2412", "adj_factor": 120.74 / 125.5},
        ]
    )
    px = pl.DataFrame(
        [
            {"date": dt.date(2026, 8, 17), "stock_id": "6225", "open": 44.4, "close": 44.4},
            {"date": dt.date(2026, 8, 18), "stock_id": "6225", "open": 48.8, "close": 48.8},
            {"date": dt.date(2024, 7, 3), "stock_id": "2412", "open": 125.0, "close": 125.5},
            {"date": dt.date(2024, 7, 4), "stock_id": "2412", "open": 119.0, "close": 118.5},
        ]
    )
    bad = inconsistent_events(ev, px, max_deviation=0.10)
    assert bad["stock_id"].to_list() == ["6225"]
    assert abs(bad["deviation"][0]) > 0.10


def test_ac2_inconsistent_events_keeps_events_without_price_rows() -> None:
    """約 5.75% 的除權息日在 prices_daily 無對應列（颱風休市、停牌）。
    無從比對 ≠ 有問題——不得把它們判為矛盾而丟掉。"""
    import datetime as dt

    from twquant.data.clean import inconsistent_events

    ev = pl.DataFrame([{"date": dt.date(2024, 7, 4), "stock_id": "2412", "adj_factor": 0.96}])
    px = pl.DataFrame(
        [{"date": dt.date(2024, 7, 3), "stock_id": "2412", "open": 125.0, "close": 125.5}]
    )  # 除權息當日無行情列
    assert inconsistent_events(ev, px, max_deviation=0.10).is_empty()


def test_ac2_inconsistent_events_allows_limit_move_exactly_on_the_band() -> None:
    """漲跌停價恰好落在帶寬邊界上，是合法的，不得誤判為資料錯誤。

    實測 6791 於 2026-04-16 除息（前收 123.5 → 參考價 120.0），當日自參考價
    **漲停**開在 132.0，偏離正好 +10.0%。adj_factor 是浮點往返值，乘回去有末位誤差。
    """
    import datetime as dt

    from twquant.data.clean import inconsistent_events

    ev = pl.DataFrame(
        [{"date": dt.date(2026, 4, 16), "stock_id": "6791", "adj_factor": 120.0 / 123.5}]
    )
    px = pl.DataFrame(
        [
            {"date": dt.date(2026, 4, 15), "stock_id": "6791", "open": 123.0, "close": 123.5},
            {"date": dt.date(2026, 4, 16), "stock_id": "6791", "open": 132.0, "close": 132.0},
        ]
    )
    assert inconsistent_events(ev, px, max_deviation=0.10).is_empty()


# ── T-151：分割／面額變更／反分割 ────────────────────────────────────────


def _sp(sid: str, day: str, kind: str, before: float, after: float) -> dict[str, object]:
    return {
        "date": day,
        "stock_id": sid,
        "type": kind,
        "before_price": before,
        "after_price": after,
    }


def test_ac1_t151_split_rows_use_the_same_after_over_before_convention() -> None:
    """分割沿用與除權息**同一個**還原慣例，否則兩類事件的累乘會互相矛盾。

    0050 在 2025-06-18 由 188.65 → 47.16 是 1:4 分割，因子恰為 0.25。
    """
    from twquant.data.sources.finmind import split_rows

    got = split_rows([_sp("0050", "2025-06-18", "分割", 188.65, 47.16)])
    assert len(got) == 1
    r = got[0]
    assert r["stock_id"] == "0050"
    assert r["date"] == dt.date(2025, 6, 18)
    assert r["adj_factor"] == pytest.approx(0.25, abs=1e-4)
    assert r["ref_price"] == pytest.approx(47.16)
    assert r["event_type"] == "split"


def test_ac2_t151_reverse_split_keeps_factor_above_one() -> None:
    """反分割的因子 > 1（00632R 3.28 → 22.96 ＝ 1:7）。

    把它強制壓到 < 1 會讓還原方向相反，產生一個看起來合理的錯數字。
    """
    from twquant.data.sources.finmind import split_rows

    r = split_rows([_sp("00632R", "2024-12-11", "反分割", 3.28, 22.96)])[0]
    assert r["adj_factor"] == pytest.approx(7.0, abs=1e-3)
    assert r["event_type"] == "reverse_split"


def test_ac3_t151_par_value_change_is_a_split_by_another_name() -> None:
    """台股多數「分割」在 FinMind 標成`面額變更`（面額 10 → 1 等）。

    只認`分割`會漏掉絕大多數——實測 34 筆裡有 22 筆是面額變更。
    """
    from twquant.data.sources.finmind import split_rows

    r = split_rows([_sp("5314", "2025-03-31", "面額變更", 1390.0, 69.5)])[0]
    assert r["adj_factor"] == pytest.approx(0.05)
    assert r["event_type"] == "split"


def test_ac4_t151_blank_type_is_derived_from_the_factor_not_guessed_as_a_category() -> None:
    """FinMind 有 1 筆 `type` 是空字串（00631L 2026-03-31）。

    還原的數學**只依賴因子**，型別字串是描述性的——這與 `dividend_rows` 不同
    （那裡不同類別有不同的參考價公式，故型別是關鍵資訊）。
    故空字串由因子推導方向，而不是猜一個類別。
    """
    from twquant.data.sources.finmind import split_rows

    assert split_rows([_sp("00631L", "2026-03-31", "", 443.15, 20.14)])[0]["event_type"] == "split"


def test_ac5_t151_unknown_non_empty_type_raises() -> None:
    """非空的未知型別仍要 raise——新類別必須被人看到，不得靜默歸類。"""
    from twquant.data.sources.finmind import split_rows

    with pytest.raises(DataError, match="未知"):
        split_rows([_sp("1234", "2025-01-02", "某種新事件", 100.0, 50.0)])


def test_ac6_t151_non_positive_prices_raise() -> None:
    """0 或負價會讓因子變成 0／inf／負數，並經累乘污染該股全部歷史。"""
    from twquant.data.sources.finmind import split_rows

    with pytest.raises(DataError, match="須 > 0"):
        split_rows([_sp("1234", "2025-01-02", "分割", 100.0, 0.0)])
