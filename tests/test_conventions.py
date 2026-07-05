"""T-002 / SPEC-000 AC-1：時區與時間戳斷言。"""

from __future__ import annotations

import datetime as dt

import pytest

from twquant.conventions import (
    TZ,
    as_of_cutoff,
    assert_tw_timestamp,
    normalize_stock_id,
    now_tw,
)
from twquant.errors import TwquantError


def test_ac1_naive_datetime_raises() -> None:
    naive = dt.datetime(2026, 7, 5, 20, 0)  # 故意 naive 供測試
    with pytest.raises(TwquantError, match="tz-aware"):
        assert_tw_timestamp(naive)


def test_ac1_wrong_offset_raises() -> None:
    utc = dt.datetime(2026, 7, 5, 20, 0, tzinfo=dt.UTC)
    with pytest.raises(TwquantError, match="\\+08:00"):
        assert_tw_timestamp(utc)


def test_ac1_tw_timestamp_passes() -> None:
    ts = dt.datetime(2026, 7, 5, 20, 0, tzinfo=TZ)
    assert assert_tw_timestamp(ts) is ts
    assert assert_tw_timestamp(now_tw()) is not None


def test_as_of_cutoff_date_coerces_to_2000() -> None:
    cut = as_of_cutoff(dt.date(2026, 7, 5))
    assert cut.hour == 20 and cut.minute == 0
    assert cut.tzinfo == TZ


def test_normalize_stock_id_rejects_int() -> None:
    assert normalize_stock_id("0050") == "0050"  # 前導零保留
    with pytest.raises(TwquantError, match="前導零"):
        normalize_stock_id(50)
