"""Structured insight evidence packs with citation and cutoff validation (SPEC-014 §6).

Evidence text is data. Nothing in a pack is ever executed, resolved as a tool instruction or
fetched; this module only validates a supplied document and reads it back.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from twquant.conventions import assert_tw_timestamp, normalize_stock_id
from twquant.errors import DataError, TwquantError

INSIGHT_SCHEMA_VERSION: Literal[1] = 1
_INSIGHT_DIR = "insights"
_SAFE_ID = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
_RUN_ID = re.compile(_SAFE_ID)


def _taipei(value: dt.datetime) -> dt.datetime:
    """Reject naive and non-Taipei timestamps at the persistence boundary."""
    try:
        return assert_tw_timestamp(value)
    except TwquantError as exc:
        raise ValueError(str(exc)) from exc


class InsightEvidence(BaseModel):
    """One captured source item; its text is quoted evidence, never an instruction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(pattern=_SAFE_ID)
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    published_at: dt.datetime
    available_at: dt.datetime
    captured_at: dt.datetime
    stock_id: str | None = None
    industry: str | None = None
    direction: Literal["support", "contradict", "gap"]
    reason_ref: str | None = None

    @field_validator("published_at", "available_at", "captured_at")
    @classmethod
    def _timestamps_are_taipei(cls, value: dt.datetime) -> dt.datetime:
        """Require aware Taipei timestamps so cutoff comparisons are unambiguous."""
        return _taipei(value)

    @field_validator("stock_id", mode="before")
    @classmethod
    def _stock_id_is_string(cls, value: object) -> str | None:
        """Keep stock identity a string so leading zeroes are never lost."""
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("stock_id 必須是字串")
        return normalize_stock_id(value)

    @model_validator(mode="after")
    def _validate_timeline(self) -> InsightEvidence:
        """A source cannot become available before publication, or be captured before that."""
        if self.available_at < self.published_at:
            raise ValueError(f"evidence_id={self.evidence_id} 的 available_at 早於 published_at")
        if self.captured_at < self.available_at:
            raise ValueError(f"evidence_id={self.evidence_id} 的 captured_at 早於 available_at")
        return self


class InsightAnalysis(BaseModel):
    """The generated reading of a pack, saved together with how it was produced."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_id: str = Field(min_length=1)
    prompt_hash: str = Field(min_length=1)
    analysis_schema_version: int = Field(ge=1)
    model_version: str = Field(min_length=1)
    as_of: dt.datetime
    summary: str = Field(min_length=1)
    cited_evidence_ids: tuple[str, ...]

    @field_validator("as_of")
    @classmethod
    def _as_of_is_taipei(cls, value: dt.datetime) -> dt.datetime:
        """Analysis cutoffs are compared against evidence timing, so they must be aware."""
        return _taipei(value)

    @model_validator(mode="after")
    def _citations_are_unique(self) -> InsightAnalysis:
        """A repeated citation would overstate how much evidence supports a claim."""
        if len(self.cited_evidence_ids) != len(set(self.cited_evidence_ids)):
            raise ValueError("cited_evidence_ids 不得重複")
        return self


class InsightPack(BaseModel):
    """Evidence plus an optional analysis; an absent analysis must say why it is absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = INSIGHT_SCHEMA_VERSION
    insight_run_id: str = Field(pattern=_SAFE_ID)
    as_of: dt.datetime
    stock_id: str | None = None
    industry: str | None = None
    evidence: tuple[InsightEvidence, ...]
    analysis: InsightAnalysis | None = None
    analysis_absent_reason: str | None = None

    @field_validator("as_of")
    @classmethod
    def _as_of_is_taipei(cls, value: dt.datetime) -> dt.datetime:
        """The pack cutoff decides which evidence may be cited, so it must be aware."""
        return _taipei(value)

    @field_validator("stock_id", mode="before")
    @classmethod
    def _stock_id_is_string(cls, value: object) -> str | None:
        """Keep stock identity a string so leading zeroes are never lost."""
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("stock_id 必須是字串")
        return normalize_stock_id(value)

    @model_validator(mode="after")
    def _validate_pack(self) -> InsightPack:
        """Reject unknown citations, post-cutoff citations and analyses that vanish silently."""
        ids = [item.evidence_id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("evidence 不得有重複 evidence_id")
        if self.analysis is None:
            if not self.analysis_absent_reason:
                raise ValueError("沒有分析時必須說明 analysis_absent_reason")
            return self
        if self.analysis_absent_reason:
            raise ValueError("已有分析時不得同時宣稱分析缺席")
        if self.analysis.as_of != self.as_of:
            raise ValueError("analysis.as_of 必須與證據包的 as_of 相同")
        known = {item.evidence_id: item for item in self.evidence}
        for cited in self.analysis.cited_evidence_ids:
            source = known.get(cited)
            if source is None:
                raise ValueError(f"分析引用了不存在的 evidence_id={cited}")
            if source.available_at > self.as_of:
                raise ValueError(f"evidence_id={cited} 在截點之後才可得，屬前視引用")
        return self

    def gaps(self) -> tuple[InsightEvidence, ...]:
        """Evidence explicitly recorded as a gap rather than support or contradiction."""
        return tuple(item for item in self.evidence if item.direction == "gap")


@dataclass(frozen=True)
class InsightWriteResult:
    """The immutable target path and whether this invocation created it."""

    path: Path
    created: bool


@dataclass(frozen=True)
class InsightCatalog:
    """Saved packs plus client-safe issues for packs that cannot be read."""

    packs: list[InsightPack]
    issues: list[dict[str, str]]


def insight_path(data_dir: str | Path, insight_run_id: str) -> Path:
    """Return the fixed server-side path for a validated opaque insight run identifier."""
    if not isinstance(insight_run_id, str) or _RUN_ID.fullmatch(insight_run_id) is None:
        raise DataError("insight_evidence", None, "insight_run_id 格式錯誤")
    return Path(data_dir) / _INSIGHT_DIR / f"{insight_run_id}.json"


def _canonical_bytes(pack: InsightPack) -> bytes:
    """Serialize a valid pack deterministically for idempotency comparison."""
    payload = pack.model_dump(mode="json")
    return (json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def read_pack(path: str | Path) -> InsightPack:
    """Read and validate one JSON pack; nothing inside it is fetched or executed."""
    source = Path(path)
    try:
        payload = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise DataError("insight_evidence", None, "洞悉證據包無法讀取") from exc
    try:
        return InsightPack.model_validate_json(payload)
    except (ValidationError, ValueError) as exc:
        raise DataError("insight_evidence", None, "洞悉證據包合約無效") from exc


def save_pack(data_dir: str | Path, pack: InsightPack) -> InsightWriteResult:
    """Atomically create one immutable pack; equal repeats succeed and conflicts raise."""
    target = insight_path(data_dir, pack.insight_run_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    contents = _canonical_bytes(pack)
    temporary = target.with_name(f"{target.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        with temporary.open("xb") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            existing = read_pack(target)
            if _canonical_bytes(existing) != contents:
                raise DataError(
                    "insight_evidence",
                    None,
                    f"insight_run_id={pack.insight_run_id} 已存在不同內容，拒絕覆寫",
                ) from None
            return InsightWriteResult(path=target, created=False)
    except OSError as exc:
        raise DataError("insight_evidence", None, "洞悉證據包無法原子保存") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return InsightWriteResult(path=target, created=True)


def import_pack(source: str | Path, data_dir: str | Path) -> InsightWriteResult:
    """Validate an operator-supplied pack then save it under the fixed insight root."""
    return save_pack(data_dir, read_pack(source))


def index_packs(data_dir: str | Path) -> InsightCatalog:
    """Read every saved pack without hiding invalid ones and without calling any model."""
    root = Path(data_dir) / _INSIGHT_DIR
    if not root.exists():
        return InsightCatalog(packs=[], issues=[])
    packs: list[InsightPack] = []
    issues: list[dict[str, str]] = []
    for path in sorted(root.glob("*.json"), key=lambda item: item.name):
        try:
            packs.append(read_pack(path))
        except DataError:
            run_id = path.stem if _RUN_ID.fullmatch(path.stem) else "unreadable_pack"
            issues.append({"insight_run_id": run_id, "detail": "insight evidence pack is invalid"})
    return InsightCatalog(packs=packs, issues=issues)


def select_packs(
    data_dir: str | Path,
    *,
    stock_id: str | None = None,
    insight_run_id: str | None = None,
) -> InsightCatalog:
    """Apply stock and run filters; an unmatched filter yields an empty, honest result."""
    catalog = index_packs(data_dir)
    wanted = normalize_stock_id(stock_id) if stock_id else None
    packs = [
        pack
        for pack in catalog.packs
        if (wanted is None or pack.stock_id == wanted)
        and (insight_run_id is None or pack.insight_run_id == insight_run_id)
    ]
    return InsightCatalog(packs=packs, issues=catalog.issues)
