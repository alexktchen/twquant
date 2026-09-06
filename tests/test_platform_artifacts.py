"""T-202 / SPEC-014 §3: read-only platform artifact index."""

from __future__ import annotations

import json
from pathlib import Path

from twquant.reporting.platform_artifacts import backtest_detail, index_backtests, index_models


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_ac202_1_model_index_is_stable_read_only_and_hides_vault_fields(tmp_path: Path) -> None:
    root = tmp_path / "models"
    meta = {
        "model_id": "lgbm_net_h20_20260225_6a798962",
        "train_end": "2026-02-25",
        "features": ["price.mom_20d"],
        "horizon_days": 20,
        "label": "cost_adjusted_return",
        "artifact_fingerprint": "safe-fingerprint",
        "config_hash": "cfg",
        "data_hash": "data",
        "code_hash": "code",
        "oos_rank_ic": 0.0452,
        "vault_oos_rank_ic": 0.99,
        "secret": "must never enter a platform projection",
    }
    meta_path = root / "lgbm_net_h20_20260225_6a798962" / "meta.json"
    _write(meta_path, meta)
    before = meta_path.read_bytes()

    first = index_models(root)
    second = index_models(root)

    assert first.items == second.items
    assert len(first.items) == 1 and first.issues == []
    item = first.items[0]
    assert item["model_id"] == meta["model_id"]
    assert item["execution_status"] == "unavailable"
    assert item["qualification"] == "unreviewed"
    assert isinstance(item["artifact_id"], str) and item["artifact_id"].startswith("model_")
    assert "vault_oos_rank_ic" not in item and "secret" not in item
    assert str(root) not in json.dumps(item)
    assert meta_path.read_bytes() == before


def test_ac202_1_legacy_model_keeps_unknown_fields_without_inventing_history(
    tmp_path: Path,
) -> None:
    root = tmp_path / "models"
    _write(root / "legacy" / "meta.json", {"model_id": "legacy-model", "features": []})

    item = index_models(root).items[0]

    assert item["train_end"] is None
    assert item["started_at"] is None and item["ended_at"] is None
    assert item["window_ics"] is None


def test_ac202_2_index_marks_archive_rejected_and_corrupt_metadata(tmp_path: Path) -> None:
    root = tmp_path / "models"
    _write(root / "_archive" / "old" / "meta.json", {"model_id": "archived"})
    _write(root / "_rejected" / "bad" / "meta.json", {"model_id": "rejected"})
    corrupt = root / "broken" / "meta.json"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("not json", encoding="utf-8")

    catalog = index_models(root)

    assert [item["artifact_state"] for item in catalog.items] == ["archived", "rejected"]
    assert len(catalog.issues) == 1
    assert catalog.issues[0]["artifact_id"].startswith("model_")
    assert "broken" not in json.dumps(catalog.issues[0])


def test_ac202_1_backtest_index_projects_existing_bundle_without_recalculation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "results"
    bundle = {
        "schema_version": 1,
        "strategy": {"name": "large_cap"},
        "start": "2023-01-01",
        "end": "2023-12-31",
        "source": "pg",
        "data_fingerprint": "fprint",
        "initial_capital": 80_000_000,
        "max_participation": 0.05,
        "metrics": {"cagr": 0.1, "sharpe": 1.2, "mdd": -0.2, "turnover": 1.0},
        "equity": [["2023-01-02", 80_000_000, 80_000_000, 0]],
        "ledger": [],
        "unfilled": [],
        "final_holdings": [],
    }
    path = root / "large_cap.json"
    _write(path, bundle)
    before = path.read_bytes()

    catalog = index_backtests({"ladder": root})

    assert len(catalog.items) == 1 and catalog.issues == []
    item = catalog.items[0]
    assert isinstance(item["artifact_id"], str) and item["artifact_id"].startswith("backtest_")
    assert item["strategy_name"] == "large_cap"
    assert item["metrics"] == bundle["metrics"]
    assert item["artifact_state"] == "active"
    assert str(root) not in json.dumps(item)
    assert path.read_bytes() == before


def test_ac202_2_backtest_index_reports_unsupported_schema_without_silently_dropping(
    tmp_path: Path,
) -> None:
    root = tmp_path / "results"
    _write(root / "_rejected" / "old.json", {"schema_version": 0})

    catalog = index_backtests({"rejected": root})

    assert catalog.items == []
    assert len(catalog.issues) == 1
    assert catalog.issues[0]["artifact_id"].startswith("backtest_")
    assert "old.json" not in json.dumps(catalog.issues[0])


def test_ac202_2_backtest_index_reports_non_object_without_leaking_root(tmp_path: Path) -> None:
    root = tmp_path / "private_results"
    path = root / "not-a-bundle.json"
    _write(path, ["not", "a", "bundle"])

    catalog = index_backtests({"research": root})

    assert catalog.items == []
    assert catalog.issues[0]["detail"] == "result unavailable: artifact JSON must be an object"
    assert str(root) not in json.dumps(catalog.issues[0])


def test_ac204_2_backtest_detail_keeps_stored_equity_and_holdings_without_recalculation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "results"
    payload = {
        "schema_version": 1,
        "strategy": {"name": "strategy"},
        "metrics": {"cagr": 0.1},
        "equity": [["2023-01-02", 80_000_000, 79_000_000, 1_000_000]],
        "final_holdings": [
            {
                "stock_id": "0050",
                "shares": 1000,
                "last_close": 50.0,
                "value": 50_000.0,
                "weight": 0.1,
            }
        ],
    }
    _write(root / "strategy.json", payload)
    artifact_id = index_backtests({"ladder": root}).items[0]["artifact_id"]
    assert isinstance(artifact_id, str)

    detail = backtest_detail({"ladder": root}, artifact_id)

    assert detail is not None
    assert detail["equity"] == [
        {
            "date": "2023-01-02",
            "equity": 80_000_000,
            "cash": 79_000_000,
            "positions_value": 1_000_000,
        }
    ]
    assert detail["final_holdings"] == payload["final_holdings"]
