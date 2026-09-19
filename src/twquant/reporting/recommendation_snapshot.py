"""Immutable, validated daily ML recommendation snapshots (SPEC-014 §4)."""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from twquant.conventions import assert_tw_timestamp, normalize_stock_id
from twquant.errors import DataError, TwquantError

SNAPSHOT_SCHEMA_VERSION: Literal[1] = 1
_SNAPSHOT_DIR = "recommendations"
_SAFE_ID = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
_RUN_ID = re.compile(_SAFE_ID)
_DATA_STATUSES = frozenset({"ready", "pending", "stale", "unavailable"})


class RecommendationCandidate(BaseModel):
    """One raw ML-ranked candidate with optional PIT-valid insight linkage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stock_id: str = Field(min_length=1)
    rank: int = Field(ge=1)
    score: float
    bucket: str = Field(min_length=1)
    reason_ref: str | None = None
    insight_run_id: str | None = None
    insight_available_at: dt.datetime | None = None
    data_status: str

    @field_validator("stock_id", mode="before")
    @classmethod
    def _stock_id_is_string(cls, value: object) -> str:
        """Keep stock identity as a string so leading zeroes cannot be lost."""
        if not isinstance(value, str):
            raise ValueError("stock_id 必須是字串")
        return normalize_stock_id(value)

    @field_validator("score")
    @classmethod
    def _score_is_finite(cls, value: float) -> float:
        """Reject non-finite scores instead of serializing an ambiguous JSON value."""
        if not math.isfinite(value):
            raise ValueError("score 必須是有限數值")
        return value

    @field_validator("insight_available_at")
    @classmethod
    def _insight_timestamp_is_taipei(cls, value: dt.datetime | None) -> dt.datetime | None:
        """Require an aware Taipei timestamp when a linked insight has a timestamp."""
        if value is None:
            return None
        try:
            return assert_tw_timestamp(value)
        except TwquantError as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode="after")
    def _validate_insight_reference(self) -> RecommendationCandidate:
        """Require both sides of an insight timing reference or neither side."""
        if (self.insight_run_id is None) != (self.insight_available_at is None):
            raise ValueError("insight_run_id 與 insight_available_at 必須同時提供或同時省略")
        if self.data_status not in _DATA_STATUSES:
            raise ValueError(f"data_status 必須為 {sorted(_DATA_STATUSES)} 之一")
        return self


class RecommendationSnapshot(BaseModel):
    """A complete, immutable ML inference run for daily research display."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = SNAPSHOT_SCHEMA_VERSION
    run_id: str = Field(pattern=_SAFE_ID)
    model_id: str = Field(min_length=1)
    as_of: dt.datetime
    generated_at: dt.datetime
    published_at: dt.datetime | None = None
    mode: Literal["research", "reconstructed"]
    artifact_fingerprint: str = Field(min_length=1)
    candidates: tuple[RecommendationCandidate, ...]

    @field_validator("as_of", "generated_at", "published_at")
    @classmethod
    def _timestamp_is_taipei(cls, value: dt.datetime | None) -> dt.datetime | None:
        """Reject naive and non-Taipei timestamps at the persistence boundary."""
        if value is None:
            return None
        try:
            return assert_tw_timestamp(value)
        except TwquantError as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode="after")
    def _validate_integrity(self) -> RecommendationSnapshot:
        """Enforce source ranking, PIT insight timing and publication semantics."""
        if self.generated_at < self.as_of:
            raise ValueError("generated_at 不得早於 as_of")
        if self.published_at is not None and self.published_at < self.generated_at:
            raise ValueError("published_at 不得早於 generated_at")
        if self.mode == "reconstructed" and self.published_at is not None:
            raise ValueError("reconstructed 快照不得宣稱向前發布")
        stock_ids = [candidate.stock_id for candidate in self.candidates]
        ranks = [candidate.rank for candidate in self.candidates]
        if len(stock_ids) != len(set(stock_ids)):
            raise ValueError("candidates 不得有重複 stock_id")
        if len(ranks) != len(set(ranks)):
            raise ValueError("candidates 不得有重複 rank")
        for candidate in self.candidates:
            if (
                candidate.insight_available_at is not None
                and candidate.insight_available_at > self.as_of
            ):
                raise ValueError(
                    f"stock_id={candidate.stock_id} 的 insight_available_at 晚於 as_of，屬前視事件"
                )
        return self


@dataclass(frozen=True)
class SnapshotWriteResult:
    """The immutable target path and whether this invocation created it."""

    path: Path
    created: bool


@dataclass(frozen=True)
class RecommendationCatalog:
    """Saved snapshots and client-safe issues for snapshots that cannot be read."""

    runs: list[RecommendationSnapshot]
    issues: list[dict[str, str]]


def snapshot_path(data_dir: str | Path, run_id: str) -> Path:
    """Return the fixed server-side path for a validated opaque run identifier."""
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise DataError("recommendation_snapshot", None, "run_id 格式錯誤")
    return Path(data_dir) / _SNAPSHOT_DIR / f"{run_id}.json"


def _canonical_bytes(snapshot: RecommendationSnapshot) -> bytes:
    """Serialize a valid snapshot deterministically for idempotency comparison."""
    payload = snapshot.model_dump(mode="json")
    return (json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def read_snapshot(path: str | Path) -> RecommendationSnapshot:
    """Read and validate one JSON snapshot without attempting any inference."""
    source = Path(path)
    try:
        payload = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise DataError("recommendation_snapshot", None, "快照檔無法讀取") from exc
    try:
        return RecommendationSnapshot.model_validate_json(payload)
    except (ValidationError, ValueError) as exc:
        raise DataError("recommendation_snapshot", None, "快照合約無效") from exc


def save_snapshot(data_dir: str | Path, snapshot: RecommendationSnapshot) -> SnapshotWriteResult:
    """Atomically create one immutable run; equal repeats succeed and conflicts raise."""
    target = snapshot_path(data_dir, snapshot.run_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    contents = _canonical_bytes(snapshot)
    temporary = target.with_name(f"{target.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        with temporary.open("xb") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            existing = read_snapshot(target)
            if _canonical_bytes(existing) != contents:
                raise DataError(
                    "recommendation_snapshot",
                    None,
                    f"run_id={snapshot.run_id} 已存在不同內容，拒絕覆寫",
                ) from None
            return SnapshotWriteResult(path=target, created=False)
    except OSError as exc:
        raise DataError("recommendation_snapshot", None, "快照無法原子保存") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return SnapshotWriteResult(path=target, created=True)


def import_snapshot(source: str | Path, data_dir: str | Path) -> SnapshotWriteResult:
    """Validate an operator-supplied contract file then save it under the fixed snapshot root."""
    return save_snapshot(data_dir, read_snapshot(source))


def index_snapshots(data_dir: str | Path) -> RecommendationCatalog:
    """Read every saved snapshot without starting inference or hiding invalid artifacts."""
    root = Path(data_dir) / _SNAPSHOT_DIR
    if not root.exists():
        return RecommendationCatalog(runs=[], issues=[])
    runs: list[RecommendationSnapshot] = []
    issues: list[dict[str, str]] = []
    for path in sorted(root.glob("*.json"), key=lambda item: item.name):
        try:
            runs.append(read_snapshot(path))
        except DataError:
            run_id = path.stem if _RUN_ID.fullmatch(path.stem) else "unreadable_snapshot"
            issues.append({"run_id": run_id, "detail": "recommendation snapshot is invalid"})
    return RecommendationCatalog(runs=runs, issues=issues)


def select_candidates(
    run: RecommendationSnapshot,
    *,
    bucket: str | None = None,
    query: str | None = None,
) -> tuple[RecommendationCandidate, ...]:
    """Apply display filters to one run while preserving each candidate's original rank."""
    needle = query.strip().casefold() if query else None
    return tuple(
        candidate
        for candidate in run.candidates
        if (bucket is None or candidate.bucket == bucket)
        and (needle is None or needle in candidate.stock_id.casefold())
    )


def stock_events(data_dir: str | Path, stock_id: str) -> list[dict[str, object]]:
    """Return one stock's saved recommendation events in as_of order; no events yields []."""
    wanted = normalize_stock_id(stock_id)
    catalog = index_snapshots(data_dir)
    events: list[dict[str, object]] = []
    for run in sorted(catalog.runs, key=lambda item: (item.as_of, item.run_id)):
        for candidate in run.candidates:
            if candidate.stock_id != wanted:
                continue
            events.append(
                {
                    "run_id": run.run_id,
                    "model_id": run.model_id,
                    "stock_id": candidate.stock_id,
                    "as_of": run.as_of.isoformat(),
                    "published_at": (
                        run.published_at.isoformat() if run.published_at is not None else None
                    ),
                    "mode": run.mode,
                    "rank": candidate.rank,
                    "score": candidate.score,
                    "bucket": candidate.bucket,
                    "reason_ref": candidate.reason_ref,
                    "insight_run_id": candidate.insight_run_id,
                    "data_status": candidate.data_status,
                }
            )
    return events


def select_snapshots(
    data_dir: str | Path,
    *,
    date: dt.date | None = None,
    model_id: str | None = None,
    run_id: str | None = None,
) -> RecommendationCatalog:
    """Apply date, model and run filters before callers perform any pagination."""
    catalog = index_snapshots(data_dir)
    runs = [
        run
        for run in catalog.runs
        if (date is None or run.as_of.date() == date)
        and (model_id is None or run.model_id == model_id)
        and (run_id is None or run.run_id == run_id)
    ]
    # 新到舊：頁面寫的是「查詢日期：最新可得」，最上面那張卡就該是最新的 run。
    # 原本依檔名（＝由舊到新）排，於是不帶日期時永遠先看到最舊、早已過期的快照，
    # 最新的被壓在最底下（2026-09-19 實際發生：09-18 的名單排在 09-04 的過期快照後面）。
    runs.sort(key=lambda run: (run.as_of, run.generated_at), reverse=True)
    return RecommendationCatalog(runs=runs, issues=catalog.issues)
