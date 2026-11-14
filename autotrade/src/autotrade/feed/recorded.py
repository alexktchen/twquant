"""用富邦即時錄下來的逐筆重播一天（A-013）。跟期交所盤後檔不同：每一筆都有當下的買價、賣價。

檔案是 `tools/export_recorded_ticks.py` 從錄製程式的資料庫匯出來的 CSV，欄位：`t,price,size,bid,ask`。
`t` 帶時區（+08:00）；`bid`／`ask` 可以是空的（那一筆沒有報價）。口數照錄製原值。
盤中跟著讀的版本在 `feed/follow.py`（A-014），共用這裡的每一列怎麼讀。
"""

from __future__ import annotations

import csv
from datetime import date, datetime, time
from pathlib import Path

from autotrade.feed.replay import FeedError
from autotrade.model import Tick, require_taipei

HEADER = ["t", "price", "size", "bid", "ask"]


def _price(text: str, what: str, line: int, path: Path) -> float | None:
    if text.strip() == "":
        return None
    try:
        value = float(text)
    except ValueError as exc:
        raise FeedError(f"{path} 第 {line} 列的 {what} 不是數字：{text!r}") from exc
    if value <= 0:
        raise FeedError(f"{path} 第 {line} 列的 {what} 要大於 0：{value}")
    return value


def row_time(row: list[str], line: int, path: Path, last: datetime | None) -> datetime:
    """一列的時間：欄位數要對、要帶時區、不能比前一列早。壞資料就 raise。"""
    if len(row) != len(HEADER):
        raise FeedError(f"{path} 第 {line} 列欄位數不對：{row!r}")
    try:
        ts = require_taipei(datetime.fromisoformat(row[0]), f"{path} 第 {line} 列的時間")
    except ValueError as exc:
        raise FeedError(f"{path} 第 {line} 列的時間不對：{row[0]!r}（{exc}）") from exc
    if last is not None and ts < last:
        raise FeedError(f"{path} 第 {line} 列的時間比前一列早：{ts} < {last}")
    return ts


def row_tick(ts: datetime, row: list[str], line: int, path: Path) -> Tick:
    """一列的價、量、買賣價 → 一筆逐筆。"""
    price = _price(row[1], "成交價", line, path)
    if price is None:
        raise FeedError(f"{path} 第 {line} 列沒有成交價")
    try:
        qty = int(row[2])
    except ValueError as exc:
        raise FeedError(f"{path} 第 {line} 列的口數不是整數：{row[2]!r}") from exc
    if qty <= 0:
        raise FeedError(f"{path} 第 {line} 列的口數要大於 0：{qty}")
    bid = _price(row[3], "買價", line, path)
    ask = _price(row[4], "賣價", line, path)
    return Tick(ts=ts, price=price, qty=qty, bid=bid, ask=ask)


def read_recorded_day(path: Path, day: date, start: time, end: time) -> list[Tick]:
    """讀一天的錄製逐筆（只留 start～end 之間、那一天的）。時間要遞增，壞資料就 raise，不跳過。"""
    rows: list[Tick] = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header != HEADER:
            raise FeedError(f"{path} 的欄位要是 {HEADER}，拿到 {header}")
        last: datetime | None = None
        for line, row in enumerate(reader, start=2):
            last = ts = row_time(row, line, path, last)
            if ts.date() != day or not start <= ts.time() <= end:
                continue
            rows.append(row_tick(ts, row, line, path))
    if not rows:
        raise FeedError(f"{path} 裡沒有 {day} {start}～{end} 的逐筆")
    return rows
