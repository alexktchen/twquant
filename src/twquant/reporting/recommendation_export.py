"""Export an existing daily ML inference run as a recommendation snapshot (SPEC-014 §7).

This module never runs inference, never trains and never promotes a model. It reads scores that
a prior `ml score` run already produced, together with that model's saved metadata, and writes
one immutable research snapshot. A model that trains successfully gains no publication right
here: every exported run is `mode="research"` with `published_at=None`.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

from twquant.conventions import as_of_cutoff
from twquant.errors import DataError
from twquant.reporting.recommendation_snapshot import (
    RecommendationSnapshot,
    SnapshotWriteResult,
    save_snapshot,
)

if TYPE_CHECKING:  # polars is only needed for the concrete export entry point.
    import polars as pl

# 這一層不做任何晉級判斷，所有候選一律標為 ML 研究候選；分組門檻屬研究決策，
# 不在匯出層發明（發明門檻等於在畫面上偷偷做晉級）。
_RESEARCH_BUCKET = "ml_candidate"
_UNANALYSED = "pending"
_REQUIRED_COLUMNS = ("date", "stock_id", "score", "available_at")


def run_id_for(model_id: str, day: dt.date) -> str:
    """Return the deterministic run identity, so one inference day yields exactly one snapshot."""
    digest = hashlib.sha256(f"mlscore:{model_id}:{day.isoformat()}".encode()).hexdigest()[:16]
    return f"mlscore_{day:%Y%m%d}_{digest}"


def read_model_identity(model_dir: str | Path) -> tuple[str, str]:
    """Read model_id and fingerprint from a real model artifact; refuse to invent either."""
    meta_path = Path(model_dir) / "meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DataError("recommendation_export", None, f"讀不到模型 metadata：{meta_path}") from exc
    except ValueError as exc:
        raise DataError(
            "recommendation_export", None, f"模型 metadata 不是有效 JSON：{meta_path}"
        ) from exc
    if not isinstance(meta, dict):
        raise DataError("recommendation_export", None, "模型 metadata 必須是物件")
    model_id = meta.get("model_id")
    if not isinstance(model_id, str) or not model_id:
        raise DataError("recommendation_export", None, "模型 metadata 缺 model_id，拒絕匯出")
    fingerprint = meta.get("artifact_fingerprint") or meta.get("config_hash")
    if not isinstance(fingerprint, str) or not fingerprint:
        raise DataError("recommendation_export", None, "模型 metadata 缺 artifact 指紋，拒絕匯出")
    return model_id, fingerprint


def snapshot_from_scores(
    rows: list[dict[str, object]],
    *,
    model_id: str,
    day: dt.date,
    artifact_fingerprint: str,
) -> RecommendationSnapshot:
    """Build one research snapshot from an inference run's own rows, keeping every candidate.

    Ranks come from the inference scores alone (highest score first, stock_id breaking ties), so
    the snapshot's ordering is reproducible from the source rows and nothing is filtered out.
    """
    if not rows:
        raise DataError("recommendation_export", day, "該日推論結果為空，拒絕發布半成品快照")
    for row in rows:
        missing = [column for column in _REQUIRED_COLUMNS if column not in row]
        if missing:
            raise DataError("recommendation_export", day, f"推論結果缺欄位：{missing}")
        if row["date"] != day:
            raise DataError("recommendation_export", day, f"推論結果含其他日期的列：{row['date']}")
    available = [row["available_at"] for row in rows]
    if any(not isinstance(value, dt.datetime) for value in available):
        raise DataError("recommendation_export", day, "available_at 必須是 datetime")
    stamps = sorted(value for value in available if isinstance(value, dt.datetime))
    # as_of 是「這一個交易日的訊號截點」，用專案既有的 D-008 定義（當日 20:00），
    # 不能用「跑匯出的當下」——否則 2026-09-04 的推論會被歸到執行匯出的那一天。
    cutoff = as_of_cutoff(day)
    if stamps[-1] < cutoff:
        raise DataError(
            "recommendation_export",
            day,
            f"推論產生時刻 {stamps[-1].isoformat()} 早於該日訊號截點 {cutoff.isoformat()}（D-008），"
            "不得以該日截點發布",
        )
    ordered = sorted(rows, key=lambda row: (-float(str(row["score"])), str(row["stock_id"])))
    candidates = [
        {
            "stock_id": row["stock_id"],
            "rank": index,
            "score": float(str(row["score"])),
            "bucket": _RESEARCH_BUCKET,
            "reason_ref": None,
            "insight_run_id": None,
            "insight_available_at": None,
            "data_status": _UNANALYSED,
        }
        for index, row in enumerate(ordered, start=1)
    ]
    return RecommendationSnapshot.model_validate(
        {
            "schema_version": 1,
            "run_id": run_id_for(model_id, day),
            "model_id": model_id,
            # 截點＝該交易日的 D-008 訊號截點；產生時刻＝推論自己記錄的 available_at。
            "as_of": cutoff,
            "generated_at": stamps[-1],
            "published_at": None,
            "mode": "research",
            "artifact_fingerprint": artifact_fingerprint,
            "candidates": candidates,
        }
    )


def _rows_from_frame(frame: pl.DataFrame) -> list[dict[str, object]]:
    """Convert a scores frame to plain rows without reordering or dropping anything."""
    return [dict(row) for row in frame.iter_rows(named=True)]


def export_scores(
    scores_path: str | Path,
    model_dir: str | Path,
    data_dir: str | Path,
    day: dt.date,
) -> SnapshotWriteResult:
    """Export one saved inference parquet as an immutable research snapshot."""
    import polars as pl

    source = Path(scores_path)
    try:
        frame = pl.read_parquet(source)
    except (OSError, pl.exceptions.PolarsError) as exc:
        raise DataError("recommendation_export", day, f"讀不到推論結果：{source}") from exc
    model_id, fingerprint = read_model_identity(model_dir)
    snapshot = snapshot_from_scores(
        _rows_from_frame(frame),
        model_id=model_id,
        day=day,
        artifact_fingerprint=fingerprint,
    )
    return save_snapshot(data_dir, snapshot)
