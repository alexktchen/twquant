"""Read-only, safe projections of saved model and backtest artifacts (SPEC-014 §3)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from twquant.errors import DataError
from twquant.reporting.result_bundle import read_bundle


@dataclass(frozen=True)
class ArtifactCatalog:
    """Index response for one artifact type, including files that could not be read safely."""

    items: list[dict[str, object]]
    issues: list[dict[str, str]]


def _artifact_id(kind: str, scope: str, relative_path: Path) -> str:
    """Return a stable opaque ID without exposing an artifact filesystem path."""
    identity = f"{kind}:{scope}:{relative_path.as_posix()}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"{kind}_{digest}"


def _artifact_state(relative_path: Path) -> str:
    """Classify retained history from explicit archive/rejection directory names."""
    parts = {part.lower() for part in relative_path.parts}
    if "_rejected" in parts or "rejected" in parts:
        return "rejected"
    if "_archive" in parts or "archive" in parts:
        return "archived"
    return "active"


def _read_object(path: Path) -> dict[str, object]:
    """Read one JSON object and reject non-object documents before projecting fields."""
    decoded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("artifact JSON must be an object")
    return decoded


def _optional_string(value: object, field: str) -> str | None:
    """Accept an absent string field or raise an explicit format error."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise ValueError(f"{field} must be a string")


def _optional_date(value: object, field: str) -> str | None:
    """Accept an absent ISO date field or raise an explicit format error."""
    text = _optional_string(value, field)
    if text is not None:
        dt.date.fromisoformat(text)
    return text


def _optional_number(value: object, field: str) -> float | int | None:
    """Accept a finite numeric field while refusing bool, NaN and infinity."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    return value


def _optional_features(value: object) -> list[str] | None:
    """Return a legacy-safe feature list without guessing malformed values."""
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(feature, str) for feature in value):
        raise ValueError("features must be a list of strings")
    return value


def _optional_window_ics(value: object) -> list[float | int] | None:
    """Return finite development-window IC values only when their shape is known."""
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("window_ics must be a list")
    result: list[float | int] = []
    for index, item in enumerate(value):
        number = _optional_number(item, f"window_ics[{index}]")
        if number is None:
            raise ValueError(f"window_ics[{index}] must not be null")
        result.append(number)
    return result


def _issue(artifact_id: str, kind: str, detail: str) -> dict[str, str]:
    """Build a client-safe issue without leaking filesystem names or root paths."""
    return {"artifact_id": artifact_id, "kind": kind, "detail": detail}


def _safe_problem_detail(exc: Exception) -> str:
    """Describe a bad artifact without placing local paths in a client-visible issue."""
    if isinstance(exc, OSError):
        return "artifact file could not be read"
    if isinstance(exc, UnicodeDecodeError):
        return "artifact is not valid UTF-8"
    if isinstance(exc, json.JSONDecodeError):
        return "artifact is not valid JSON"
    if isinstance(exc, DataError):
        return exc.detail
    return str(exc)


def _model_projection(meta: dict[str, object], artifact_id: str, state: str) -> dict[str, object]:
    """Select permitted model metadata, deliberately excluding vault and arbitrary fields."""
    return {
        "artifact_id": artifact_id,
        "artifact_state": state,
        "model_id": _optional_string(meta.get("model_id"), "model_id"),
        "train_end": _optional_date(meta.get("train_end"), "train_end"),
        "features": _optional_features(meta.get("features")),
        "horizon_days": _optional_number(meta.get("horizon_days"), "horizon_days"),
        "label": _optional_string(meta.get("label"), "label"),
        "artifact_fingerprint": _optional_string(
            meta.get("artifact_fingerprint"), "artifact_fingerprint"
        ),
        "config_hash": _optional_string(meta.get("config_hash"), "config_hash"),
        "data_hash": _optional_string(meta.get("data_hash"), "data_hash"),
        "code_hash": _optional_string(meta.get("code_hash"), "code_hash"),
        "oos_rank_ic": _optional_number(meta.get("oos_rank_ic"), "oos_rank_ic"),
        "window_ics": _optional_window_ics(meta.get("window_ics")),
        "started_at": None,
        "ended_at": None,
        "execution_status": "unavailable",
        "qualification": "unreviewed",
    }


def index_models(models_root: Path) -> ArtifactCatalog:
    """Index model meta files without loading boosters or changing model artifacts."""
    if not models_root.exists():
        return ArtifactCatalog(items=[], issues=[])
    items: list[dict[str, object]] = []
    issues: list[dict[str, str]] = []
    for path in sorted(models_root.rglob("meta.json"), key=lambda item: item.as_posix()):
        relative = path.relative_to(models_root)
        artifact_id = _artifact_id("model", "models", relative)
        try:
            meta = _read_object(path)
            items.append(_model_projection(meta, artifact_id, _artifact_state(relative)))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            issues.append(
                _issue(artifact_id, "model", f"metadata unavailable: {_safe_problem_detail(exc)}")
            )
    return ArtifactCatalog(items=items, issues=issues)


def _metric_projection(raw_metrics: object) -> dict[str, float | int]:
    """Keep only known finite metrics from a bundle and never recalculate them."""
    if not isinstance(raw_metrics, dict):
        raise ValueError("metrics must be an object")
    known = ("cagr", "sharpe", "sortino", "mdd", "calmar", "turnover", "win_rate", "n_days")
    metrics: dict[str, float | int] = {}
    for field in known:
        value = _optional_number(raw_metrics.get(field), f"metrics.{field}")
        if value is not None:
            metrics[field] = value
    return metrics


def _backtest_projection(
    bundle: dict[str, Any], artifact_id: str, state: str, source_name: str
) -> dict[str, object]:
    """Select an immutable result-bundle summary for platform list views."""
    strategy = bundle.get("strategy")
    if strategy is not None and not isinstance(strategy, dict):
        raise ValueError("strategy must be an object")
    strategy_name = _optional_string(strategy.get("name") if strategy else None, "strategy.name")
    return {
        "artifact_id": artifact_id,
        "artifact_state": state,
        "source_name": source_name,
        "strategy_name": strategy_name,
        "start": _optional_date(bundle.get("start"), "start"),
        "end": _optional_date(bundle.get("end"), "end"),
        "source": _optional_string(bundle.get("source"), "source"),
        "data_fingerprint": _optional_string(bundle.get("data_fingerprint"), "data_fingerprint"),
        "initial_capital": _optional_number(bundle.get("initial_capital"), "initial_capital"),
        "max_participation": _optional_number(bundle.get("max_participation"), "max_participation"),
        "metrics": _metric_projection(bundle.get("metrics")),
    }


def index_backtests(roots: Mapping[str, Path]) -> ArtifactCatalog:
    """Index result bundles below named roots without returning source filesystem paths."""
    items: list[dict[str, object]] = []
    issues: list[dict[str, str]] = []
    for source_name, root in sorted(roots.items()):
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.json"), key=lambda item: item.as_posix()):
            relative = path.relative_to(root)
            artifact_id = _artifact_id("backtest", source_name, relative)
            try:
                _read_object(path)
                bundle = read_bundle(path)
                items.append(
                    _backtest_projection(
                        bundle, artifact_id, _artifact_state(relative), source_name
                    )
                )
            except (
                DataError,
                OSError,
                UnicodeDecodeError,
                json.JSONDecodeError,
                ValueError,
            ) as exc:
                issues.append(
                    _issue(
                        artifact_id,
                        "backtest",
                        f"result unavailable: {_safe_problem_detail(exc)}",
                    )
                )
    return ArtifactCatalog(items=items, issues=issues)
