"""T-003：交易日曆（SPEC-000 AC-3）。缺口以開市日為準，不用 weekday 推算。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl

from twquant.conventions import TZ
from twquant.data import calendar as cal
from twquant.data import schemas
from twquant.data.warehouse import Warehouse

# 一個「開市日」集合：連續平日但中間 6/19（端午）休市
_SESSIONS = [
    dt.date(2026, 6, 15),
    dt.date(2026, 6, 16),
    dt.date(2026, 6, 17),
    dt.date(2026, 6, 18),
    # 6/19 端午休市 → 不在日曆
    dt.date(2026, 6, 22),
    dt.date(2026, 6, 23),
]


def test_load_from_table_source_stub() -> None:
    # TableSource（無 root，如 PgTableSource，T-046/D-026）→ 由 prices_daily 推導日曆
    class _Src:
        def scan(self, table: str, layer: str = "clean") -> pl.LazyFrame:
            assert table == "prices_daily"
            return pl.DataFrame(
                {"date": [_SESSIONS[1], _SESSIONS[0], _SESSIONS[1]]},
                schema={"date": pl.Date},
            ).lazy()

    tc = cal.load(_Src())  # type: ignore[arg-type]  # 鴨型：僅需 scan
    assert tc.sessions == [_SESSIONS[0], _SESSIONS[1]]


def test_is_session_and_between() -> None:
    tc = cal.TradingCalendar(_SESSIONS)
    assert tc.is_session(dt.date(2026, 6, 18))
    assert not tc.is_session(dt.date(2026, 6, 19))  # 端午
    assert not tc.is_session(dt.date(2026, 6, 20))  # 週六
    assert tc.between(dt.date(2026, 6, 16), dt.date(2026, 6, 22)) == [
        dt.date(2026, 6, 16),
        dt.date(2026, 6, 17),
        dt.date(2026, 6, 18),
        dt.date(2026, 6, 22),
    ]


def test_missing_ignores_holiday() -> None:
    tc = cal.TradingCalendar(_SESSIONS)
    # 手上有的資料缺了 6/17；6/19 是假日不該算缺口
    present = [d for d in _SESSIONS if d != dt.date(2026, 6, 17)]
    gaps = tc.missing(present, dt.date(2026, 6, 15), dt.date(2026, 6, 23))
    assert gaps == [dt.date(2026, 6, 17)]  # 只有真正缺的開市日，端午不列入


def test_next_prev_session() -> None:
    tc = cal.TradingCalendar(_SESSIONS)
    assert tc.next_session(dt.date(2026, 6, 18)) == dt.date(2026, 6, 22)  # 跨端午+週末
    assert tc.prev_session(dt.date(2026, 6, 22)) == dt.date(2026, 6, 18)
    assert tc.next_session(dt.date(2026, 6, 23)) is None
    assert tc.prev_session(dt.date(2026, 6, 15)) is None


def _prices(dates: list[dt.date], source: str) -> pl.DataFrame:
    rows = [
        {
            "date": d,
            "stock_id": "2330",
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "volume": 1000,
            "amount": 100_000.0,
            "limit_up_price": None,
            "limit_down_price": None,
            "available_at": dt.datetime(d.year, d.month, d.day, 18, tzinfo=TZ),
        }
        for d in dates
    ]
    return pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(schemas.TW_DT),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )


def test_derive_union_of_sources(tmp_path: Path) -> None:
    wh = Warehouse(tmp_path)
    # TWSE 於 6/17 後「被 ban」缺資料；TPEX 完整 → 聯集還原真實日曆
    wh.write("clean", "prices_daily", _prices(_SESSIONS[:3], "twse"), source="twse")
    wh.write("clean", "prices_daily", _prices(_SESSIONS, "tpex"), source="tpex")
    tc = cal.derive_from_prices(wh)
    assert tc.sessions == _SESSIONS  # 聯集 = 完整日曆
    # TWSE 在其涵蓋區間（6/15~6/17）內無缺口；聯集區間外不算它的缺口
    checks = cal.cross_check(wh, tc)
    assert checks["twse"] == []
    assert checks["tpex"] == []


def test_persist_load_roundtrip(tmp_path: Path) -> None:
    wh = Warehouse(tmp_path)
    tc = cal.TradingCalendar(_SESSIONS)
    cal.persist(wh, tc)
    assert cal._path(wh).exists()
    loaded = cal.load(wh)
    assert loaded.sessions == _SESSIONS
