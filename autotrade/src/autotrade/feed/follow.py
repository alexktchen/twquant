"""盤中的行情來源（A-014）。交易程式不連資料庫，行情是另一支程序（行情中繼）接在一個檔案後面，這裡跟著讀。

- `FollowFile`：跟著一個一直在長大的檔案讀。格式跟 A-013 的錄製檔一樣（`t,price,size,bid,ask`），
  所以收盤後同一個檔可以直接拿去 `replay --feed recorded` 重算，兩邊要一樣。
- `PacedTicks`：回放用。把整天錄好的逐筆照時鐘一筆一筆放出來（時鐘可以是加速的假時鐘）。

兩個都只有一個動作：`poll(now)` 回傳這一次新到的逐筆（可以是空的）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Protocol

from autotrade.feed.recorded import HEADER, row_tick, row_time
from autotrade.feed.replay import FeedError
from autotrade.model import Tick

BOM = "﻿"


class TickSource(Protocol):
    """交易程式看得到的行情來源。poll 必須很快回來（只碰本機）。"""

    def poll(self, now: datetime) -> list[Tick]:
        """這一次新到的逐筆，照時間排好；沒有新的就回空的。壞資料丟 FeedError。"""
        ...


@dataclass
class FollowFile:
    """跟著一個一直在長大的逐筆檔讀。只讀寫完整的行（有換行結尾的）；只留那一天、交易時段內的。"""

    path: Path
    day: date
    start: time
    end: time
    _offset: int = 0  # 已經讀到檔案的第幾個位元組
    _line: int = 0
    _last: datetime | None = None

    def poll(self, now: datetime) -> list[Tick]:
        """讀上次之後多出來的完整行。檔案還沒出現＝還沒有行情；檔案變短或不見了就 raise。"""
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            if self._offset:
                raise FeedError(f"{self.path} 不見了（讀到一半被刪掉）") from None
            return []
        if size < self._offset:
            raise FeedError(f"{self.path} 變短了（{self._offset} → {size} 位元組），不是只往後接")
        if size == self._offset:
            return []
        with self.path.open("rb") as fh:
            fh.seek(self._offset)
            chunk = fh.read(size - self._offset)
        cut = chunk.rfind(b"\n")
        if cut < 0:
            return []  # 這一行還沒寫完
        self._offset += cut + 1
        ticks: list[Tick] = []
        for raw in chunk[: cut + 1].split(b"\n")[:-1]:
            self._line += 1
            try:
                text = raw.decode("utf-8").rstrip("\r")
            except UnicodeDecodeError as exc:
                raise FeedError(f"{self.path} 第 {self._line} 列不是 UTF-8：{exc}") from exc
            row = text.split(",")
            if self._line == 1:
                if [row[0].lstrip(BOM), *row[1:]] != HEADER:
                    raise FeedError(f"{self.path} 的欄位要是 {HEADER}，拿到 {row}")
                continue
            self._last = ts = row_time(row, self._line, self.path, self._last)
            if ts.date() != self.day or not self.start <= ts.time() <= self.end:
                continue
            ticks.append(row_tick(ts, row, self._line, self.path))
        return ticks


@dataclass
class PacedTicks:
    """回放：手上有整天的逐筆，時間到了才放出來。"""

    rows: list[Tick]
    _next: int = 0

    def poll(self, now: datetime) -> list[Tick]:
        """時間不晚於 now、還沒放過的逐筆。"""
        first = self._next
        while self._next < len(self.rows) and self.rows[self._next].ts <= now:
            self._next += 1
        return self.rows[first : self._next]

    @property
    def left(self) -> int:
        """還沒放出來的筆數。"""
        return len(self.rows) - self._next
