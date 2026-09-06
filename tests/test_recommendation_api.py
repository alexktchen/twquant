"""T-206 read-only recommendation API tests."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, cast

from twquant.monitoring import api
from twquant.reporting.recommendation_snapshot import RecommendationSnapshot, save_snapshot


def _snapshot(
    run_id: str, day: str, model_id: str, candidates: list[dict[str, object]]
) -> RecommendationSnapshot:
    return RecommendationSnapshot.model_validate(
        {
            "schema_version": 1,
            "run_id": run_id,
            "model_id": model_id,
            "as_of": f"{day}T20:00:00+08:00",
            "generated_at": f"{day}T20:05:00+08:00",
            "published_at": None,
            "mode": "research",
            "artifact_fingerprint": "fp",
            "candidates": candidates,
        }
    )


def _candidate(stock_id: str, rank: int) -> dict[str, object]:
    return {
        "stock_id": stock_id,
        "rank": rank,
        "score": 0.4,
        "bucket": "top_decile",
        "reason_ref": None,
        "insight_run_id": None,
        "insight_available_at": None,
        "data_status": "pending",
    }


def test_ac206_1_recommendation_filters_keep_original_run_and_candidate_identity(
    tmp_path: Path,
) -> None:
    save_snapshot(tmp_path, _snapshot("run_a", "2026-09-04", "model_a", [_candidate("0050", 1)]))
    save_snapshot(tmp_path, _snapshot("run_b", "2026-09-05", "model_b", [_candidate("2330", 1)]))

    response = api.get_platform_recommendations(
        tmp_path, date=dt.date(2026, 9, 4), model="model_a", run=None
    )
    runs = cast(list[dict[str, Any]], response["runs"])

    assert response["query"] == {"date": "2026-09-04", "model": "model_a", "run": None}
    assert runs[0]["run_id"] == "run_a"
    assert cast(list[dict[str, Any]], runs[0]["candidates"])[0]["stock_id"] == "0050"


def test_ac206_2_empty_date_is_not_replaced_by_another_run_and_zero_candidates_is_distinct(
    tmp_path: Path,
) -> None:
    save_snapshot(tmp_path, _snapshot("zero", "2026-09-04", "model_a", []))
    save_snapshot(tmp_path, _snapshot("other", "2026-09-05", "model_a", [_candidate("0050", 1)]))

    zero = api.get_platform_recommendations(tmp_path, date=dt.date(2026, 9, 4))
    missing = api.get_platform_recommendations(tmp_path, date=dt.date(2026, 9, 3))
    zero_runs = cast(list[dict[str, Any]], zero["runs"])
    missing_runs = cast(list[dict[str, Any]], missing["runs"])

    assert [run["run_id"] for run in zero_runs] == ["zero"]
    assert zero_runs[0]["candidates"] == []
    assert missing_runs == []


def test_ac206_1_platform_route_is_read_only() -> None:
    route = next(
        route
        for route in api.app.routes
        if getattr(route, "path", None) == "/api/platform/recommendations"
    )
    assert getattr(route, "methods", None) == {"GET"}
