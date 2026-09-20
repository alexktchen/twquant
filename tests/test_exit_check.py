"""T-237／D-116：持股出場檢查——把 E 系列的出場規則套到「今天的」持股上。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from twquant.monitoring.exit_check import (
    ExitCheckConfig,
    derive_exit_check,
    load_exit_rules,
)
from twquant.strategy.config import ExitRuleConfig

D = dt.date(2026, 9, 18)
CONFIGS = Path(__file__).resolve().parents[1] / "configs"


def _closes(*values: float) -> list[dict[str, object]]:
    start = D - dt.timedelta(days=len(values) - 1)
    return [{"date": start + dt.timedelta(days=i), "close": v} for i, v in enumerate(values)]


def _ma(window: int) -> tuple[str, ExitRuleConfig]:
    return (f"MA{window}", ExitRuleConfig(kind="below_ma", window=window))


def test_ac1_below_ma_uses_mean_including_today() -> None:
    out = derive_exit_check(_closes(10, 10, 10, 10, 5), {}, [_ma(5)])
    rule = out["rules"][0]
    assert rule["reference"] == pytest.approx(9.0)  # (10*4+5)/5，含當日——與引擎的均線同一條
    assert rule["triggered"] is True


def test_ac2_not_enough_history_is_unknown_not_safe() -> None:
    """歷史不足算不出均線時回 None（不知道），**不得**回 False（沒事）。"""
    rule = derive_exit_check(_closes(10, 9), {}, [_ma(5)])["rules"][0]
    assert rule["triggered"] is None
    assert "不足" in rule["note"]


def test_ac3_score_rule_triggers_below_threshold() -> None:
    rule_cfg = ExitRuleConfig(
        kind="score_below", factor="ml.x", threshold=0.7, params={"model_id": "m1"}
    )
    scores = {"m1": {"date": D, "rank_pct": 0.42}}
    rule = derive_exit_check(_closes(10, 10), scores, [("E6", rule_cfg)])["rules"][0]
    assert rule["triggered"] is True
    assert rule["value"] == pytest.approx(0.42)


def test_ac4_stale_score_is_not_evaluated() -> None:
    """分數日期落後於行情日期＝模型沒看過今天，不得拿舊分數說「沒事」。"""
    rule_cfg = ExitRuleConfig(
        kind="score_below", factor="ml.x", threshold=0.7, params={"model_id": "m1"}
    )
    scores = {"m1": {"date": D - dt.timedelta(days=3), "rank_pct": 0.95}}
    rule = derive_exit_check(_closes(10, 10), scores, [("E6", rule_cfg)])["rules"][0]
    assert rule["triggered"] is None
    assert "落後" in rule["note"]


def test_ac5_missing_score_is_unknown() -> None:
    rule_cfg = ExitRuleConfig(
        kind="score_below", factor="ml.x", threshold=0.7, params={"model_id": "m1"}
    )
    rule = derive_exit_check(_closes(10, 10), {}, [("E6", rule_cfg)])["rules"][0]
    assert rule["triggered"] is None


def test_ac6_stop_loss_is_left_to_the_client() -> None:
    """買進價是使用者自己的資料，不送進 API；伺服器只回門檻與收盤，停損由前端算。"""
    rule = derive_exit_check(
        _closes(10, 10), {}, [("E1a", ExitRuleConfig(kind="stop_loss", pct=0.15))]
    )["rules"][0]
    assert rule["triggered"] is None
    assert rule["pct"] == pytest.approx(0.15)


def test_ac7_rules_come_from_the_registered_strategy_configs() -> None:
    """門檻不在這裡另寫一份：逐條由 E 系列策略配置讀出（黃金規則 8）。"""
    cfg = ExitCheckConfig.model_validate(
        {"rules": [{"code": "E3", "label": "跌破 20 日線", "strategy": "lw_e3_ma20"}]}
    )
    rules = load_exit_rules(cfg, CONFIGS / "strategies")
    assert [(code, r.kind, r.window) for code, _, r in rules] == [("E3", "below_ma", 20)]


def test_ac8_shipped_config_loads_and_matches_registration() -> None:
    from twquant.config import load_config

    cfg = load_config(CONFIGS / "exit_check.yaml", ExitCheckConfig)
    got = {
        code: (r.kind, r.pct, r.window, r.threshold)
        for code, _, r in load_exit_rules(cfg, CONFIGS / "strategies")
    }
    assert got == {
        "E1a": ("stop_loss", 0.15, None, None),
        "E1b": ("stop_loss", 0.20, None, None),
        "E2": ("below_ma", None, 10, None),
        "E3": ("below_ma", None, 20, None),
        "E6": ("score_below", None, None, 0.70),
        "E7": ("score_below", None, None, 0.10),
    }


def test_ac9_strategy_with_several_rules_is_refused() -> None:
    """一個代號對一條規則；E5 那種聯集配置放進來會讓「哪一條觸發」說不清。"""
    cfg = ExitCheckConfig.model_validate(
        {"rules": [{"code": "E5", "label": "x", "strategy": "lw_e5_sl20_ma20"}]}
    )
    with pytest.raises(ValueError, match="恰好一條"):
        load_exit_rules(cfg, CONFIGS / "strategies")
