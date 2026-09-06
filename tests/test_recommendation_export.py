"""T-213 / SPEC-014 §7: daily ML inference exported as a recommendation snapshot."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from twquant.conventions import TZ
from twquant.errors import DataError
from twquant.reporting.recommendation_export import (
    export_scores,
    read_model_identity,
    run_id_for,
    snapshot_from_scores,
)
from twquant.reporting.recommendation_snapshot import read_snapshot, snapshot_path

DAY = dt.date(2026, 9, 4)
# 推論在截點之後產生（盤後跑），符合 D-008 的 20:00 訊號截點。
STAMP = dt.datetime(2026, 9, 4, 20, 30, tzinfo=TZ)
CUTOFF = "2026-09-04T20:00:00+08:00"


def _rows(*pairs: tuple[str, float], stamp: dt.datetime = STAMP) -> list[dict[str, Any]]:
    return [
        {"date": DAY, "stock_id": stock_id, "score": score, "available_at": stamp}
        for stock_id, score in pairs
    ]


def _model(tmp_path: Path, **patch: Any) -> Path:
    directory = tmp_path / "models" / "lgbm_net_h20_20260225"
    directory.mkdir(parents=True)
    meta: dict[str, Any] = {
        "model_id": "lgbm_net_h20_20260225",
        "artifact_fingerprint": "fp-abc",
        "features": ["mom_20d"],
    }
    meta.update(patch)
    (directory / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return directory


def test_ac213_1_every_inference_row_survives_with_its_own_score_and_model(
    tmp_path: Path,
) -> None:
    snapshot = snapshot_from_scores(
        _rows(("2330", 0.41), ("0050", 0.92), ("0056", 0.77)),
        model_id="lgbm_net_h20_20260225",
        day=DAY,
        artifact_fingerprint="fp-abc",
    )

    assert snapshot.model_id == "lgbm_net_h20_20260225"
    # as_of is the inference day's signal cutoff, not the moment the export ran.
    assert snapshot.as_of.isoformat() == CUTOFF
    assert snapshot.generated_at.isoformat() == "2026-09-04T20:30:00+08:00"
    # Highest score first; every candidate from the inference is kept, none filtered away.
    assert [c.stock_id for c in snapshot.candidates] == ["0050", "0056", "2330"]
    assert [c.rank for c in snapshot.candidates] == [1, 2, 3]
    assert [c.score for c in snapshot.candidates] == [0.92, 0.77, 0.41]
    assert {c.data_status for c in snapshot.candidates} == {"pending"}


def test_ac213_1_an_exported_run_is_research_only_and_never_claims_publication() -> None:
    snapshot = snapshot_from_scores(
        _rows(("0050", 0.92)),
        model_id="lgbm_net_h20_20260225",
        day=DAY,
        artifact_fingerprint="fp-abc",
    )

    assert snapshot.mode == "research"
    assert snapshot.published_at is None
    # A successful training run confers no bucket promotion at the export boundary.
    assert {c.bucket for c in snapshot.candidates} == {"ml_candidate"}
    assert all(c.insight_run_id is None for c in snapshot.candidates)


def test_ac213_1_the_same_inference_run_yields_exactly_one_snapshot(tmp_path: Path) -> None:
    model_dir = _model(tmp_path)
    scores = tmp_path / "scores_2026-09-04.parquet"
    pl.DataFrame(_rows(("0050", 0.92), ("2330", 0.41))).write_parquet(scores)

    first = export_scores(scores, model_dir, tmp_path, DAY)
    again = export_scores(scores, model_dir, tmp_path, DAY)

    assert first.created is True
    assert again.created is False  # Idempotent: the second export changes nothing.
    assert first.path == again.path
    assert run_id_for("lgbm_net_h20_20260225", DAY) == read_snapshot(first.path).run_id
    assert [c.stock_id for c in read_snapshot(first.path).candidates] == ["0050", "2330"]


def test_ac213_1_a_conflicting_rerun_is_refused_rather_than_overwriting(tmp_path: Path) -> None:
    model_dir = _model(tmp_path)
    first_scores = tmp_path / "a.parquet"
    second_scores = tmp_path / "b.parquet"
    pl.DataFrame(_rows(("0050", 0.92))).write_parquet(first_scores)
    pl.DataFrame(_rows(("0050", 0.55))).write_parquet(second_scores)

    export_scores(first_scores, model_dir, tmp_path, DAY)
    with pytest.raises(DataError, match="拒絕覆寫"):
        export_scores(second_scores, model_dir, tmp_path, DAY)

    assert (
        read_snapshot(snapshot_path(tmp_path, run_id_for("lgbm_net_h20_20260225", DAY)))
        .candidates[0]
        .score
        == 0.92
    )


def test_ac213_1_a_failed_export_publishes_nothing(tmp_path: Path) -> None:
    model_dir = _model(tmp_path)
    empty = tmp_path / "empty.parquet"
    pl.DataFrame(
        schema={"date": pl.Date, "stock_id": pl.String, "score": pl.Float64}
    ).write_parquet(empty)

    with pytest.raises(DataError, match="拒絕發布半成品"):
        export_scores(empty, model_dir, tmp_path, DAY)
    assert not (tmp_path / "recommendations").exists()

    wrong_day = tmp_path / "wrong.parquet"
    pl.DataFrame(_rows(("0050", 0.9))).write_parquet(wrong_day)
    with pytest.raises(DataError, match="含其他日期的列"):
        export_scores(wrong_day, model_dir, tmp_path, dt.date(2026, 9, 5))
    assert not (tmp_path / "recommendations").exists()


def test_ac213_1_a_model_without_real_identity_cannot_be_exported(tmp_path: Path) -> None:
    identified = _model(tmp_path)
    assert read_model_identity(identified) == ("lgbm_net_h20_20260225", "fp-abc")

    with pytest.raises(DataError, match="讀不到模型 metadata"):
        read_model_identity(tmp_path / "models" / "absent")

    nameless = tmp_path / "nameless"
    nameless.mkdir()
    (nameless / "meta.json").write_text(json.dumps({"features": ["mom_20d"]}), encoding="utf-8")
    with pytest.raises(DataError, match="缺 model_id"):
        read_model_identity(nameless)

    unfingerprinted = tmp_path / "unfingerprinted"
    unfingerprinted.mkdir()
    (unfingerprinted / "meta.json").write_text(json.dumps({"model_id": "m1"}), encoding="utf-8")
    with pytest.raises(DataError, match="缺 artifact 指紋"):
        read_model_identity(unfingerprinted)


def test_ac213_1_inference_produced_before_the_days_cutoff_cannot_claim_that_day() -> None:
    # A run generated at 13:00 cannot be published as "as of the 20:00 signal cutoff" (D-008).
    with pytest.raises(DataError, match="早於該日訊號截點"):
        snapshot_from_scores(
            _rows(("0050", 0.92), stamp=dt.datetime(2026, 9, 4, 13, 0, tzinfo=TZ)),
            model_id="lgbm_net_h20_20260225",
            day=DAY,
            artifact_fingerprint="fp-abc",
        )
