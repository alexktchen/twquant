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


@pytest.mark.parametrize("arm", ["quiet_t_ma", "quiet_t_atr", "quiet_t_atr_f15"])
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


def test_ac6_f15_arm_swaps_only_the_band_for_a_fixed_stop() -> None:
    """D-161 §一：T-ATR-F15＝T-ATR 把布林下軌換成固定 −15% 停損，其餘出場逐字相同。"""
    rules = [
        {k: v for k, v in r.model_dump().items() if v not in (None, {})}
        for r in _load("quiet_t_atr_f15").exit_rules
    ]
    assert rules == [
        {"kind": "stop_loss", "pct": 0.15},
        {"kind": "atr_trailing", "window": 14, "mult": 3.0},
        {"kind": "time_stop", "sessions": 120},
    ]


@pytest.mark.parametrize("arm", ["quiet_s0", "quiet_t_atr", "quiet_t_atr_f15"])
def test_ac1_d162_posthoc_arms_only_swap_the_model(arm: str) -> None:
    """D-162 §二：事後檢驗的三個配置只換 model_id（重訓到 2026-02-26）與 name。"""
    base = _load(arm).model_dump(mode="json")
    new = _load(f"{arm}_2602").model_dump(mode="json")
    assert new["name"] == f"{arm}_2602"
    assert new["signals"][0]["params"]["model_id"] == "lgbm_quietA20_tu_h20_20260226_0ac86788"
    new["signals"][0]["params"]["model_id"] = base["signals"][0]["params"]["model_id"]
    new["name"] = base["name"]
    assert new == base
