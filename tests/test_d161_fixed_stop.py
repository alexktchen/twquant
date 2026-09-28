"""T-316／D-161：固定 −15% 停損的觸發規則。"""

from __future__ import annotations

import numpy as np
from scripts.d159_trailing_exits import exit_day
from scripts.d161_atr_fixed_stop import FIXED_STOP, fixed_stop_trigger

nan = np.nan


def test_ac1_parameter_is_the_registered_fifteen_percent() -> None:
    assert FIXED_STOP == 0.15


def test_ac1_fires_at_or_below_entry_times_085_and_never_on_nan() -> None:
    close = np.array([[90.0, 85.0, 80.0], [nan, 84.9, 100.0]])
    got = fixed_stop_trigger(close, np.array([100.0, 100.0]), 0.15)
    assert got.tolist() == [[False, True, True], [False, True, False]]


def test_ac1_exit_is_next_open_after_the_stop_closes() -> None:
    close = np.array([[99.0, 86.0, 84.0, 70.0]])
    trig = fixed_stop_trigger(close, np.array([100.0]), 0.15)
    assert exit_day(trig, last=np.array([121]), cap=121).tolist() == [3]
