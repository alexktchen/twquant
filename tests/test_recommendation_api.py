"""T-206 read-only recommendation API tests."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import HTTPException

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

    assert response["query"] == {
        "date": "2026-09-04",
        "model": "model_a",
        "run": None,
        "bucket": None,
        "q": None,
        "page": 1,
        "page_size": 200,
    }
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


def _bucketed(stock_id: str, rank: int, bucket: str) -> dict[str, object]:
    candidate = _candidate(stock_id, rank)
    candidate["bucket"] = bucket
    return candidate


def test_ac206_1_bucket_and_search_filters_apply_before_pagination(tmp_path: Path) -> None:
    save_snapshot(
        tmp_path,
        _snapshot(
            "run_p",
            "2026-09-04",
            "model_a",
            [
                _bucketed("0050", 1, "top_decile"),
                _bucketed("2330", 2, "watch"),
                _bucketed("0056", 3, "top_decile"),
                _bucketed("2317", 4, "top_decile"),
            ],
        ),
    )

    filtered = api.get_platform_recommendations(
        tmp_path, date=dt.date(2026, 9, 4), bucket="top_decile", page=1, page_size=2
    )
    run = cast(list[dict[str, Any]], filtered["runs"])[0]
    page_two = api.get_platform_recommendations(
        tmp_path, date=dt.date(2026, 9, 4), bucket="top_decile", page=2, page_size=2
    )
    second = cast(list[dict[str, Any]], page_two["runs"])[0]

    # Pagination slices only what the bucket filter already kept, never the raw run.
    assert run["candidate_total"] == 3
    assert [c["stock_id"] for c in cast(list[dict[str, Any]], run["candidates"])] == [
        "0050",
        "0056",
    ]
    assert [c["stock_id"] for c in cast(list[dict[str, Any]], second["candidates"])] == ["2317"]
    assert [c["rank"] for c in cast(list[dict[str, Any]], second["candidates"])] == [4]


def test_ac206_1_search_matches_stock_id_and_keeps_original_rank(tmp_path: Path) -> None:
    save_snapshot(
        tmp_path,
        _snapshot(
            "run_q",
            "2026-09-04",
            "model_a",
            [_candidate("0050", 1), _candidate("2330", 2), _candidate("0056", 3)],
        ),
    )

    response = api.get_platform_recommendations(tmp_path, q="005")
    run = cast(list[dict[str, Any]], response["runs"])[0]

    assert cast(dict[str, Any], response["query"])["q"] == "005"
    assert [c["stock_id"] for c in cast(list[dict[str, Any]], run["candidates"])] == [
        "0050",
        "0056",
    ]
    assert [c["rank"] for c in cast(list[dict[str, Any]], run["candidates"])] == [1, 3]


def test_ac206_1_stock_events_return_only_that_stock_across_runs(tmp_path: Path) -> None:
    save_snapshot(
        tmp_path,
        _snapshot(
            "run_s1", "2026-09-04", "model_a", [_candidate("0050", 1), _candidate("2330", 2)]
        ),
    )
    save_snapshot(tmp_path, _snapshot("run_s2", "2026-09-05", "model_a", [_candidate("0050", 3)]))

    events = cast(
        list[dict[str, Any]], api.get_platform_stock_recommendations("0050", tmp_path)["events"]
    )

    assert [event["run_id"] for event in events] == ["run_s1", "run_s2"]
    assert [event["rank"] for event in events] == [1, 3]
    assert all(event["stock_id"] == "0050" for event in events)
    assert api.get_platform_stock_recommendations("2330", tmp_path)["events"] == [
        {
            "run_id": "run_s1",
            "model_id": "model_a",
            "stock_id": "2330",
            "as_of": "2026-09-04T20:00:00+08:00",
            "published_at": None,
            "mode": "research",
            "rank": 2,
            "score": 0.4,
            "bucket": "top_decile",
            "reason_ref": None,
            "insight_run_id": None,
            "data_status": "pending",
        }
    ]


def test_ac206_2_stock_without_events_is_empty_and_bad_identifier_is_rejected(
    tmp_path: Path,
) -> None:
    save_snapshot(tmp_path, _snapshot("run_z", "2026-09-04", "model_a", [_candidate("0050", 1)]))

    assert api.get_platform_stock_recommendations("9999", tmp_path)["events"] == []
    for bad in ("../etc", "00 50", ""):
        with pytest.raises(HTTPException) as raised:
            api.get_platform_stock_recommendations(bad, tmp_path)
        assert raised.value.status_code == 422
