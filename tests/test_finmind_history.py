"""T-161：FinMind 長歷史清洗器；T-163：D-062 的相對流動性門檻。

清洗器的測試重點是**它丟掉了什麼、有沒有說**——這批資料的危險不在於算錯，
而在於 FinMind 把缺值編碼成 0，而 0 通過每一道 null 檢查。
"""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from twquant.conventions import TZ
from twquant.data.sources.finmind_history import (
    clean_finmind_prices,
    price_identity_violations,
    trading_calendar,
)
from twquant.errors import DataError


def _raw(rows: list[dict[str, object]]) -> pl.DataFrame:
    """FinMind TaiwanStockPrice 的原始欄位形狀（date 是字串）。"""
    base = {
        "date": "2024-01-02",
        "stock_id": "2330",
        "Trading_Volume": 20_000_000,
        "Trading_money": 12_000_000_000.0,
        "open": 600.0,
        "max": 606.0,
        "min": 597.0,
        "close": 601.0,
        "spread": 1.0,
        "Trading_turnover": 30_000,
    }
    return pl.DataFrame([{**base, **r} for r in rows])


# ── T-161：清洗器 ─────────────────────────────────────────────────────────


def test_ac1_maps_columns_and_types_to_the_prices_daily_schema() -> None:
    """AC-1：欄名／型別／欄序由 `schemas.validate` 把關，不是靠程式碼記得。

    FinMind 的 `max`／`min`／`Trading_Volume`／`Trading_money` 要變成
    `high`／`low`／`volume`／`amount`，`spread` 與 `Trading_turnover` 要丟掉
    （schema 是 strict=True，多一欄就會被擋下來）。
    """
    r = clean_finmind_prices(_raw([{}]))
    assert r.rows_out == 1
    assert r.frame.columns == [
        "date",
        "stock_id",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "limit_up_price",
        "limit_down_price",
        "available_at",
    ]
    row = r.frame.row(0, named=True)
    assert row["date"] == dt.date(2024, 1, 2)
    assert row["high"] == 606.0 and row["low"] == 597.0
    assert row["volume"] == 20_000_000 and row["amount"] == 12_000_000_000.0
    assert isinstance(row["stock_id"], str), "黃金規則 4：代號一律字串"
    assert row["limit_up_price"] is None and row["limit_down_price"] is None


def test_ac2_zero_prices_are_dropped_and_counted() -> None:
    """AC-2：OHLC 為 0 是 FinMind 的「查無資料」編碼，**不是**價格為零元。

    這是這批資料最大的地雷：`0` 通過每一道 null 檢查。T-140 的 22,988 列 `inf`
    根因就是 1,185 列 `close=0` 被當除數，而 `inf` 連 `drop_nulls` 都攔不住。
    剔除是對的，但**必須計數回報**（黃金規則 5：不靜默）。
    """
    raw = _raw(
        [
            {},
            {"date": "2024-01-03", "close": 0.0},
            {"date": "2024-01-04", "open": 0.0},
            {"date": "2024-01-05", "max": 0.0, "min": 0.0},
        ]
    )
    r = clean_finmind_prices(raw)
    assert r.rows_in == 4
    assert r.rows_out == 1, "三列零價全部剔除"
    assert r.dropped_zero_price == 3
    assert "零價剔除 3" in r.summary()
    assert r.frame["date"].to_list() == [dt.date(2024, 1, 2)]


def test_ac2_zero_volume_survives() -> None:
    """AC-2：`volume = 0` **不剔除**——有報價但當天沒成交是合法狀態，不是缺值。

    把它一起剔掉會系統性地丟掉流動性最差的那一端，而那正是宇宙濾網要判斷的對象。
    """
    r = clean_finmind_prices(_raw([{"Trading_Volume": 0, "Trading_money": 0.0}]))
    assert r.rows_out == 1
    assert r.dropped_zero_price == 0
    assert r.frame["volume"][0] == 0


def test_ac3_available_at_matches_the_existing_cleaner() -> None:
    """AC-3：`available_at` ＝ 該交易日 18:00（tz-aware Asia/Taipei）。

    與 `clean.py::clean_prices` 的 `_at(date, 18)` **逐字相同**——兩條路徑寫進
    同一張表，PIT 邊界的語意不得有兩套；差一小時就會讓同一天的因子在兩批資料上
    可見性不同，而那不會有任何一關報錯。
    """
    r = clean_finmind_prices(_raw([{}]))
    at = r.frame["available_at"][0]
    assert at == dt.datetime(2024, 1, 2, 18, 0, tzinfo=TZ)
    assert at.tzinfo is not None, "黃金規則 2：禁止 naive datetime"


def test_ac4_duplicate_date_stock_pairs_are_deduped_and_counted() -> None:
    """AC-4：同一 (date, stock_id) 只留最後一列，並計數。

    PG 的主鍵是 (date, stock_id)，重複 PK 會讓 upsert 二次命中同列而
    CardinalityViolation——在寫入之前就要收斂掉。
    """
    raw = _raw([{}, {"close": 602.0}])
    r = clean_finmind_prices(raw)
    assert r.rows_out == 1
    assert r.dropped_duplicate == 1
    assert r.frame["close"][0] == 602.0, "keep=last"


def test_ac5_missing_columns_raise_with_the_stub_file_hint() -> None:
    """AC-5：缺欄位直接 raise。

    查無資料的股票會落地成只有 `stock_id` 一欄的殘檔（實測每個資料集 336~874 檔）。
    那不是壞檔，是「這檔沒有這種資料」——訊息要講清楚，呼叫端才知道該跳過而不是修檔。
    """
    with pytest.raises(DataError, match="缺欄位"):
        clean_finmind_prices(pl.DataFrame({"stock_id": ["2330"]}))


def test_ac5_bad_date_format_raises_instead_of_becoming_null() -> None:
    """AC-5：日期格式不符即 raise，不靜默轉 null（那會讓整批資料悄悄少一段）。"""
    with pytest.raises(Exception, match=r"(?i)conversion|strict|date"):
        clean_finmind_prices(_raw([{"date": "2024/01/02"}]))


def test_ac6_identity_check_catches_lot_vs_share_unit_error() -> None:
    """AC-6：單位錯誤（張 vs 股）不會觸發任何 schema 檢查，要靠恆等式抓。

    `volume` 若是「張」而非「股」，`amount / volume` 會是實際股價的 1,000 倍
    ——兩者都是合法的非負數，schema 完全攔不住。
    """
    ok = clean_finmind_prices(_raw([{}])).frame
    assert price_identity_violations(ok).is_empty()

    lots = clean_finmind_prices(_raw([{"Trading_Volume": 20_000}])).frame  # 股數誤填成張數
    assert price_identity_violations(lots).height == 1

    crossed = clean_finmind_prices(_raw([{"min": 610.0}])).frame  # low > close
    assert price_identity_violations(crossed).height == 1


def test_trading_calendar_is_the_union_across_stocks() -> None:
    """個股缺列正是要偵測的東西，故日曆取多檔聯集而非任一檔自己的列。"""
    a = clean_finmind_prices(_raw([{"date": "2024-01-02"}, {"date": "2024-01-03"}])).frame
    b = clean_finmind_prices(
        _raw(
            [{"date": "2024-01-03", "stock_id": "2317"}, {"date": "2024-01-04", "stock_id": "2317"}]
        )
    ).frame
    assert trading_calendar([a, b]) == [
        dt.date(2024, 1, 2),
        dt.date(2024, 1, 3),
        dt.date(2024, 1, 4),
    ]


def test_empty_input_returns_an_empty_valid_frame() -> None:
    """零列但欄位齊全（該股在這個期間沒有任何交易日）→ 回空表，不 raise。

    與「只有 stock_id 一欄的殘檔」是兩回事：後者是缺欄位，該 raise。
    """
    r = clean_finmind_prices(_raw([{}]).head(0))
    assert r.rows_in == 0 and r.rows_out == 0
    assert r.frame.columns[0] == "date"
