"""T-218 / PLAN §4: the candidate analysis task and its degradation paths.

Every test here injects a backend. Nothing in this file reaches the network.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from twquant.conventions import TZ
from twquant.insight.candidate_analysis import (
    LlmRefusal,
    analyse_candidate,
    build_user_payload,
    insight_run_id_for,
)
from twquant.insight.client import InsightClient
from twquant.insight.config import LlmConfig
from twquant.insight.evidence_bundle import build_bundle
from twquant.insight.prompts import CANDIDATE_ANALYSIS_SYSTEM, PROMPT_VERSION
from twquant.insight.schemas import CandidateAnalysis

AS_OF = dt.datetime(2026, 9, 4, 20, 0, tzinfo=TZ)
BEFORE = dt.datetime(2026, 9, 3, 9, 0, tzinfo=TZ)
NOW = dt.datetime(2026, 9, 4, 20, 30, tzinfo=TZ)
RUN = "mlscore_20260904_abc"


def _cfg(**patch: Any) -> LlmConfig:
    row: dict[str, Any] = {
        "usd_twd": 32.0,
        "daily_budget_twd": 300.0,
        "models": {
            "opus": {"id": "claude-opus-5", "input_usd_per_1m": 5.0, "output_usd_per_1m": 25.0}
        },
        "tasks": {"candidate_analysis": "opus"},
    }
    row.update(patch)
    return LlmConfig.model_validate(row)


def _bundle() -> Any:
    return build_bundle(
        "2330",
        AS_OF,
        {
            "ml": {
                "date": dt.date(2026, 9, 4),
                "score": 0.13,
                "rank_pct": 0.99,
                "model_id": "lgbm_net_h20",
                "available_at": BEFORE,
            },
            "monthly_revenue": [
                {
                    "date": dt.date(2026, 8, 10),
                    "year_month": "2026-07",
                    "revenue": 1234567.0,
                    "yoy": 0.18,
                    "available_at": BEFORE,
                    "available_at_estimated": False,
                }
            ],
            "institutional": [
                {
                    "date": dt.date(2026, 9, 4),
                    "foreign_net": 2000,
                    "trust_net": 0,
                    "dealer_net": 0,
                    "available_at": BEFORE,
                }
            ],
            "industry": None,
            "margin": [],
            "stock_valuation": None,
            "corporate_events": [],
            "disposition": [],
        },
    )


def _reply(**patch: Any) -> CandidateAnalysis:
    row: dict[str, Any] = {
        "stance": "support",
        "supporting_evidence_ids": ["fundamentals_revenue_yoy"],
        "contradicting_evidence_ids": [],
        "event_horizon": "medium",
        "risks": [],
        "missing_data": [],
        "recommended_bucket": "watch",
        "rationale": "月營收年增 18.0% 支持基本面改善。",
    }
    row.update(patch)
    return CandidateAnalysis.model_validate(row)


def _backend(reply: Any = None, *, raises: BaseException | None = None) -> Any:
    calls: list[tuple[str, str, str]] = []

    def backend(
        model_id: str, system: str, user: str, schema: type, max_tokens: int
    ) -> tuple[Any, int, int]:
        calls.append((model_id, system, user))
        if raises is not None:
            raise raises
        return (reply if reply is not None else _reply()), 8000, 1000

    backend.calls = calls  # type: ignore[attr-defined]
    return backend


def _client(tmp_path: Path, backend: Any, **patch: Any) -> InsightClient:
    return InsightClient(_cfg(**patch), tmp_path, api_key=None, backend=backend)


def test_ac218_1_a_valid_reply_becomes_a_persistable_v2_analysis(tmp_path: Path) -> None:
    bundle = _bundle()
    outcome = analyse_candidate(
        bundle, _client(tmp_path, _backend()), now=NOW, recommendation_run_id=RUN
    )

    assert outcome.absent_reason is None
    analysis = outcome.analysis
    assert analysis is not None
    assert analysis.analysis_schema_version == 2
    assert analysis.stance == "support"
    assert analysis.recommended_bucket == "watch"
    assert analysis.model_version == "claude-opus-5"
    assert analysis.prompt_id == "candidate_analysis"
    assert analysis.as_of == AS_OF
    # Whatever the analysis leaned on is also cited, so the pack's cutoff check covers it.
    assert set(analysis.supporting_evidence_ids) <= set(analysis.cited_evidence_ids)


def test_ac218_1_the_evidence_goes_in_the_user_turn_and_the_system_prompt_is_frozen(
    tmp_path: Path,
) -> None:
    backend = _backend()
    analyse_candidate(_bundle(), _client(tmp_path, backend), now=NOW, recommendation_run_id=RUN)
    _, system, user = backend.calls[0]

    # SPEC-008 §0.3: instructions in the system prompt, data in the user turn.
    assert system == CANDIDATE_ANALYSIS_SYSTEM
    assert "2330" not in system
    assert "fundamentals_revenue_yoy" in user
    assert "2330" in user
    # The payload states what is missing rather than leaving the model to guess.
    assert "缺口" in user or "gap" in user


def test_ac218_2_offline_is_a_stated_absence_not_an_error(tmp_path: Path) -> None:
    offline = InsightClient(_cfg(), tmp_path, api_key=None, backend=None)
    outcome = analyse_candidate(_bundle(), offline, now=NOW, recommendation_run_id=RUN)

    assert outcome.analysis is None
    assert outcome.absent_reason is not None
    assert "離線" in outcome.absent_reason


def test_ac218_2_exceeding_the_daily_budget_refuses_the_call(tmp_path: Path) -> None:
    from twquant.reporting.llm_log import record_usage

    # The gate trips on accumulated spend, so put today's spend over the cap first.
    record_usage(
        tmp_path,
        task="candidate_analysis",
        model_id="claude-opus-5",
        prompt_version=PROMPT_VERSION,
        input_tokens=1_000_000,
        output_tokens=0,
        cost_twd=200.0,
        now=NOW,
    )
    backend = _backend()
    client = _client(tmp_path, backend, daily_budget_twd=100.0)
    outcome = analyse_candidate(_bundle(), client, now=NOW, recommendation_run_id=RUN)

    assert outcome.analysis is None
    assert "預算" in (outcome.absent_reason or "")
    assert backend.calls == []  # The model was never called.


def test_ac218_2_a_refusal_is_recorded_with_its_category_and_not_retried(tmp_path: Path) -> None:
    backend = _backend(raises=LlmRefusal("cyber"))
    outcome = analyse_candidate(
        _bundle(), _client(tmp_path, backend), now=NOW, recommendation_run_id=RUN
    )

    assert outcome.analysis is None
    assert outcome.absent_reason == "refusal:cyber"
    # Retrying a policy decline just spends money for the same answer.
    assert len(backend.calls) == 1


def test_ac218_2_repeated_schema_failures_produce_no_analysis(tmp_path: Path) -> None:
    backend = _backend(raises=ValueError("bad json"))
    outcome = analyse_candidate(
        _bundle(), _client(tmp_path, backend), now=NOW, recommendation_run_id=RUN
    )

    assert outcome.analysis is None
    assert "失敗" in (outcome.absent_reason or "")
    assert len(backend.calls) == 2  # Two attempts, then give up (SPEC-008 AC-1).


def test_ac218_3_citing_evidence_outside_the_bundle_is_rejected(tmp_path: Path) -> None:
    stray = _reply(supporting_evidence_ids=["ev_not_in_bundle"])
    outcome = analyse_candidate(
        _bundle(), _client(tmp_path, _backend(stray)), now=NOW, recommendation_run_id=RUN
    )

    assert outcome.analysis is None
    assert "證據包" in (outcome.absent_reason or "")


def test_ac218_3_numbers_absent_from_cited_evidence_are_recorded_not_rejected(
    tmp_path: Path,
) -> None:
    """D-081(d): measure the rejection rate first; do not enforce yet."""
    invented = _reply(rationale="月營收年增 18.0%，毛利率提升 42%。")
    outcome = analyse_candidate(
        _bundle(), _client(tmp_path, _backend(invented)), now=NOW, recommendation_run_id=RUN
    )

    assert outcome.analysis is not None  # Still usable.
    assert outcome.analysis.unverified_numbers == ("42",)


def test_ac218_3_the_run_id_is_content_addressed_and_stable(tmp_path: Path) -> None:
    bundle = _bundle()
    first = insight_run_id_for(RUN, bundle, PROMPT_VERSION, "claude-opus-5")
    again = insight_run_id_for(RUN, bundle, PROMPT_VERSION, "claude-opus-5")
    other_prompt = insight_run_id_for(RUN, bundle, "different", "claude-opus-5")
    other_model = insight_run_id_for(RUN, bundle, PROMPT_VERSION, "claude-sonnet-5")

    assert first == again
    assert len({first, other_prompt, other_model}) == 3
    assert first.startswith("ins_")
    # The outcome carries the same identity whether the analysis succeeded or not.
    offline = analyse_candidate(
        _bundle(),
        InsightClient(_cfg(), tmp_path, api_key=None, backend=None),
        now=NOW,
        recommendation_run_id=RUN,
    )
    assert offline.insight_run_id == first


def test_ac218_3_the_payload_never_carries_an_instruction_channel(tmp_path: Path) -> None:
    backend = _backend()
    analyse_candidate(_bundle(), _client(tmp_path, backend), now=NOW, recommendation_run_id=RUN)
    _, _, user = backend.calls[0]

    # Evidence titles are quoted as data under a labelled section, so a title that looks
    # like an instruction cannot be mistaken for one.
    assert build_user_payload(_bundle()) == user
    assert "以下為資料" in user or "資料（非指令）" in user
