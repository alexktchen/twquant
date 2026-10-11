"""D-174：第三條線「潛伏飆股・高分爆量」登記進第二保留期，開啟日之前不得被改動；必要條件的語意。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from twquant.backtest.costs import CostConfig
from twquant.config import load_config
from twquant.reporting.holdout import (
    HoldoutRegistration,
    config_fingerprint,
    refuse_before_opening,
)
from twquant.strategy.config import StrategyConfig, load_strategy

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"


def _reg() -> HoldoutRegistration:
    return load_config(_CONFIG_DIR / "holdout2_quiet_volume.yaml", HoldoutRegistration)


def test_d174_registered_strategy_is_unchanged() -> None:
    for entry in _reg().entries:
        cfg = load_strategy(str(_CONFIG_DIR / "strategies" / f"{entry.strategy}.yaml"))
        assert config_fingerprint(cfg) == entry.strategy_fingerprint, (
            f"{entry.code}：configs/strategies/{entry.strategy}.yaml 在登記（D-174）之後被改動了。"
        )


def test_d174_differs_from_p35_only_by_the_volume_requirement() -> None:
    p35 = load_strategy(str(_CONFIG_DIR / "strategies" / "quiet_p35.yaml")).model_dump()
    v = load_strategy(str(_CONFIG_DIR / "strategies" / "quiet_p35v.yaml"))
    d = v.model_dump()
    for key in ("universe", "signals", "exit_rules", "overlays", "entry_filters"):
        assert d[key] == p35[key], key
    assert v.slots is not None
    assert v.slots.entry_threshold == 0.35 and v.slots.exclude == []
    assert [(r.factor, r.min_value, r.params) for r in v.slots.require] == [
        ("volume.vol_ratio_5d", 1.5, {"window": 20})
    ]


def test_d174_costs_boundary_and_inert_field() -> None:
    reg = _reg()
    fp = config_fingerprint(load_config(_CONFIG_DIR / "costs.yaml", CostConfig))
    assert all(e.costs_fingerprint == fp for e in reg.entries)
    assert reg.registration == "D-174" and reg.opens_on == dt.date(2027, 3, 1)
    with pytest.raises(PermissionError):
        refuse_before_opening(reg, dt.date(2026, 10, 11), 20)
    p35 = load_strategy(str(_CONFIG_DIR / "strategies" / "quiet_p35.yaml"))
    assert p35.slots is not None and p35.slots.require == []  # 空清單不改既有登記的指紋


def test_d174_require_rule_validation() -> None:
    base = load_strategy(str(_CONFIG_DIR / "strategies" / "quiet_p35v.yaml")).model_dump()
    bad = {**base, "slots": {**base["slots"], "require": [{"factor": "volume.vol_ratio_5d"}]}}
    with pytest.raises(Exception, match="min_value"):
        StrategyConfig.model_validate(bad)
