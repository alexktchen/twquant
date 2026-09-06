"""T-220 / PLAN §3.3: insight fields derived at read time, never written into a snapshot."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, cast

from twquant.conventions import TZ
from twquant.monitoring import api
from twquant.reporting.insight_evidence import InsightPack, save_pack
from twquant.reporting.insight_run import insights_for_run
from twquant.reporting.recommendation_snapshot import RecommendationSnapshot, save_snapshot

AS_OF = dt.datetime(2026, 9, 4, 20, 0, tzinfo=TZ)
BEFORE = dt.datetime(2026, 9, 3, 9, 0, tzinfo=TZ)
RUN = "mlscore_20260904_abc"


def _snapshot(data_dir: Path, stocks: list[str]) -> None:
    save_snapshot(
        data_dir,
        RecommendationSnapshot.model_validate(
            {
                "schema_version": 1,
                "run_id": RUN,
                "model_id": "lgbm_net_h20",
                "as_of": AS_OF,
                "generated_at": dt.datetime(2026, 9, 4, 20, 5, tzinfo=TZ),
                "published_at": None,
                "mode": "research",
                "artifact_fingerprint": "fp",
                "candidates": [
                    {
                        "stock_id": stock,
                        "rank": index + 1,
                        "score": 1.0 / (index + 1),
                        "bucket": "ml_candidate",
                        "reason_ref": None,
                        "insight_run_id": None,
                        "insight_available_at": None,
                        "data_status": "pending",
                    }
                    for index, stock in enumerate(stocks)
                ],
            }
        ),
    )


def _evidence(evidence_id: str, direction: str) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "title": evidence_id,
        "url": f"twquant://serving/x/{evidence_id}",
        "published_at": BEFORE,
        "available_at": BEFORE,
        "captured_at": BEFORE,
        "stock_id": "0050",
        "industry": None,
        "direction": direction,
        "availability": "actual",
        "reason_ref": None,
    }


def _pack(data_dir: Path, stock: str, run_id: str, *, analysed: bool) -> None:
    analysis = {
        "prompt_id": "candidate_analysis",
        "prompt_hash": "v1",
        "analysis_schema_version": 2,
        "model_version": "claude-opus-5",
        "as_of": AS_OF,
        "summary": "營收支持、籌碼反向。",
        "cited_evidence_ids": ["ev_a", "ev_b"],
        "stance": "conflict",
        "supporting_evidence_ids": ["ev_a"],
        "contradicting_evidence_ids": ["ev_b"],
        "event_horizon": "medium",
        "risks": [],
        "missing_data": [],
        "recommended_bucket": "watch",
        "unverified_numbers": [],
    }
    save_pack(
        data_dir,
        InsightPack.model_validate(
            {
                "schema_version": 1,
                "insight_run_id": run_id,
                "recommendation_run_id": RUN,
                "as_of": AS_OF,
                "stock_id": stock,
                "industry": None,
                "evidence": [
                    _evidence("ev_a", "support"),
                    _evidence("ev_b", "contradict"),
                    _evidence("ev_c", "gap"),
                ],
                "analysis": analysis if analysed else None,
                "analysis_absent_reason": None if analysed else "LLM 離線，未產生分析",
            }
        ),
    )


def test_ac220_1_the_derived_view_summarises_stance_and_evidence_counts(tmp_path: Path) -> None:
    _snapshot(tmp_path, ["0050"])
    _pack(tmp_path, "0050", "ins_a", analysed=True)

    joined = insights_for_run(tmp_path, RUN)

    assert set(joined) == {"0050"}
    row = joined["0050"]
    assert row["stance"] == "conflict"
    assert row["recommended_bucket"] == "watch"
    assert (row["support_n"], row["contradict_n"], row["gap_n"]) == (1, 1, 1)
    assert row["insight_run_id"] == "ins_a"
    assert row["absent_reason"] is None


def test_ac220_1_an_absent_analysis_is_reported_with_its_reason(tmp_path: Path) -> None:
    _snapshot(tmp_path, ["0050"])
    _pack(tmp_path, "0050", "ins_b", analysed=False)

    row = insights_for_run(tmp_path, RUN)["0050"]

    assert row["stance"] is None
    assert row["recommended_bucket"] is None
    assert "離線" in str(row["absent_reason"])
    assert row["gap_n"] == 1  # Evidence still counted without an analysis.


def test_ac220_1_packs_from_another_run_never_leak_in(tmp_path: Path) -> None:
    _snapshot(tmp_path, ["0050"])
    save_pack(
        tmp_path,
        InsightPack.model_validate(
            {
                "schema_version": 1,
                "insight_run_id": "ins_other",
                "recommendation_run_id": "mlscore_20260903_zzz",
                "as_of": AS_OF,
                "stock_id": "0050",
                "industry": None,
                "evidence": [_evidence("ev_a", "support")],
                "analysis": None,
                "analysis_absent_reason": "另一個 run",
            }
        ),
    )

    assert insights_for_run(tmp_path, RUN) == {}


def test_ac220_2_the_api_attaches_insight_without_touching_the_snapshot(tmp_path: Path) -> None:
    _snapshot(tmp_path, ["0050", "2330"])
    _pack(tmp_path, "0050", "ins_a", analysed=True)
    snapshot_file = tmp_path / "recommendations" / f"{RUN}.json"
    before = snapshot_file.read_bytes()

    response = api.get_platform_recommendations(tmp_path, run=RUN)
    run = cast(list[dict[str, Any]], response["runs"])[0]
    candidates = {c["stock_id"]: c for c in cast(list[dict[str, Any]], run["candidates"])}

    assert snapshot_file.read_bytes() == before  # PLAN §3.3: published runs are immutable.
    assert candidates["0050"]["insight"]["stance"] == "conflict"
    # The model's suggestion is carried as a suggestion, not as the candidate's bucket.
    assert candidates["0050"]["insight"]["recommended_bucket"] == "watch"
    assert candidates["0050"]["bucket"] == "ml_candidate"
    # A candidate with no pack says so rather than inheriting another stock's analysis.
    assert candidates["2330"]["insight"] is None


def test_ac220_2_recommendations_without_any_insight_still_serve(tmp_path: Path) -> None:
    _snapshot(tmp_path, ["0050"])

    response = api.get_platform_recommendations(tmp_path, run=RUN)
    run = cast(list[dict[str, Any]], response["runs"])[0]

    assert cast(list[dict[str, Any]], run["candidates"])[0]["insight"] is None


def test_ac221_1_the_pipeline_finds_the_days_run_or_reports_none(tmp_path: Path) -> None:
    from twquant.reporting.insight_run import run_id_for_day

    # No snapshot for the day is a normal outcome the pipeline treats as "nothing to do".
    assert run_id_for_day(tmp_path, dt.date(2026, 9, 4)) is None

    _snapshot(tmp_path, ["0050"])
    assert run_id_for_day(tmp_path, dt.date(2026, 9, 4)) == RUN
    assert run_id_for_day(tmp_path, dt.date(2026, 9, 3)) is None
