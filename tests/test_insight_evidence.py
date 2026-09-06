"""T-210 / SPEC-014 §6: insight evidence pack contract tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from twquant.errors import DataError
from twquant.reporting.insight_evidence import (
    InsightPack,
    import_pack,
    index_packs,
    read_pack,
    save_pack,
    select_packs,
)


def _evidence(
    evidence_id: str, available: str = "2026-09-03T09:00:00+08:00", **patch: Any
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "evidence_id": evidence_id,
        "title": "月營收公告",
        "url": "https://mops.example/announce/1",
        "published_at": "2026-09-03T08:00:00+08:00",
        "available_at": available,
        "captured_at": "2026-09-03T10:00:00+08:00",
        "stock_id": "0050",
        "industry": None,
        "direction": "support",
        "reason_ref": None,
    }
    row.update(patch)
    return row


def _pack(**patch: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "schema_version": 1,
        "insight_run_id": "ins_1",
        "as_of": "2026-09-04T20:00:00+08:00",
        "stock_id": "0050",
        "industry": None,
        "evidence": [_evidence("ev_1")],
        "analysis": {
            "prompt_id": "insight_v1",
            "prompt_hash": "abc123",
            "analysis_schema_version": 1,
            "model_version": "claude-x-2026",
            "as_of": "2026-09-04T20:00:00+08:00",
            "summary": "營收年增支持基本面改善。",
            "cited_evidence_ids": ["ev_1"],
        },
        "analysis_absent_reason": None,
    }
    row.update(patch)
    return row


def test_ac210_1_valid_pack_keeps_citations_directions_and_string_identity() -> None:
    pack = InsightPack.model_validate(_pack())

    assert pack.stock_id == "0050"
    assert pack.analysis is not None
    assert pack.analysis.cited_evidence_ids == ("ev_1",)
    assert pack.evidence[0].direction == "support"


def test_ac210_1_analysis_citing_a_missing_evidence_is_rejected() -> None:
    with pytest.raises(ValidationError, match="不存在的 evidence_id"):
        InsightPack.model_validate(
            _pack(
                analysis={
                    **_pack()["analysis"],
                    "cited_evidence_ids": ["ev_missing"],
                }
            )
        )


def test_ac210_1_citing_evidence_available_after_the_cutoff_is_rejected() -> None:
    # The source only became available the day after the analysis cutoff. Its own timeline is
    # internally consistent, so the cutoff rule is what has to reject it.
    with pytest.raises(ValidationError, match="前視引用"):
        InsightPack.model_validate(
            _pack(
                evidence=[
                    _evidence(
                        "ev_1",
                        available="2026-09-05T09:00:00+08:00",
                        published_at="2026-09-05T08:00:00+08:00",
                        captured_at="2026-09-05T10:00:00+08:00",
                    )
                ]
            )
        )


def test_ac210_1_naive_timestamps_and_broken_source_timelines_are_rejected() -> None:
    with pytest.raises(ValidationError):
        InsightPack.model_validate(_pack(as_of="2026-09-04T20:00:00"))
    with pytest.raises(ValidationError, match="available_at 早於 published_at"):
        InsightPack.model_validate(
            _pack(evidence=[_evidence("ev_1", available="2026-09-03T07:00:00+08:00")])
        )
    with pytest.raises(ValidationError, match="captured_at 早於 available_at"):
        InsightPack.model_validate(
            _pack(evidence=[_evidence("ev_1", captured_at="2026-09-03T08:30:00+08:00")])
        )


def test_ac210_1_an_absent_analysis_must_state_why_and_never_be_invented() -> None:
    offline = InsightPack.model_validate(
        _pack(analysis=None, analysis_absent_reason="LLM 離線，未產生分析")
    )
    assert offline.analysis is None
    assert offline.analysis_absent_reason == "LLM 離線，未產生分析"
    assert offline.evidence[0].evidence_id == "ev_1"  # Evidence still readable without analysis.

    with pytest.raises(ValidationError, match="analysis_absent_reason"):
        InsightPack.model_validate(_pack(analysis=None))
    with pytest.raises(ValidationError, match="不得同時宣稱分析缺席"):
        InsightPack.model_validate(_pack(analysis_absent_reason="LLM 離線"))


def test_ac210_1_gaps_and_contradictions_stay_visible_and_duplicates_are_rejected() -> None:
    pack = InsightPack.model_validate(
        _pack(
            evidence=[
                _evidence("ev_1"),
                _evidence("ev_2", direction="contradict", title="法人賣超"),
                _evidence("ev_3", direction="gap", title="尚無法人持股明細"),
            ]
        )
    )

    assert [item.direction for item in pack.evidence] == ["support", "contradict", "gap"]
    assert [item.evidence_id for item in pack.gaps()] == ["ev_3"]

    with pytest.raises(ValidationError, match="重複 evidence_id"):
        InsightPack.model_validate(_pack(evidence=[_evidence("ev_1"), _evidence("ev_1")]))


def test_ac210_1_saving_is_idempotent_and_conflicting_content_is_refused(tmp_path: Path) -> None:
    pack = InsightPack.model_validate(_pack())

    first = save_pack(tmp_path, pack)
    again = save_pack(tmp_path, pack)

    assert first.created is True
    assert again.created is False
    assert read_pack(first.path).insight_run_id == "ins_1"

    conflicting = InsightPack.model_validate(_pack(stock_id="2330"))
    with pytest.raises(DataError, match="拒絕覆寫"):
        save_pack(tmp_path, conflicting)


def test_ac210_1_invalid_saved_packs_become_issues_instead_of_being_dropped(
    tmp_path: Path,
) -> None:
    save_pack(tmp_path, InsightPack.model_validate(_pack()))
    (tmp_path / "insights" / "broken.json").write_text('{"schema_version": 1}', encoding="utf-8")

    catalog = index_packs(tmp_path)

    assert [pack.insight_run_id for pack in catalog.packs] == ["ins_1"]
    assert catalog.issues == [
        {"insight_run_id": "broken", "detail": "insight evidence pack is invalid"}
    ]
    # No filesystem path may reach the client through an issue entry.
    assert all(str(tmp_path) not in value for issue in catalog.issues for value in issue.values())


def test_ac210_1_import_validates_before_saving_and_filters_do_not_substitute(
    tmp_path: Path,
) -> None:
    import json

    source = tmp_path / "incoming.json"
    source.write_text(json.dumps(_pack()), encoding="utf-8")
    import_pack(source, tmp_path)

    assert [pack.insight_run_id for pack in select_packs(tmp_path, stock_id="0050").packs] == [
        "ins_1"
    ]
    assert select_packs(tmp_path, stock_id="2330").packs == []

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(_pack(analysis=None)), encoding="utf-8")
    with pytest.raises(DataError, match="合約無效"):
        import_pack(bad, tmp_path)
