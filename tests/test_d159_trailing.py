"""T-315／D-159：逐筆腳本的純函數（出場日、鎖利線、主力成本、階梯停利）。

腳本要接 PG 才能跑；這裡只守規則本身，確保跑出來的數字就是登記寫的那條規則。
"""

from __future__ import annotations

import numpy as np
import pytest
from scripts.d159_trailing_exits import (
    atr_line,
    broker_cost,
    exit_day,
    lock_trigger,
    step_floor,
)

nan = np.nan


def test_ac1_exit_day_is_next_open_after_the_first_trigger() -> None:
    trig = np.array([[False, True, True], [False, False, False]])
    got = exit_day(trig, last=np.array([121, 121]), cap=121)
    assert got.tolist() == [2, 121]  # 第 1 天收盤觸發 → 第 2 天開盤；沒觸發 → 最長


def test_ac1_exit_day_respects_cap_and_missing_data() -> None:
    trig = np.array([[False] * 5, [False, False, False, False, True]])
    assert exit_day(trig, last=np.array([3, 121]), cap=4).tolist() == [3, 4]


def test_ac2_lock_only_when_line_is_above_entry() -> None:
    close = np.array([[105.0, 99.0], [105.0, 99.0]])
    line = np.array([[nan, 100.0], [nan, 99.5]])
    entry = np.array([100.0, 101.0])
    # 第一筆：線 100 不高於買進價 100 → 不觸發；第二筆：線 99.5 < 買進價 101 → 也不觸發
    assert not lock_trigger(close, line, entry).any()
    assert lock_trigger(close, line, np.array([95.0, 95.0]))[:, 1].tolist() == [True, True]


def test_ac3_atr_line_follows_the_running_high() -> None:
    close = np.array([[100.0, 120.0, nan, 110.0]])
    atr = np.array([[5.0, 5.0, 5.0, 5.0]])
    assert atr_line(close, atr, 3.0).tolist() == [[85.0, 105.0, 105.0, 105.0]]


def test_ac4_broker_cost_weights_positive_net_buys_by_vwap() -> None:
    net = np.array([[100.0, -50.0, 300.0], [-1.0, nan, -2.0]])
    vwap = np.array([[10.0, 99.0, 20.0], [10.0, 10.0, 10.0]])
    got = broker_cost(net, vwap)
    assert got[0] == pytest.approx((100 * 10 + 300 * 20) / 400)
    assert np.isnan(got[1])  # 沒有一天淨買超 > 0 → 沒有成本


def test_ac5_step_floor_moves_up_one_target_behind() -> None:
    close = np.array([[140.0, 150.0, 190.0, 200.0, 180.0, 260.0]])
    floor = step_floor(close, np.array([100.0]), 0.5)
    # 150 到 1.5C → 線 = C；200 到 2C → 線 = 1.5C；260 到 2.5C → 線 = 2C
    assert np.isnan(floor[0, 0])
    assert floor[0, 1:].tolist() == [100.0, 100.0, 150.0, 150.0, 200.0]


def test_ac5_step_floor_without_cost_is_inactive() -> None:
    assert np.isnan(step_floor(np.array([[500.0]]), np.array([nan]), 0.5)).all()
