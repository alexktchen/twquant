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
from typing import TYPE_CHECKING, cast

import polars as pl

from twquant.errors import DataError

if TYPE_CHECKING:
    from pathlib import Path

    from twquant.data.warehouse import TableSource, Warehouse

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


def load(wh: Warehouse | TableSource) -> TradingCalendar:
    """載入持久化日曆；不存在則即時由 prices_daily 推導（讓下游隨時可用）。

    無檔案系統的 TableSource（如 PgTableSource，D-026）：無 root/sql，直接以
    scan("prices_daily") 的 distinct date 推導（與 derive_from_prices 等價語義）。
    """
    if not hasattr(wh, "root"):
        df = wh.scan("prices_daily").select("date").unique().sort("date").collect()
        return TradingCalendar(df["date"].to_list())
    fs_wh = cast("Warehouse", wh)  # 有 root ⇒ 檔案系統 Warehouse
    p = _path(fs_wh)
    if p.exists():
        return TradingCalendar(pl.read_parquet(p)["date"].to_list())
    return derive_from_prices(fs_wh)


def persist(wh: Warehouse, calendar: TradingCalendar) -> None:
    """把日曆寫成單檔 parquet（原子）。"""
    p = _path(wh)
    p.parent.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame({"date": calendar.sessions}, schema={"date": pl.Date})
    tmp = p.with_suffix(".parquet.tmp")
    df.write_parquet(tmp)
    tmp.replace(p)


def _year_days(year: int) -> list[dt.date]:
    """該西元年所有日曆日（1/1..12/31）。"""
    start, end = dt.date(year, 1, 1), dt.date(year, 12, 31)
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


def official_holidays(wh: Warehouse) -> pl.DataFrame | None:
    """讀官方開休市日曆表 market_holidays；尚未擷取（ingest-holidays）回 None。

    以「有無已落地 .parquet」為準而非僅目錄存在：中斷的寫入可能只留 part-*.parquet.tmp，
    此時 warehouse.connect 不會建視圖、`SELECT ... FROM market_holidays` 會拋 DuckDB
    CatalogException——回 None 讓「尚未擷取」保持乾淨降級（endpoint 回 available:False）。
    """
    if not any((wh.root / "clean" / "market_holidays").rglob("*.parquet")):
        return None
    return wh.sql(
        "SELECT holiday_date, year, name, weekday, description, closed FROM market_holidays"
    )


def official_calendar(wh: Warehouse) -> TradingCalendar:
    """由官方 market_holidays 建交易日曆：涵蓋年度內所有平日 − 官方休市日。

    與 derive_from_prices 不同，這是「前瞻」日曆——不需先有行情即知未來開市/休市，補假、
    週末、國定假日皆依官方公布正確排除。TWSE 一般不於補班週六交易，故只取平日再扣休市；
    若真有特殊週六交易，derive_from_prices（觀測）仍會捕捉、cross_check_official 會標出。
    尚無官方日曆 → raise（提示先 ingest-holidays）。
    """
    hol = official_holidays(wh)
    if hol is None or hol.is_empty():
        raise DataError(
            "market_holidays", None, "尚無官方開休市日曆，請先 `twquant ingest-holidays`"
        )
    closed = set(hol.filter(pl.col("closed"))["holiday_date"].to_list())
    years = sorted({int(y) for y in hol["year"].to_list()})
    sessions = [
        day for year in years for day in _year_days(year) if day.weekday() < 5 and day not in closed
    ]
    return TradingCalendar(sessions)


def cross_check_official(wh: Warehouse) -> dict[str, object]:
    """官方交易日曆 vs 觀測（prices_daily 各來源日期聯集）在官方涵蓋年度內的差異。

    missing＝官方判開市卻無行情（真缺口）；unexpected＝有行情卻官方判休市/未涵蓋（異常）。
    只在官方有涵蓋的年度內比對，避免只抓某年官方日曆卻據以指謫他年觀測日。無行情時回空比對。
    """
    hol = official_holidays(wh)
    covered = sorted({int(y) for y in hol["year"].to_list()}) if hol is not None else []
    empty: dict[str, object] = {"missing": [], "unexpected": [], "range": None, "years": covered}
    if hol is None or hol.is_empty() or wh.latest_date("prices_daily") is None:
        return empty
    official = official_calendar(wh)
    obs = [d for d in derive_from_prices(wh).sessions if d.year in set(covered)]
    if not obs:
        return empty
    lo, hi = obs[0], obs[-1]
    off_in = set(official.between(lo, hi))
    obs_set = set(obs)
    return {
        "missing": sorted(off_in - obs_set),
        "unexpected": sorted(obs_set - off_in),
        "range": (lo, hi),
        "years": covered,
    }


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
