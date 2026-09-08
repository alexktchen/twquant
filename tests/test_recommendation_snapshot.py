"""T-205 recommendation snapshot contract and controlled import acceptance tests."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from twquant import cli
from twquant.conventions import TZ
from twquant.errors import DataError
from twquant.reporting.recommendation_snapshot import (
    RecommendationSnapshot,
    read_snapshot,
    save_snapshot,
    snapshot_path,
)


def _payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": "daily_20260906_model_a",
        "model_id": "lgbm_net_h20_20260225_6a798962",
        "as_of": "2026-09-04T20:00:00+08:00",
        "generated_at": "2026-09-04T20:03:00+08:00",
        "published_at": None,
        "mode": "research",
        "artifact_fingerprint": "aabbccdd",
        "candidates": [
            {
                "stock_id": "0050",
                "rank": 1,
                "score": 0.4125,
                "bucket": "top_decile",
                "reason_ref": "reason-0050",
                "insight_run_id": None,
                "insight_available_at": None,
                "data_status": "pending",
            },
            {
                "stock_id": "2330",
                "rank": 2,
                "score": 0.398,
                "bucket": "top_decile",
                "reason_ref": None,
                "insight_run_id": "insight_20260904_2330",
                "insight_available_at": "2026-09-04T19:30:00+08:00",
                "data_status": "ready",
            },
        ],
    }


def _snapshot() -> RecommendationSnapshot:
    return RecommendationSnapshot.model_validate(_payload())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(as_of="2026-09-04T20:00:00"),
        lambda payload: payload["candidates"].__setitem__(
            1, {**payload["candidates"][1], "stock_id": "0050"}
        ),
        lambda payload: payload["candidates"].__setitem__(
            1, {**payload["candidates"][1], "rank": 1}
        ),
        lambda payload: payload["candidates"].__setitem__(
            1, {**payload["candidates"][1], "insight_available_at": "2026-09-04T20:01:00+08:00"}
        ),
        lambda payload: payload.update(mode="published"),
    ],
)
def test_ac205_1_contract_rejects_naive_duplicate_forward_or_unknown_mode(
    mutate: Any,
) -> None:
    payload = _payload()
    mutate(payload)

    with pytest.raises(ValidationError):
        RecommendationSnapshot.model_validate(payload)


def test_ac205_1_same_run_same_payload_is_idempotent_and_conflict_preserves_first(
    tmp_path: Path,
) -> None:
    first = _snapshot()
    created = save_snapshot(tmp_path, first)
    before = created.path.read_bytes()

    repeated = save_snapshot(tmp_path, RecommendationSnapshot.model_validate(_payload()))

    assert created.created is True
    assert repeated.created is False
    assert repeated.path == snapshot_path(tmp_path, first.run_id)
    assert repeated.path.read_bytes() == before

    conflicting = _payload()
    conflicting["candidates"][0]["score"] = 0.1
    with pytest.raises(DataError, match="拒絕覆寫"):
        save_snapshot(tmp_path, RecommendationSnapshot.model_validate(conflicting))
    assert created.path.read_bytes() == before
    assert list(created.path.parent.glob("*.tmp-*")) == []


def test_ac205_2_research_preserves_raw_ml_pending_and_unpublished_candidates(
    tmp_path: Path,
) -> None:
    saved = save_snapshot(tmp_path, _snapshot())
    loaded = read_snapshot(saved.path)

    assert loaded.mode == "research"
    assert loaded.published_at is None
    assert [candidate.stock_id for candidate in loaded.candidates] == ["0050", "2330"]
    assert [candidate.rank for candidate in loaded.candidates] == [1, 2]
    assert loaded.candidates[0].score == pytest.approx(0.4125)
    assert loaded.candidates[0].data_status == "pending"
    assert loaded.candidates[0].insight_run_id is None


def test_ac205_2_reconstructed_snapshot_cannot_claim_forward_publication() -> None:
    payload = _payload()
    payload["mode"] = "reconstructed"
    payload["published_at"] = "2026-09-04T20:05:00+08:00"

    with pytest.raises(ValidationError, match="reconstructed"):
        RecommendationSnapshot.model_validate(payload)


def test_ac205_1_controlled_cli_import_uses_validated_source_and_configured_output(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.json"
    source.write_text(json.dumps(_payload()), encoding="utf-8")
    output = tmp_path / "data"

    result = CliRunner().invoke(
        cli.app, ["recommendations-import", str(source), "--out", str(output)]
    )

    assert result.exit_code == 0, result.output
    assert "已保存" in result.output
    assert read_snapshot(snapshot_path(output, "daily_20260906_model_a")).as_of == dt.datetime(
        2026, 9, 4, 20, tzinfo=TZ
    )
