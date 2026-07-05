"""CLI 純邏輯測試（排程時間計算）。"""

from __future__ import annotations

import datetime as dt

from twquant.cli import _seconds_until
from twquant.conventions import TZ


def test_seconds_until_later_today() -> None:
    now = dt.datetime(2026, 7, 5, 9, 0, tzinfo=TZ)
    assert _seconds_until(now, 16, 40) == (16 - 9) * 3600 + 40 * 60


def test_seconds_until_wraps_to_tomorrow() -> None:
    now = dt.datetime(2026, 7, 5, 20, 0, tzinfo=TZ)
    # 20:00 已過 16:40 → 明日 16:40
    delay = _seconds_until(now, 16, 40)
    assert delay == (24 - 20 + 16) * 3600 + 40 * 60


def test_seconds_until_positive() -> None:
    now = dt.datetime(2026, 7, 5, 16, 40, 0, tzinfo=TZ)
    # 剛好等於觸發時刻 → 視為已過，算明日（>0）
    assert _seconds_until(now, 16, 40) == 24 * 3600
