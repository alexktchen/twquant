"""行情中繼（A-014）：把富邦錄製程式寫在本機資料庫的逐筆，接到一個檔案後面，給交易程式跟著讀。

交易程式那個程序不連資料庫（SPEC §1）；會連資料庫的是這一支，另外一個程序在跑。
只讀資料庫（SELECT），不改任何東西。資料庫慢、斷線只會卡住這一支：交易程式照跑，從行情的時間看得出行情停了。

寫出來的檔案跟 A-013 的匯出檔同一個格式（`t,price,size,bid,ask`），只往後接、不回頭改。
"""

from __future__ import annotations

import importlib
import os
import time as time_module
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any, Protocol

from autotrade.feed.recorded import HEADER
from autotrade.jobs import HEADLINE
from autotrade.model import TAIPEI

MONTH_CODE = "ABCDEFGHIJKL"  # 富邦的月份代碼：A＝1 月 … L＝12 月
# ctid：同一個時間有好幾筆時，照寫進資料庫的先後排，每次查到的順序才會一樣
TICKS_SQL = (
    "SELECT to_char(t AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD\"T\"HH24:MI:SS.US') || '+08:00', "
    "price, size, bid, ask FROM market_live.ticks "
    "WHERE day = %s AND symbol = %s AND t >= %s::timestamptz ORDER BY t, ctid"
)
Row = tuple[str, float, int, float | None, float | None]


class TickDbError(RuntimeError):
    """讀不到資料庫裡的逐筆（連不上、逾時、查詢失敗）。"""


def third_wednesday(year: int, month: int) -> date:
    """那個月的第三個星期三（台指期的結算日）。"""
    first = date(year, month, 1)
    return first + timedelta(days=(2 - first.weekday()) % 7 + 14)


def fubon_symbol(product: str, day: date) -> str:
    """那一天主力月份的富邦代碼，例：TMF、2026-11-13 → TMFK6。

    跟錄製程式同一個規則：結算日（第三個星期三）當天起改用下個月——結算日近月 13:30 就收。
    """
    year, month = day.year, day.month
    if day >= third_wednesday(year, month):
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return f"{product}{MONTH_CODE[month - 1]}{year % 10}"


class TickDb(Protocol):
    """行情中繼看得到的資料庫：只有一個查詢。"""

    def fetch(self, day: date, symbol: str, since: str) -> list[Row]:
        """那一天、那個代碼、時間不早於 since 的逐筆，照時間排。讀不到丟 TickDbError。"""
        ...


class PgTickDb:
    """用 psycopg 連資料庫。唯讀；連線斷了下一次重連。"""

    def __init__(self, connect: Callable[[], Any], errors: tuple[type[BaseException], ...]) -> None:
        self._connect = connect
        self._errors = errors
        self._conn: Any = None

    def fetch(self, day: date, symbol: str, since: str) -> list[Row]:
        """查一次。"""
        try:
            if self._conn is None:
                self._conn = self._connect()
            rows = self._conn.execute(TICKS_SQL, (day, symbol, since)).fetchall()
        except self._errors as exc:
            conn, self._conn = self._conn, None
            if conn is not None:
                conn.close()
            raise TickDbError(str(exc).strip() or type(exc).__name__) from exc
        return [(str(t), float(p), int(s), b, a) for t, p, s, b, a in rows]


def open_tick_db(dsn: str, timeout_s: float) -> PgTickDb:
    """用連線字串建立唯讀連線。需要 psycopg（pip install "autotrade[web]"）。"""
    psycopg = importlib.import_module("psycopg")

    def connect() -> Any:
        conn = psycopg.connect(
            dsn, autocommit=True, prepare_threshold=None, connect_timeout=max(1, int(timeout_s))
        )
        conn.read_only = True
        return conn

    return PgTickDb(connect, errors=(psycopg.Error, OSError))


def _num(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if value == int(value) else repr(float(value))


@dataclass(frozen=True)
class TickRelayReport:
    """行情中繼跑一輪的結果。"""

    ok: bool
    written: int
    error: str | None


@dataclass
class TickRelay:
    """資料庫 → 檔案。記得「寫到哪個時間、那個時間寫了幾筆」，下一輪從那裡接下去。"""

    db: TickDb
    day: date
    symbol: str
    out: Path
    total: int = 0
    _since: str | None = None  # 最後寫出去那一筆的時間
    _at_since: int = 0  # 那個時間已經寫了幾筆

    def resume(self) -> int:
        """接著既有的檔案寫（程式重新啟動時）：讀出已經寫到哪裡。沒有檔案就建一個只有欄位名稱的。回傳已有幾筆。"""
        if not self.out.exists():
            self.out.write_text(",".join(HEADER) + "\n", encoding="utf-8", newline="\n")
            return 0
        raw = self.out.read_bytes()
        if raw and not raw.endswith(b"\n"):
            # 上次寫到一半就斷掉的殘行：補一個換行會變成壞資料，所以直接拒絕，請人看過
            raise ValueError(f"{self.out} 最後一行沒有寫完，請先檢查這個檔案")
        lines = raw.decode("utf-8").split("\n")[:-1]
        if not lines or lines[0].lstrip("﻿").split(",") != HEADER:
            raise ValueError(f"{self.out} 的欄位不是 {HEADER}，不是行情中繼寫的檔案")
        times = [line.split(",", 1)[0] for line in lines[1:]]
        self.total = len(times)
        if times:
            self._since = times[-1]
            self._at_since = sum(1 for t in times if t == times[-1])
        return self.total

    def step(self) -> TickRelayReport:
        """查一次、把新的接到檔案後面。資料庫出錯不丟例外：回報在結果裡，下一輪再試。"""
        since = self._since or f"{self.day.isoformat()}T00:00:00+08:00"
        try:
            rows = self.db.fetch(self.day, self.symbol, since)
        except TickDbError as exc:
            return TickRelayReport(False, 0, str(exc))
        skip = 0
        if self._since is not None:
            # 同一個時間的前幾筆上次已經寫過了
            while skip < len(rows) and skip < self._at_since and rows[skip][0] == self._since:
                skip += 1
        new = rows[skip:]
        if not new:
            return TickRelayReport(True, 0, None)
        text = "".join(f"{t},{_num(p)},{s},{_num(b)},{_num(a)}\n" for t, p, s, b, a in new)
        with self.out.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        last = new[-1][0]
        same = sum(1 for row in new if row[0] == last)
        self._at_since = self._at_since + same if last == self._since else same
        self._since = last
        self.total += len(new)
        return TickRelayReport(True, len(new), None)


@dataclass(frozen=True)
class RecordCheck:
    """檢查今天的行情有沒有漏錄（A-024）要用的東西：盤的起訖與兩個門檻（從設定檔來）。"""

    start: time
    end: time
    late_s: float
    gap_s: float

    def due(self, day: date) -> list[datetime]:
        """哪些時間點要檢查：開盤後 late_s、收盤後 late_s。週末不檢查。"""
        if day.weekday() >= 5:
            return []
        late = timedelta(seconds=self.late_s)
        return [datetime.combine(day, t, TAIPEI) + late for t in (self.start, self.end)]


def recording_note(times: Sequence[datetime], now: datetime, check: RecordCheck) -> str:
    """今天到現在的行情有沒有漏：盤中一筆都沒有、開頭晚了、中間斷過、太早停。有問題的話開頭是 ⚠。"""
    day = now.date()
    open_at = datetime.combine(day, check.start, TAIPEI)
    close_at = datetime.combine(day, check.end, TAIPEI)
    until = min(now, close_at)
    inside = [t for t in times if open_at <= t <= close_at]
    if not inside:
        return (
            f"⚠ 今天的行情可能整段漏錄：{open_at:%H:%M} 開盤到 {until:%H:%M:%S} 資料庫裡一筆都沒有。"
            "行情錄製程式有在跑嗎？本機資料庫（Docker）有開嗎？今天休市的話不用理會。"
        )
    first, last = inside[0], inside[-1]
    problems: list[str] = []
    if (first - open_at).total_seconds() > check.late_s:
        problems.append(f"開頭缺 {open_at:%H:%M:%S}～{first:%H:%M:%S}")
    gaps = [(a, b) for a, b in pairwise(inside) if (b - a).total_seconds() > check.gap_s]
    if gaps:
        a, b = max(gaps, key=lambda g: g[1] - g[0])
        problems.append(
            f"中間有 {len(gaps)} 段超過 {check.gap_s:g} 秒沒有行情（最長 {a:%H:%M:%S}～{b:%H:%M:%S}）"
        )
    if (until - last).total_seconds() > check.gap_s:
        problems.append(f"行情停在 {last:%H:%M:%S}（檢查到 {until:%H:%M:%S}）")
    if problems:
        return (
            "⚠ 今天的行情可能有漏錄：" + "；".join(problems) + "。錄製的原始檔還在的話可以補回來。"
        )
    return (
        f"今天的行情檢查過了：{first:%H:%M:%S}～{last:%H:%M:%S} 共 {len(inside)} 筆，沒有發現漏錄。"
    )


def file_times(path: Path) -> list[datetime]:
    """行情中繼寫的檔裡每一筆的時間。"""
    with path.open(encoding="utf-8") as fh:
        next(fh, None)  # 欄位名稱
        return [datetime.fromisoformat(line.split(",", 1)[0]) for line in fh if line.strip()]


def run(
    relay: TickRelay,
    poll_s: float,
    *,
    once: bool = False,
    check: RecordCheck | None = None,
    report_s: float = 60.0,
    log: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time_module.sleep,
    clock: Callable[[], datetime] = lambda: datetime.now(TAIPEI),
) -> None:
    """一直跑到被中斷（Ctrl+C）。只在「好 ↔ 壞」改變、第一次有行情、以及每隔 report_s 秒印一行，不洗版。"""
    had = relay.resume()
    log(
        f"行情中繼開始：{relay.day} {relay.symbol} → {relay.out}"
        + (f"（接著既有的 {had} 筆）" if had else "")
    )
    last_ok: bool | None = None
    next_report = clock() + timedelta(seconds=report_s)
    pending = [] if check is None else check.due(relay.day)
    blind = False
    while True:
        before = relay.total
        report = relay.step()
        now = clock()
        if report.ok != last_ok:
            state = "資料庫讀得到" if report.ok else f"讀不到資料庫（會一直再試）：{report.error}"
            log(f"{now:%H:%M:%S} {state}")
        if before == 0 and relay.total > 0:
            log(f"{now:%H:%M:%S} 開始有行情了：先接上 {relay.total} 筆")
        elif now >= next_report:
            log(f"{now:%H:%M:%S} 已接 {relay.total} 筆")
        if now >= next_report:
            next_report = now + timedelta(seconds=report_s)
        if check is not None and pending and now >= pending[0] and now.date() == relay.day:
            if report.ok:
                pending = [at for at in pending if at > now]
                note = recording_note(file_times(relay.out), now, check)
                log(f"{now:%H:%M:%S} {HEADLINE} {note}")
            elif not blind:
                blind = True  # 只講一次；資料庫讀得到之後照樣補做檢查
                log(
                    f"{now:%H:%M:%S} {HEADLINE} ⚠ 讀不到行情資料庫，沒辦法檢查今天有沒有漏錄，"
                    "而且這段時間的行情很可能正在漏：本機資料庫（Docker）有開嗎？"
                )
        last_ok = report.ok
        if once:
            return
        sleep(poll_s)
