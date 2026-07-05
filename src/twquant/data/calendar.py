"""交易日曆（SPEC-000 AC-3）。

以 TWSE 實際開市日為準判斷交易日與缺口，**禁止用 weekday() 推算**（國定假日、颱風假
無法從星期推得）。第一版採「推導核對」：交易日 = prices_daily 各來源日期的聯集——
台股上市（TWSE）與上櫃（TPEX）共用同一開市日曆，兩來源至少一個完整即能還原真實日曆
（本專案 TPEX 幾乎無缺，TWSE 於 2026-01 後被 ban，聯集仍為完整日曆）。

日曆是參考表（reference/dimension），非日頻事實表，故以單檔持久化於
`data/clean/trading_calendar/calendar.parquet`，不走日期分區。
"""

from __future__ import annotations

import bisect
import datetime as dt
from collections.abc import Iterable
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from pathlib import Path

    from twquant.data.warehouse import Warehouse

_REL_PATH = ("clean", "trading_calendar", "calendar.parquet")


class TradingCalendar:
    """一組交易日（升冪去重）。查詢是否開市、區間內開市日、缺口、前/後一個交易日。"""

    def __init__(self, sessions: Iterable[dt.date]) -> None:
        self._days: list[dt.date] = sorted(set(sessions))
        self._set: frozenset[dt.date] = frozenset(self._days)

    def __len__(self) -> int:
        return len(self._days)

    @property
    def sessions(self) -> list[dt.date]:
        return list(self._days)

    def is_session(self, day: dt.date) -> bool:
        return day in self._set

    def between(self, start: dt.date, end: dt.date) -> list[dt.date]:
        """[start, end]（含端點）內的交易日。"""
        lo = bisect.bisect_left(self._days, start)
        hi = bisect.bisect_right(self._days, end)
        return self._days[lo:hi]

    def missing(self, present: Iterable[dt.date], start: dt.date, end: dt.date) -> list[dt.date]:
        """[start, end] 內「開市卻沒有資料」的交易日——真正的缺口（假日不算）。"""
        have = set(present)
        return [d for d in self.between(start, end) if d not in have]

    def next_session(self, day: dt.date) -> dt.date | None:
        """day 之後（不含）的第一個交易日。"""
        i = bisect.bisect_right(self._days, day)
        return self._days[i] if i < len(self._days) else None

    def prev_session(self, day: dt.date) -> dt.date | None:
        """day 之前（不含）的最後一個交易日。"""
        i = bisect.bisect_left(self._days, day)
        return self._days[i - 1] if i > 0 else None


def derive_from_prices(wh: Warehouse) -> TradingCalendar:
    """由 prices_daily 各來源日期聯集推導交易日曆（DuckDB 讀全來源分區）。"""
    df = wh.sql("SELECT DISTINCT date FROM prices_daily ORDER BY date")
    return TradingCalendar(df["date"].to_list())


def _path(wh: Warehouse) -> Path:
    return wh.root.joinpath(*_REL_PATH)


def load(wh: Warehouse) -> TradingCalendar:
    """載入持久化日曆；不存在則即時由 prices_daily 推導（讓下游隨時可用）。"""
    p = _path(wh)
    if p.exists():
        return TradingCalendar(pl.read_parquet(p)["date"].to_list())
    return derive_from_prices(wh)


def persist(wh: Warehouse, calendar: TradingCalendar) -> None:
    """把日曆寫成單檔 parquet（原子）。"""
    p = _path(wh)
    p.parent.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame({"date": calendar.sessions}, schema={"date": pl.Date})
    tmp = p.with_suffix(".parquet.tmp")
    df.write_parquet(tmp)
    tmp.replace(p)


def cross_check(wh: Warehouse, calendar: TradingCalendar) -> dict[str, list[dt.date]]:
    """核對：在各來源自身的涵蓋區間內，日曆開市卻缺該來源資料的日子（單來源缺口）。

    回傳 {"twse": [...], "tpex": [...]}。兩來源在其重疊區間應一致；差異即待補的缺口。
    """
    out: dict[str, list[dt.date]] = {}
    for source in ("twse", "tpex"):
        dates = wh.source_dates("prices_daily", source)
        if not dates:
            out[source] = []
            continue
        out[source] = calendar.missing(dates, dates[0], dates[-1])
    return out
