"""排程日期解析純函數（SPEC-012）；不做 IO、不假裝判斷交易日。"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence

from twquant.conventions import TZ

_SLOT_RE = re.compile(r"^(?P<hour>[01][0-9]|2[0-3]):(?P<minute>[0-5][0-9])$")


def parse_slot(value: str) -> dt.time:
    """解析 24 小時制 HH:MM 班次；格式或範圍錯誤即 raise。"""
    match = _SLOT_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"slot 須為 HH:MM（24 小時制），得到 {value!r}")
    return dt.time(hour=int(match["hour"]), minute=int(match["minute"]))


def latest_scheduled_day(
    now: dt.datetime,
    slots: Sequence[dt.time],
    *,
    lookback_days: int = 7,
) -> dt.date:
    """回傳 Asia/Taipei 時區中，不晚於 now 的最近週一至週五班次日期。"""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"now 必須是 tz-aware datetime，得到 {now!r}")
    if not slots:
        raise ValueError("slots 不可為空")
    if lookback_days < 1:
        raise ValueError(f"lookback_days 須 >= 1，得到 {lookback_days}")
    if any(slot.tzinfo is not None for slot in slots):
        raise ValueError("slot 不得自帶 timezone；一律按 Asia/Taipei 解讀")

    local_now = now.astimezone(TZ)
    ordered_slots = tuple(sorted(set(slots), reverse=True))
    for offset in range(lookback_days):
        day = local_now.date() - dt.timedelta(days=offset)
        if day.isoweekday() > 5:  # 重建 workflow cron 1-5；不是交易日曆判斷
            continue
        for slot in ordered_slots:
            candidate = dt.datetime.combine(day, slot, tzinfo=TZ)
            if candidate <= local_now:
                return day
    raise ValueError(f"最近 {lookback_days} 日找不到不晚於 {local_now.isoformat()} 的預定班次")
