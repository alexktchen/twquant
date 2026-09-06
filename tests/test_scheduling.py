"""T-073：GitHub scheduled run 延遲時的最近預定班次解析（SPEC-012）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from twquant.conventions import TZ
from twquant.scheduling import latest_scheduled_day, parse_slot

_ROOT = Path(__file__).resolve().parents[1]
_SLOTS = (dt.time(19, 23), dt.time(22, 43))


def _tw(year: int, month: int, day: int, hour: int, minute: int) -> dt.datetime:
    return dt.datetime(year, month, day, hour, minute, tzinfo=TZ)


def test_ac1_friday_early_morning_maps_to_thursday_slot() -> None:
    assert latest_scheduled_day(_tw(2026, 8, 28, 4, 58), _SLOTS) == dt.date(2026, 8, 27)


def test_ac2_saturday_delayed_run_maps_to_friday_slot() -> None:
    assert latest_scheduled_day(_tw(2026, 8, 29, 7, 31), _SLOTS) == dt.date(2026, 8, 28)


def test_ac3_monday_before_and_after_first_slot() -> None:
    assert latest_scheduled_day(_tw(2026, 8, 31, 18, 59), _SLOTS) == dt.date(2026, 8, 28)
    assert latest_scheduled_day(_tw(2026, 8, 31, 19, 24), _SLOTS) == dt.date(2026, 8, 31)


def test_ac4_invalid_inputs_raise_and_output_is_deterministic() -> None:
    aware = _tw(2026, 8, 31, 19, 24)
    with pytest.raises(ValueError, match="tz-aware"):
        latest_scheduled_day(dt.datetime(2026, 8, 31, 19, 24), _SLOTS)
    with pytest.raises(ValueError, match="slots"):
        latest_scheduled_day(aware, ())
    with pytest.raises(ValueError, match="timezone"):
        latest_scheduled_day(aware, (dt.time(19, 23, tzinfo=dt.UTC),))
    with pytest.raises(ValueError, match="lookback_days"):
        latest_scheduled_day(aware, _SLOTS, lookback_days=0)
    with pytest.raises(ValueError, match="找不到"):
        latest_scheduled_day(_tw(2026, 8, 31, 18, 59), _SLOTS, lookback_days=1)
    assert latest_scheduled_day(aware, _SLOTS) == latest_scheduled_day(aware, _SLOTS)
    assert parse_slot("19:23") == dt.time(19, 23)
    with pytest.raises(ValueError, match="HH:MM"):
        parse_slot("19")


def test_ac5_workflow_uses_off_peak_cron_and_resolver() -> None:
    text = (_ROOT / ".github" / "workflows" / "daily-pipeline.yml").read_text(encoding="utf-8")
    assert 'cron: "23 11 * * 1-5"' in text
    assert 'cron: "43 14 * * 1-5"' in text
    assert "scripts/resolve_pipeline_day.py" in text
    assert '--slot "19:23" --slot "22:43"' in text
    assert 'DAY="${{ inputs.date }}"' in text
