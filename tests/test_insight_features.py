"""T-027/T-028：daily_brief / explainer / advisor + 提案生命週期。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from twquant.insight.advisor import propose_adjustment
from twquant.insight.client import Backend, InsightClient
from twquant.insight.config import LlmConfig
from twquant.insight.daily_brief import build_daily_brief
from twquant.insight.explainer import explain_event
from twquant.insight.schemas import DailyBrief, Explanation, Proposal, ProposedChange
from twquant.reporting.proposals import (
    apply_changes,
    list_proposals,
    load_proposal,
    save_proposal,
    update_proposal,
)

_NOW = dt.datetime(2026, 6, 30, 17, 0)


def _cfg() -> LlmConfig:
    return LlmConfig.model_validate(
        {
            "usd_twd": 32.0,
            "daily_budget_twd": 1000.0,
            "models": {
                "opus": {
                    "id": "claude-opus-4-8",
                    "input_usd_per_1m": 5.0,
                    "output_usd_per_1m": 25.0,
                },
                "haiku": {
                    "id": "claude-haiku-4-5",
                    "input_usd_per_1m": 1.0,
                    "output_usd_per_1m": 5.0,
                },
            },
            "tasks": {"daily_brief": "haiku", "advisor": "opus", "explainer": "opus"},
        }
    )


def _backend(obj: object) -> Backend:
    def b(
        model_id: str, system: str, user: str, schema: type, max_tokens: int
    ) -> tuple[object, int, int]:
        return obj, 800, 300

    return b


# ── daily_brief / explainer 離線降級（AC-6）+ 有 backend ────────────
def test_daily_brief_offline_and_online(tmp_path: Path) -> None:
    offline = InsightClient(_cfg(), tmp_path, api_key=None)
    assert build_daily_brief(offline, {"sharpe": 1.0}, _NOW) is None  # 離線→None
    online = InsightClient(_cfg(), tmp_path, backend=_backend(DailyBrief(summary="今日平盤。")))
    brief = build_daily_brief(online, {"sharpe": 1.0}, _NOW)
    assert brief is not None and brief.summary == "今日平盤。"


def test_explainer(tmp_path: Path) -> None:
    exp = Explanation(likely_causes=["外資大額調節"], suggested_checks=["查當日法人明細"])
    client = InsightClient(_cfg(), tmp_path, backend=_backend(exp))
    out = explain_event(client, {"check": "drift", "expected": 0.02, "actual": 0.05}, _NOW)
    assert out is not None and out.likely_causes[0] == "外資大額調節"


# ── advisor → LlmResult(Proposal) ─────────────────────────────────
def test_advisor_returns_proposal(tmp_path: Path) -> None:
    prop = Proposal(
        action="param_change",
        target_strategy="sample_revenue_trust",
        changes=[ProposedChange(yaml_path="signals.0.weight", old_value="0.6", new_value="0.7")],
        rationale="月營收因子 IC t=8 最穩健，加重。",
        expected_impact="提升 Sharpe",
        confidence="medium",
    )
    client = InsightClient(_cfg(), tmp_path, backend=_backend(prop))
    res = propose_adjustment(client, {"factor_ic": {"revenue_yoy": 8.0}}, _NOW)
    assert res is not None
    assert res.parsed.action == "param_change"
    assert res.model_id == "claude-opus-4-8"


# ── 提案生命週期 ───────────────────────────────────────────────────
def test_no_action_not_saved(tmp_path: Path) -> None:
    prop = Proposal(
        action="no_action",
        target_strategy="s",
        rationale="無明確依據",
        expected_impact="—",
        confidence="low",
    )
    pid = save_proposal(tmp_path, prop, {}, dt.date(2026, 6, 30))
    assert pid == ""  # AC-7：no_action 不落地
    assert list_proposals(tmp_path) == []


def test_save_load_update_proposal(tmp_path: Path) -> None:
    prop = Proposal(
        action="factor_reweight",
        target_strategy="sample_revenue_trust",
        changes=[ProposedChange(yaml_path="signals.1.weight", new_value="0.3")],
        rationale="投信連買在乾淨期不顯著，降權。",
        expected_impact="降風險",
        confidence="high",
    )
    meta = {"model_id": "claude-opus-4-8", "cost_twd": 1.2}
    pid = save_proposal(tmp_path, prop, meta, dt.date(2026, 6, 30))
    assert pid.startswith("2026-06-30-")
    assert list_proposals(tmp_path) == [pid]
    data = load_proposal(tmp_path, pid)
    assert data["status"] == "pending"
    assert data["proposal"]["action"] == "factor_reweight"
    assert data["llm_meta"]["cost_twd"] == 1.2

    update_proposal(tmp_path, pid, status="validated", backtest={"sharpe": 1.3})
    assert load_proposal(tmp_path, pid)["status"] == "validated"


# ── 套用提案變更到策略 dict（供 validate 回測）────────────────────
def test_apply_changes_path() -> None:
    base: dict[str, Any] = {
        "name": "s",
        "signals": [
            {"factor": "fundamental.revenue_yoy", "weight": 0.6},
            {"factor": "chip.trust_streak", "weight": 0.4},
        ],
        "max_weight_per_stock": 0.05,
    }
    changed = apply_changes(
        base,
        [
            {"yaml_path": "signals.0.weight", "new_value": "0.7"},
            {"yaml_path": "max_weight_per_stock", "new_value": "0.08"},
        ],
    )
    assert changed["signals"][0]["weight"] == 0.7
    assert changed["max_weight_per_stock"] == 0.08
    assert base["signals"][0]["weight"] == 0.6  # 原件不變
