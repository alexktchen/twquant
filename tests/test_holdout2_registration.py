"""D-148：登記進第二保留期的東西，在開啟日之前不得被改動。

這是事前登記的機器化：看到答案之前把策略、成本、模型釘死。任何一項被改，
這裡就紅——要改，就是另一個東西，另立裁決與登記（見 configs/holdout2.yaml 表頭）。
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


def _reg() -> HoldoutRegistration:
    return load_config(_CONFIG_DIR / "holdout2.yaml", HoldoutRegistration)


def test_ac1_registered_strategies_are_unchanged() -> None:
    """策略配置的行為指紋與登記值相同——註解與格式可以動，會改變行為的欄位不行。"""
    for entry in _reg().entries:
        cfg = load_strategy(str(_CONFIG_DIR / "strategies" / f"{entry.strategy}.yaml"))
        assert config_fingerprint(cfg) == entry.strategy_fingerprint, (
            f"{entry.code}：configs/strategies/{entry.strategy}.yaml 在登記（D-148）之後被改動了。"
            "改動等於換了一個東西——請另立裁決與登記，不要覆寫 holdout2.yaml 的指紋。"
        )


def test_ac1b_post_registration_optional_fields_only_count_when_set() -> None:
    """D-149 在 schema 加了 slots.conditional_vol／inverse_vol：沒設（預設 None）不得改變指紋，
    設了就必須改變——前者是 schema 演進，後者才是行為改變。"""
    cfg = load_strategy(str(_CONFIG_DIR / "strategies" / "tail_b.yaml"))
    assert cfg.slots is not None and cfg.slots.conditional_vol is None
    f3 = load_strategy(str(_CONFIG_DIR / "strategies" / "tail_f3.yaml"))
    assert f3.slots is not None and f3.slots.inverse_vol is not None
    touched = cfg.model_copy(
        update={"slots": cfg.slots.model_copy(update={"inverse_vol": f3.slots.inverse_vol})}
    )
    assert config_fingerprint(touched) != config_fingerprint(cfg)


def test_ac2_registered_costs_are_unchanged() -> None:
    costs = load_config(_CONFIG_DIR / "costs.yaml", CostConfig)
    for entry in _reg().entries:
        assert config_fingerprint(costs) == entry.costs_fingerprint, (
            f"{entry.code}：configs/costs.yaml 在登記（D-148）之後被改動了。"
        )


def test_ac3_strategy_points_at_the_registered_model() -> None:
    """策略配置裡綁的模型就是登記的那一個（模型檔本身由開啟日的腳本核對 sha256）。"""
    for entry in _reg().entries:
        cfg = load_strategy(str(_CONFIG_DIR / "strategies" / f"{entry.strategy}.yaml"))
        bound = {str(s.params.get("model_id")) for s in cfg.signals if s.params.get("model_id")}
        assert bound == {entry.model_id}, (
            f"{entry.code}：策略綁的模型 {bound} ≠ 登記的 {entry.model_id}"
        )


def test_ac4_holdout_boundary_matches_d109() -> None:
    """保留期起點與開啟日必須與 D-109 相同：起點 2026-09-09、最早 2027-03-01、≥ 120 日。"""
    reg = _reg()
    assert reg.holdout_start == dt.date(2026, 9, 9)
    assert reg.opens_on == dt.date(2027, 3, 1)
    assert reg.min_sessions >= 120


def test_ac5_the_evaluation_refuses_to_run_early() -> None:
    """開啟日之前、或交易日不足時，判讀一律拒絕——這是登記唯一不能繞過的門。"""
    reg = _reg()
    with pytest.raises(PermissionError, match="最早"):
        refuse_before_opening(reg, dt.date(2027, 2, 28), sessions=200)
    with pytest.raises(PermissionError, match="未達"):
        refuse_before_opening(reg, dt.date(2027, 3, 1), sessions=100)
    refuse_before_opening(reg, dt.date(2027, 3, 1), sessions=120)  # 條件都滿足：不 raise


def test_ac6_fingerprint_ignores_formatting_but_not_behaviour() -> None:
    """指紋只反映行為：同一份配置重新載入指紋相同；改一個會影響行為的欄位，指紋就變。"""
    reg = _reg()
    entry = reg.entries[0]
    cfg = load_strategy(str(_CONFIG_DIR / "strategies" / f"{entry.strategy}.yaml"))
    assert config_fingerprint(cfg) == config_fingerprint(cfg.model_copy())
    assert cfg.slots is not None
    changed = cfg.model_copy(update={"slots": cfg.slots.model_copy(update={"n": cfg.slots.n + 1})})
    assert config_fingerprint(changed) != config_fingerprint(cfg)
