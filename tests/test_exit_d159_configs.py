"""T-314／D-159 §四：第十五輪兩個組合臂與第十三輪 S0 只差出場規則。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from twquant.strategy.config import StrategyConfig

ROOT = Path(__file__).resolve().parents[1] / "configs" / "strategies"
ONLY_DIFFERENT = {"name", "description", "trial_scope", "exit_rules"}


def _load(name: str) -> StrategyConfig:
    return StrategyConfig.model_validate(yaml.safe_load((ROOT / f"{name}.yaml").read_text("utf-8")))


@pytest.mark.parametrize("arm", ["quiet_t_ma", "quiet_t_atr"])
def test_ac6_arms_differ_from_s0_only_in_exits(arm: str) -> None:
    s0 = _load("quiet_s0").model_dump(mode="json")
    t = _load(arm).model_dump(mode="json")
    diff = {k for k in s0.keys() | t.keys() if s0.get(k) != t.get(k)}
    assert diff <= ONLY_DIFFERENT
    assert "exit_rules" in diff


@pytest.mark.parametrize(
    ("arm", "lock"),
    [
        ("quiet_t_ma", {"kind": "ma_lock", "window": 20}),
        ("quiet_t_atr", {"kind": "atr_trailing", "window": 14, "mult": 3.0}),
    ],
)
def test_ac6_arms_carry_the_registered_parameters(arm: str, lock: dict[str, object]) -> None:
    """D-159 §二：下軌（20、2σ）排第一、鎖利線其次、最長 120 個交易日——參數寫死。"""
    rules = [
        {k: v for k, v in r.model_dump().items() if v not in (None, {})}
        for r in _load(arm).exit_rules
    ]
    assert rules == [
        {"kind": "bb_lower_stop", "window": 20, "mult": 2.0},
        lock,
        {"kind": "time_stop", "sessions": 120},
    ]
