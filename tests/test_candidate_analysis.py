"""T-216 / PLAN §5: candidate analysis output schema and its validators."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from twquant.insight.schemas import CandidateAnalysis
from twquant.insight.validation import unverified_numbers


def _analysis(**patch: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "analysis_schema_version": 2,
        "stance": "support",
        "supporting_evidence_ids": ["ev_1"],
        "contradicting_evidence_ids": [],
        "event_horizon": "medium",
        "risks": [],
        "missing_data": [],
        "recommended_bucket": "watch",
        "rationale": "月營收年增 18% 支持基本面改善（ev_1）。",
    }
    row.update(patch)
    return row


def test_ac216_1_analysis_keeps_every_prd_field_and_defaults_are_conservative() -> None:
    analysis = CandidateAnalysis.model_validate(_analysis())

    assert analysis.stance == "support"
    assert analysis.recommended_bucket == "watch"
    assert analysis.event_horizon == "medium"
    assert analysis.supporting_evidence_ids == ["ev_1"]

    # An analysis that says nothing defaults to the least committal shape.
    minimal = CandidateAnalysis.model_validate(
        {"stance": "neutral", "recommended_bucket": "watch", "rationale": "資料不足以判斷。"}
    )
    assert minimal.analysis_schema_version == 2
    assert minimal.event_horizon == "unknown"
    assert (minimal.supporting_evidence_ids, minimal.missing_data, minimal.risks) == ([], [], [])


def test_ac216_1_support_requires_supporting_evidence() -> None:
    # "Support" with nothing behind it is exactly the failure mode the layer exists to prevent.
    with pytest.raises(ValidationError, match="supporting_evidence_ids"):
        CandidateAnalysis.model_validate(_analysis(supporting_evidence_ids=[]))


def test_ac216_1_insufficient_data_must_name_what_is_missing() -> None:
    with pytest.raises(ValidationError, match="missing_data"):
        CandidateAnalysis.model_validate(
            _analysis(stance="insufficient_data", supporting_evidence_ids=[], missing_data=[])
        )
    ok = CandidateAnalysis.model_validate(
        _analysis(
            stance="insufficient_data",
            supporting_evidence_ids=[],
            missing_data=["尚無法說會逐字稿"],
        )
    )
    assert ok.missing_data == ["尚無法說會逐字稿"]


def test_ac216_1_one_evidence_cannot_both_support_and_contradict() -> None:
    with pytest.raises(ValidationError, match="同時列為支持與反對"):
        CandidateAnalysis.model_validate(
            _analysis(supporting_evidence_ids=["ev_1"], contradicting_evidence_ids=["ev_1"])
        )


def test_ac216_1_unknown_fields_and_categories_are_rejected() -> None:
    with pytest.raises(ValidationError):
        CandidateAnalysis.model_validate(_analysis(stance="bullish"))
    with pytest.raises(ValidationError):
        CandidateAnalysis.model_validate(_analysis(recommended_bucket="buy"))
    with pytest.raises(ValidationError):
        CandidateAnalysis.model_validate(_analysis(unexpected="x"))


def test_ac216_2_numbers_absent_from_cited_evidence_are_reported_not_rejected() -> None:
    """D-081(d): the check runs and records; it does not fail the analysis yet."""
    evidence = ["2026 年 8 月營收 12,345 千元，年增 18.0%", "外資近 10 日買超 2,000 張"]

    assert unverified_numbers("月營收年增 18% 且外資買超 2000 張。", evidence) == ()
    # 25% never appears in any cited evidence.
    assert unverified_numbers("毛利率提升 25%。", evidence) == ("25",)
    assert unverified_numbers("年增 18%，但毛利率 25% 且負債比 40%。", evidence) == ("25", "40")


def test_ac216_2_number_verification_normalises_separators_and_ignores_evidence_ids() -> None:
    evidence = ["當月營收 1,234,567 元", "PER 12.5 倍"]

    assert unverified_numbers("營收 1234567 元、PER 12.5 倍。", evidence) == ()
    # Evidence identifiers are references, not claims, so they are not treated as numbers.
    assert unverified_numbers("依據 ev_1 與 ev_2 判斷。", evidence) == ()
    assert unverified_numbers("沒有任何數字。", evidence) == ()
