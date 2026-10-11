"""D-172：潛伏飆股分數門檻臂（＋ETF 資金流排除）登記進第二保留期，開啟日之前不得被改動。

與 tests/test_holdout2_registration.py 同一套守門，對象是 configs/holdout2_quiet_threshold.yaml。
"""

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
from twquant.strategy.config import load_strategy

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"
_ARMS = (
    "quiet_p30",
    "quiet_p30x",
    "quiet_p35",
    "quiet_p35x",
    "quiet_p40",
    "quiet_p40x",
    "quiet_p45",
    "quiet_p45x",
    "quiet_p50",
    "quiet_p50x",
)


def _reg() -> HoldoutRegistration:
    return load_config(_CONFIG_DIR / "holdout2_quiet_threshold.yaml", HoldoutRegistration)


def test_d172_registered_strategies_are_unchanged() -> None:
    for entry in _reg().entries:
        cfg = load_strategy(str(_CONFIG_DIR / "strategies" / f"{entry.strategy}.yaml"))
        assert config_fingerprint(cfg) == entry.strategy_fingerprint, (
            f"{entry.code}：configs/strategies/{entry.strategy}.yaml 在登記（D-172）之後被改動了。"
            "改動等於換了一個東西——請另立裁決與登記。"
        )


def test_d172_arms_differ_from_s0_only_in_threshold_and_exclusion() -> None:
    s0 = load_strategy(str(_CONFIG_DIR / "strategies" / "quiet_s0.yaml"))
    base = s0.model_dump()
    for name in _ARMS:
        cfg = load_strategy(str(_CONFIG_DIR / "strategies" / f"{name}.yaml"))
        d = cfg.model_dump()
        th = int(name[7:9]) / 100
        assert cfg.slots is not None and cfg.slots.entry_threshold == th, name
        assert bool(cfg.slots.exclude) == name.endswith("x"), name
        if cfg.slots.exclude:
            assert [r.factor for r in cfg.slots.exclude] == ["chip.etf_flow_wave"]
        for key in ("universe", "signals", "exit_rules", "overlays"):
            assert d[key] == base[key], f"{name}.{key} 與 quiet_s0 不同"
        assert d["slots"]["n"] == base["slots"]["n"]


def test_d172_registry_covers_s0_and_all_arms() -> None:
    reg = _reg()
    assert reg.registration == "D-172"
    assert {e.strategy for e in reg.entries} == {"quiet_s0", *_ARMS}
    model_ids = {e.model_id for e in reg.entries}
    assert model_ids == {"lgbm_quietA20_tu_h20_20260430_655df6d7"}
    assert len({e.capital_twd for e in reg.entries}) == 1


def test_d172_costs_and_boundary_match_d109() -> None:
    reg = _reg()
    fp = config_fingerprint(load_config(_CONFIG_DIR / "costs.yaml", CostConfig))
    assert all(e.costs_fingerprint == fp for e in reg.entries)
    assert reg.holdout_start == dt.date(2026, 9, 9) and reg.opens_on == dt.date(2027, 3, 1)
    with pytest.raises(PermissionError):
        refuse_before_opening(reg, dt.date(2026, 10, 11), 20)


def test_d172_empty_exclude_is_inert_but_a_rule_changes_the_fingerprint() -> None:
    """D-172 在 schema 加了 slots.exclude：沒設（空清單）不得改變既有登記的指紋，設了就必須改變。"""
    s0 = load_strategy(str(_CONFIG_DIR / "strategies" / "quiet_s0.yaml"))
    p40 = load_strategy(str(_CONFIG_DIR / "strategies" / "quiet_p40.yaml"))
    p40x = load_strategy(str(_CONFIG_DIR / "strategies" / "quiet_p40x.yaml"))
    assert s0.slots is not None and s0.slots.exclude == []
    assert config_fingerprint(p40) != config_fingerprint(p40x)
    assert config_fingerprint(p40) != config_fingerprint(s0)
