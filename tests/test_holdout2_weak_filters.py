"""D-184：破底排除與大盤緩跌閘門登記進第二保留期，開啟日之前不得被改動；每一臂只跟對照組差一件事。"""

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
from twquant.strategy.config import MarketGateConfig, StrategyConfig, load_strategy

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"
# D-124 §三 b1 的參數（scripts/s2_market_diagnostics.py 的 REGIME）——閘門逐字沿用，不在 D-184 新訂
_D124 = {
    "index_id": "TAIEX",
    "ma_window": 60,
    "vol_window": 20,
    "vol_lookback": 250,
    "vol_pctl": 0.90,
}


def _reg() -> HoldoutRegistration:
    return load_config(_CONFIG_DIR / "holdout2_weak_filters.yaml", HoldoutRegistration)


def _load(name: str) -> StrategyConfig:
    return load_strategy(str(_CONFIG_DIR / "strategies" / f"{name}.yaml"))


def test_d184_registered_strategies_are_unchanged() -> None:
    reg = _reg()
    assert [e.code for e in reg.entries] == [
        "QUIET-S0",
        "QUIET-NOLOW20",
        "QUIET-GATE",
        "E0-CTRL",
        "E0-GATE",
    ]
    for entry in reg.entries:
        cfg = _load(entry.strategy)
        assert config_fingerprint(cfg) == entry.strategy_fingerprint, (
            f"{entry.code}：configs/strategies/{entry.strategy}.yaml 在登記（D-184）之後被改動了。"
            "改動等於換了一個東西——請另立裁決與登記。"
        )


def _without(cfg: StrategyConfig, *drop: str) -> dict[str, object]:
    d = cfg.model_dump()
    for key in ("name", "description", "trial_scope", *drop):
        d.pop(key, None)
    return d


def test_d184_quiet_arms_differ_from_s0_by_one_thing() -> None:
    s0 = _load("quiet_s0")
    nolow = _load("quiet_nolow20")
    assert nolow.slots is not None and s0.slots is not None
    assert [(r.factor, r.min_value, r.params) for r in nolow.slots.exclude] == [
        ("price.at_low_20", 1.0, {})
    ]
    a, b = _without(nolow), _without(s0)
    a["slots"]["exclude"] = []  # type: ignore[index]
    assert a == b
    gate = _load("quiet_grind_gate")
    assert gate.market_gate == MarketGateConfig.model_validate(_D124)
    assert _without(gate, "market_gate") == _without(s0, "market_gate")


def test_d184_e0_arm_differs_from_ctrl_only_by_the_gate() -> None:
    ctrl = _load("lw_e0_ctrl")
    gate = _load("lw_e0_grind_gate")
    assert ctrl.market_gate is None and gate.market_gate == MarketGateConfig.model_validate(_D124)
    assert _without(gate, "market_gate") == _without(ctrl, "market_gate")


def test_d184_costs_models_and_boundary() -> None:
    reg = _reg()
    fp = config_fingerprint(load_config(_CONFIG_DIR / "costs.yaml", CostConfig))
    assert all(e.costs_fingerprint == fp for e in reg.entries)
    for e in reg.entries:
        cfg = _load(e.strategy)
        ids = {str(s.params.get("model_id")) for s in cfg.signals if "model_id" in s.params}
        ids |= {str(p.params.get("model_id")) for p in cfg.prefilter if "model_id" in p.params}
        assert ids == {e.model_id}, e.code
    assert reg.registration == "D-184" and reg.opens_on == dt.date(2027, 3, 1)
    assert reg.holdout_start == dt.date(2026, 9, 9)
    with pytest.raises(PermissionError):
        refuse_before_opening(reg, dt.date(2026, 10, 15), 20)


def test_d184_gate_field_is_inert_for_earlier_registrations() -> None:
    """新增的 market_gate 欄位預設 None：不設的策略不輸出這個鍵，指紋與 params_hash 都不變。"""
    s0 = _load("quiet_s0")
    assert s0.market_gate is None and "market_gate" not in s0.model_dump(mode="json")
    with_none = StrategyConfig.model_validate({**s0.model_dump(), "market_gate": None})
    assert config_fingerprint(with_none) == config_fingerprint(s0)
    gated = StrategyConfig.model_validate({**s0.model_dump(), "market_gate": _D124})
    assert config_fingerprint(gated) != config_fingerprint(s0)
    with pytest.raises(Exception, match="vol_pctl"):
        StrategyConfig.model_validate(
            {**s0.model_dump(), "market_gate": {**_D124, "vol_pctl": 1.5}}
        )
