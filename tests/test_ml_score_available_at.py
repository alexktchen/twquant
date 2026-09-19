"""T-228：`ml_scores.available_at` 是資訊可得時刻，不是程式執行時刻。

2026-09-19 實際發生：分數寫的是 `now`，排程 22:00 跑或隔天補跑時就晚於當日 20:00 截點，
PitView 判定那筆分數「當時還不存在」，於是以 ML 分數當閘門的三支策略整欄 null、全部失敗。
"""

from __future__ import annotations

import datetime as dt

import pytest

from twquant.conventions import TZ, as_of_cutoff, ml_score_available_at
from twquant.errors import TwquantError

DAY = dt.date(2026, 9, 18)


def _at(day: dt.date, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def test_ac1_late_same_day_run_is_stamped_nominal() -> None:
    """排程 22:00 才跑：時間戳回到 19:00，當日 20:00 截點看得到。"""
    stamp = ml_score_available_at(DAY, _at(DAY, 22))
    assert stamp == _at(DAY, 19)
    assert stamp <= as_of_cutoff(DAY)


def test_ac1_next_day_catch_up_is_stamped_nominal() -> None:
    """隔天補跑（這次事故的情形）：同樣回到訊號日 19:00，不是補跑當下。"""
    stamp = ml_score_available_at(DAY, _at(DAY + dt.timedelta(days=1), 19, 20))
    assert stamp == _at(DAY, 19)


def test_ac2_early_run_is_not_pushed_later() -> None:
    """提早跑時不得把時間戳往後推——那才是真的前視。"""
    early = _at(DAY, 18, 30)
    assert ml_score_available_at(DAY, early) == early


def test_ac3_stamp_is_always_visible_at_the_signal_day_cutoff() -> None:
    """不論幾點跑，當日截點都看得到當日分數——回測與金庫驗證用的就是這個語意。"""
    for now in (_at(DAY, 9), _at(DAY, 19), _at(DAY, 20, 1), _at(DAY + dt.timedelta(days=30), 3)):
        assert ml_score_available_at(DAY, now) <= as_of_cutoff(DAY)


def test_ac4_naive_now_is_rejected() -> None:
    """黃金規則 2：naive datetime 一律拒絕。"""
    with pytest.raises(TwquantError):
        ml_score_available_at(DAY, dt.datetime(2026, 9, 18, 22, 0))
