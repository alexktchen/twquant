"""T-203 / SPEC-014 §3: platform artifact API boundary tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import HTTPException

from twquant.monitoring import api


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _roots(tmp_path: Path) -> api.PlatformArtifactRoots:
    return api.PlatformArtifactRoots(
        models_root=tmp_path / "models", backtest_roots={"ladder": tmp_path / "results"}
    )


def _artifact_id(payload: dict[str, object]) -> str:
    """Extract one opaque ID from a list payload with a checked runtime shape."""
    items = payload["items"]
    assert isinstance(items, list) and items
    item = items[0]
    assert isinstance(item, dict)
    artifact_id = item["artifact_id"]
    assert isinstance(artifact_id, str)
    return artifact_id


def _detail_item(payload: dict[str, object]) -> dict[str, object]:
    """Extract a checked artifact projection from one detail response."""
    item = payload["item"]
    assert isinstance(item, dict)
    return item


def test_ac203_1_model_list_and_detail_use_opaque_ids(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    _write(roots.models_root / "model-a" / "meta.json", {"model_id": "model-a"})

    listed = api.get_platform_models(roots)
    artifact_id = _artifact_id(listed)

    assert listed["source"] == "artifact_metadata"
    assert isinstance(artifact_id, str) and artifact_id.startswith("model_")
    assert str(roots.models_root) not in json.dumps(listed)
    assert _detail_item(api.get_platform_model(artifact_id, roots))["model_id"] == "model-a"


def test_ac203_1_backtest_list_and_detail_do_not_trigger_calculation(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    path = roots.backtest_roots["ladder"] / "strategy.json"
    _write(
        path,
        {
            "schema_version": 1,
            "strategy": {"name": "strategy"},
            "metrics": {"cagr": 0.1},
            "equity": [],
        },
    )
    before = path.read_bytes()

    listed = api.get_platform_backtests(roots)
    artifact_id = _artifact_id(listed)

    assert listed["source"] == "result_bundle"
    assert (
        _detail_item(api.get_platform_backtest(artifact_id, roots))["strategy_name"] == "strategy"
    )
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("getter", "artifact_id"),
    [
        (api.get_platform_model, "../../meta.json"),
        (api.get_platform_backtest, "backtest_not-opaque"),
    ],
)
def test_ac203_1_malformed_ids_are_422(
    tmp_path: Path,
    getter: Callable[[str, api.PlatformArtifactRoots], dict[str, object]],
    artifact_id: str,
) -> None:
    roots = _roots(tmp_path)
    with pytest.raises(HTTPException) as exc_info:
        getter(artifact_id, roots)
    assert exc_info.value.status_code == 422


def test_ac203_1_missing_opaque_artifact_is_404(tmp_path: Path) -> None:
    with pytest.raises(HTTPException) as exc_info:
        api.get_platform_model("model_0123456789abcdef0123", _roots(tmp_path))
    assert exc_info.value.status_code == 404


def test_ac203_1_unreadable_artifact_root_is_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = _roots(tmp_path)

    def fail(_: Path) -> object:
        raise OSError("private local path must not reach clients")

    monkeypatch.setattr(api, "index_models", fail)
    with pytest.raises(HTTPException) as exc_info:
        api.get_platform_models(roots)
    assert exc_info.value.status_code == 503
    assert "private local path" not in str(exc_info.value.detail)


def test_ac203_1_app_registers_read_only_platform_routes() -> None:
    paths = {getattr(route, "path", None) for route in api.app.routes}
    assert {
        "/api/platform/models",
        "/api/platform/models/{artifact_id}",
        "/api/platform/backtests",
        "/api/platform/backtests/{artifact_id}",
    } <= paths
