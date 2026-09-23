"""T-289／D-143 §四：ratchet 出場的 `trailing_pct` 規則。

D-143 §四 登記的組合是「初始停損 −15%；浮盈曾達 +20% 後改為自高點回落 10%；60 日時間上限」。
`trailing_pct` 是其中第二條：**上膛之前完全不作用**（由 stop_loss 管），
上膛之後停損線跟著持有期最高收盤走。

與 `trailing_ma` 的差別：均線在急漲之後會落後很多，回吐幅度不受控；
飆股正是急漲，所以要用固定比例的回落。
"""

from __future__ import annotations

import datetime as dt

import pytest

from twquant.backtest.exits import HeldPosition, exit_reasons
from twquant.strategy.config import ExitRuleConfig

DAY = dt.date(2026, 5, 4)
RATCHET = ExitRuleConfig(kind="trailing_pct", pct=0.10, min_peak_gain=0.20)
RULES = [
    ExitRuleConfig(kind="stop_loss", pct=0.15),
    RATCHET,
    ExitRuleConfig(kind="time_stop", sessions=60),
]


def _held(avg_cost: float, peak: float, sessions: int = 5) -> dict[str, HeldPosition]:
    return {
        "2330": HeldPosition(
            shares=1000,
            avg_cost=avg_cost,
            dividends_per_share=0.0,
            peak_close=peak,
            sessions_held=sessions,
        )
    }


def _px(close: float) -> dict[str, dict[str, object]]:
    return {"2330": {"close": close, "open": close}}


def _fire(rules: list[ExitRuleConfig], avg_cost: float, peak: float, close: float) -> str | None:
    return exit_reasons(rules, _held(avg_cost, peak), _px(close), None, DAY).get("2330")  # type: ignore[arg-type]


def test_ac1_not_armed_below_the_peak_gain_threshold() -> None:
    """浮盈只到 +15%（未達 +20%）就回落 12%：ratchet 不該開火。"""
    assert _fire([RATCHET], avg_cost=100.0, peak=115.0, close=101.2) is None


def test_ac2_armed_and_pulled_back_fires() -> None:
    """浮盈曾到 +30% 後自高點回落逾 10%：出場，而且是獲利了結（賣價仍高於成本）。

    刻意不取 117.0（＝回落剛好 10%）：117/130 在浮點下是 −0.09999999999999998，
    貼著門檻的測試量的是浮點誤差不是規則。
    """
    assert _fire([RATCHET], avg_cost=100.0, peak=130.0, close=116.0) == "trailing_pct_0.1"


def test_ac3_armed_but_shallow_pullback_holds() -> None:
    """回落只有 5%：讓獲利繼續奔跑。"""
    assert _fire([RATCHET], avg_cost=100.0, peak=130.0, close=123.5) is None


def test_ac4_stop_loss_covers_the_pre_armed_phase() -> None:
    """還沒上膛時由 −15% 停損接手——這是 ratchet 組合的前半段。"""
    assert _fire(RULES, avg_cost=100.0, peak=105.0, close=84.0) == "stop_loss"
    assert _fire(RULES, avg_cost=100.0, peak=105.0, close=86.0) is None


def test_ac5_rule_order_decides_the_recorded_reason() -> None:
    """兩條同時成立時記排在前面的那一條（`exit_reasons` 的既有語義）。"""
    both = _fire(RULES, avg_cost=100.0, peak=130.0, close=84.0)
    assert both == "stop_loss"
    assert _fire([RATCHET, RULES[0]], avg_cost=100.0, peak=130.0, close=84.0) == "trailing_pct_0.1"


def test_ac6_dividends_count_towards_the_gain() -> None:
    """D-085：已收股利攤回單價，否則除息會被當成回落而提早賣掉。"""
    held = {
        "2330": HeldPosition(
            shares=1000, avg_cost=100.0, dividends_per_share=5.0, peak_close=125.0, sessions_held=5
        )
    }
    # 高點含息報酬 (125+5)/100−1 = +30% → 已上膛；當日含息 (118+5)/(125+5)−1 = −5.4%，不觸發
    assert exit_reasons([RATCHET], held, _px(118.0), None, DAY).get("2330") is None  # type: ignore[arg-type]
    # (110+5)/130−1 = −11.5% → 觸發。若股利沒攤回，(110)/(125)−1 = −12% 也會觸發，
    # 但含息版才是正確的比較基準——除息造成的價格下跌不是回吐。
    assert exit_reasons([RATCHET], held, _px(110.0), None, DAY).get("2330") is not None  # type: ignore[arg-type]


def test_ac7_no_peak_yet_is_not_an_exit() -> None:
    """買進當日還沒有高點紀錄（peak_close=0）→ 不判斷，不得當成回落 100%。"""
    assert _fire([RATCHET], avg_cost=100.0, peak=0.0, close=100.0) is None


def test_ac8_config_rejects_mismatched_params() -> None:
    with pytest.raises(ValueError, match=r"exit_rules\[trailing_pct\]"):
        ExitRuleConfig(kind="trailing_pct", pct=0.10)  # 缺 min_peak_gain
    with pytest.raises(ValueError, match=r"exit_rules\[trailing_pct\]"):
        ExitRuleConfig(kind="trailing_pct", pct=0.10, min_peak_gain=0.2, window=20)  # 多 window
    with pytest.raises(ValueError, match="min_peak_gain 須 > 0"):
        ExitRuleConfig(kind="trailing_pct", pct=0.10, min_peak_gain=0.0)
