"""T-214 / SPEC-014 §7: retraining export contract tests."""

from __future__ import annotations

import datetime as dt
from typing import Any, cast

import pytest

from twquant.errors import DataError
from twquant.reporting.insight_evidence import InsightPack
from twquant.reporting.recommendation_snapshot import RecommendationSnapshot
from twquant.reporting.retrain_export import RETRAIN_COLUMNS, build_export

LABEL: dict[str, str] = {
    "source": "prices_daily 已實現前瞻報酬",
    "execution": "next_open",
    "excess": "market_mean",
    "transform": "rank_pct",
    "cost_ref": "SPEC-006 §3 成本模型",
    "spec_ref": "SPEC-010 §1 標籤定義",
}


def _candidate(stock_id: str, rank: int, **patch: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "stock_id": stock_id,
        "rank": rank,
        "score": 0.5,
        "bucket": "ml_candidate",
        "reason_ref": None,
        "insight_run_id": None,
        "insight_available_at": None,
        "data_status": "pending",
    }
    row.update(patch)
    return row


def _snapshot(candidates: list[dict[str, Any]]) -> RecommendationSnapshot:
    return RecommendationSnapshot.model_validate(
        {
            "schema_version": 1,
            "run_id": "mlscore_20260904_abc",
            "model_id": "lgbm_net_h20",
            "as_of": "2026-09-04T19:00:00+08:00",
            "generated_at": "2026-09-04T19:05:00+08:00",
            "published_at": None,
            "mode": "research",
            "artifact_fingerprint": "fp-abc",
            "candidates": candidates,
        }
    )


def _pack(run_id: str = "ins_1") -> InsightPack:
    return InsightPack.model_validate(
        {
            "schema_version": 1,
            "insight_run_id": run_id,
            "as_of": "2026-09-04T19:00:00+08:00",
            "stock_id": "0050",
            "industry": None,
            "evidence": [
                {
                    "evidence_id": f"ev_{kind}",
                    "title": kind,
                    "url": "https://source.example/x",
                    "published_at": "2026-09-03T08:00:00+08:00",
                    "available_at": "2026-09-03T09:00:00+08:00",
                    "captured_at": "2026-09-03T10:00:00+08:00",
                    "stock_id": "0050",
                    "industry": None,
                    "direction": kind,
                    "reason_ref": None,
                }
                for kind in ("support", "contradict", "gap")
            ],
            "analysis": {
                "prompt_id": "insight_v1",
                "prompt_hash": "h1",
                "analysis_schema_version": 1,
                "model_version": "claude-x-2026",
                "as_of": "2026-09-04T19:00:00+08:00",
                "summary": "營收支持、籌碼反向。",
                "cited_evidence_ids": ["ev_support"],
            },
            "analysis_absent_reason": None,
        }
    )


def test_ac214_1_point_in_time_identity_travels_with_every_row() -> None:
    export = build_export(
        _snapshot([_candidate("0050", 1), _candidate("2330", 2)]),
        horizon_days=20,
        label=LABEL,
    )

    assert [row["stock_id"] for row in export.rows] == ["0050", "2330"]
    for row in export.rows:
        assert row["run_id"] == "mlscore_20260904_abc"
        assert row["model_id"] == "lgbm_net_h20"
        assert row["artifact_fingerprint"] == "fp-abc"
        assert cast(dt.datetime, row["as_of"]).isoformat() == "2026-09-04T19:00:00+08:00"
        assert row["horizon_days"] == 20
    # The export ships its own dictionary so a null target cannot be misread as a zero.
    assert set(export.columns) == set(RETRAIN_COLUMNS)
    assert "尚未成熟時為 null" in export.columns["target"]


def test_ac214_1_an_immature_outcome_is_null_and_never_imputed() -> None:
    export = build_export(
        _snapshot([_candidate("0050", 1), _candidate("2330", 2), _candidate("0056", 3)]),
        horizon_days=20,
        label=LABEL,
        outcomes={"0050": 0.031},
    )

    by_stock = {row["stock_id"]: row for row in export.rows}
    assert by_stock["0050"]["target"] == 0.031
    assert by_stock["0050"]["target_status"] == "ready"
    for stock_id in ("2330", "0056"):
        assert by_stock[stock_id]["target"] is None  # Not 0.0, and not dropped.
        assert by_stock[stock_id]["target_status"] == "immature"
    assert export.matured == 1
    assert len(export.rows) == 3


def test_ac214_1_excluded_candidates_stay_in_the_export() -> None:
    export = build_export(
        _snapshot([_candidate("0050", 1), _candidate("2330", 2, bucket="excluded")]),
        horizon_days=20,
        label=LABEL,
        outcomes={"0050": 0.02, "2330": -0.04},
        excluded_buckets=frozenset({"excluded"}),
    )

    by_stock = {row["stock_id"]: row for row in export.rows}
    # An excluded name still carries its realised outcome, so exclusion cannot hide a loss.
    assert by_stock["2330"]["excluded"] is True
    assert by_stock["2330"]["target"] == -0.04
    assert by_stock["0050"]["excluded"] is False


def test_ac214_1_insight_output_is_recorded_as_features_only() -> None:
    export = build_export(
        _snapshot(
            [
                _candidate(
                    "0050",
                    1,
                    insight_run_id="ins_1",
                    insight_available_at="2026-09-04T09:00:00+08:00",
                    data_status="ready",
                ),
                _candidate("2330", 2),
            ]
        ),
        horizon_days=20,
        label=LABEL,
        insight_packs=[_pack()],
    )

    analysed = export.rows[0]
    assert analysed["insight_run_id"] == "ins_1"
    assert analysed["insight_model_version"] == "claude-x-2026"
    assert (analysed["insight_support_n"], analysed["insight_contradict_n"]) == (1, 1)
    assert analysed["insight_gap_n"] == 1
    # The analysis never becomes the label; without a realised outcome the target stays null.
    assert analysed["target"] is None
    assert export.rows[1]["insight_run_id"] is None
    assert export.rows[1]["insight_support_n"] == 0


def test_ac214_1_a_model_generated_label_source_is_refused() -> None:
    for bad_source in ("insight 分析分數", "LLM 判斷", "ml_score", "model output"):
        with pytest.raises(DataError, match="拒絕當作真實標籤"):
            build_export(
                _snapshot([_candidate("0050", 1)]),
                horizon_days=20,
                label={**LABEL, "source": bad_source},
            )


def test_ac214_1_an_incomplete_label_definition_or_stray_outcome_is_refused() -> None:
    with pytest.raises(DataError, match="標籤定義缺欄位"):
        build_export(
            _snapshot([_candidate("0050", 1)]),
            horizon_days=20,
            label={"source": "prices_daily 已實現前瞻報酬"},
        )
    with pytest.raises(DataError, match="不在候選中的股票"):
        build_export(
            _snapshot([_candidate("0050", 1)]),
            horizon_days=20,
            label=LABEL,
            outcomes={"9999": 0.1},
        )
    with pytest.raises(DataError, match="horizon_days"):
        build_export(_snapshot([_candidate("0050", 1)]), horizon_days=0, label=LABEL)
