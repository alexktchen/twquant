"""用期交所「每日逐筆成交」檔重播一天。

檔案欄位依序：成交日期、商品代號、到期月份(週別)、成交時間、成交價格、成交數量(B+S)……
成交數量照檔案原值（買＋賣合計，是實際口數的兩倍），不在這裡換算。
檔案的時間只到秒；同一秒內依檔案順序每筆加 1 微秒，保留先後。
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

from autotrade.model import TAIPEI, Tick

ENCODINGS = ("utf-8-sig", "big5", "cp950")
MIN_COLUMNS = 6


class FeedError(ValueError):
    """行情檔有問題。"""


def _decode(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise FeedError(f"{path} 無法用 {ENCODINGS} 解碼")


def read_taifex_day(
    path: Path, product: str, day: date, start: time, end: time
) -> tuple[str, list[Tick]]:
    """讀一個商品一天的逐筆，回傳（成交量最大的到期月份, 逐筆）。價差單不算。"""
    by_contract: dict[str, list[tuple[datetime, float, int]]] = defaultdict(list)
    wanted_day = day.strftime("%Y%m%d")
    for line_no, row in enumerate(csv.reader(_decode(path).splitlines()[1:]), start=2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) < MIN_COLUMNS:
            raise FeedError(f"{path} 第 {line_no} 列只有 {len(row)} 欄：{row!r}")
        if row[1].strip() != product or row[0].strip() != wanted_day:
            continue
        contract = row[2].strip()
        if "/" in contract:
            continue
        hhmmss = row[3].strip().zfill(6)
        try:
            clock = time(int(hhmmss[:2]), int(hhmmss[2:4]), int(hhmmss[4:6]))
            price, qty = float(row[4]), int(row[5])
        except ValueError as exc:
            raise FeedError(f"{path} 第 {line_no} 列的時間或價量讀不懂：{row!r}") from exc
        if not start <= clock <= end:
            continue
        by_contract[contract].append((datetime.combine(day, clock, tzinfo=TAIPEI), price, qty))
    if not by_contract:
        raise FeedError(f"{path} 裡沒有 {product} 在 {day} {start}～{end} 的逐筆")
    contract = max(by_contract, key=lambda c: sum(x[2] for x in by_contract[c]))
    per_second: Counter[datetime] = Counter()
    ticks = []
    for ts, price, qty in by_contract[contract]:
        ticks.append(Tick(ts=ts + timedelta(microseconds=per_second[ts]), price=price, qty=qty))
        per_second[ts] += 1
    return contract, ticks
