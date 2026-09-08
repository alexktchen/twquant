"""Retraining export contract for insight features and matured outcomes (SPEC-014 §7).

This module starts no training. It describes and assembles one export row per candidate so a
later, separately authorised research ticket can consume it:

* point-in-time identity (run, model, cutoff, fingerprint) travels with every row;
* every candidate is kept, including ones an analysis excluded, so survivorship cannot creep in;
* an outcome that has not matured yet is ``None`` — never zero, never dropped, never imputed;
* insight output is recorded as features only. A model-generated score is not a realised outcome,
  so declaring one as the label is refused outright.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from twquant.errors import DataError
from twquant.reporting.insight_evidence import InsightPack
from twquant.reporting.recommendation_snapshot import RecommendationSnapshot

#: Column name → what it means. The export ships its own dictionary so a consumer never has to
#: guess whether a null means "zero" or "not known yet".
RETRAIN_COLUMNS: dict[str, str] = {
    "run_id": "推薦快照的 run 身分，對應一次日頻推論",
    "model_id": "產生分數的模型 artifact 身分",
    "artifact_fingerprint": "模型 artifact 指紋，供重現該次推論",
    "as_of": "推論截點（tz-aware Asia/Taipei）；特徵只能用此時點前可得的資料",
    "stock_id": "股票代號，字串，保留前導零",
    "rank": "該次推論的原始名次，未經任何過濾",
    "ml_score": "模型原始分數（特徵，不是標籤）",
    "bucket": "該次推論的分組標記",
    "data_status": "候選的洞悉分析狀態：ready／pending／stale／unavailable",
    "insight_run_id": "對應的洞悉 run；未分析為 null",
    "insight_model_version": "產生洞悉分析的模型版本；無分析為 null",
    "insight_support_n": "支持證據筆數（特徵）",
    "insight_contradict_n": "反對證據筆數（特徵）",
    "insight_gap_n": "已知缺口筆數（特徵）",
    "excluded": "該候選是否被洞悉排除；排除股仍保留列以免造成存活偏誤",
    "horizon_days": "標籤視窗（交易日）",
    "target": "已實現的前瞻報酬；**尚未成熟時為 null**，不得填 0",
    "target_status": "ready＝已成熟並有結果；immature＝視窗未走完，尚無結果",
}

#: A label may never be sourced from a model's own output; that would train on a prediction.
_FORBIDDEN_LABEL_SOURCES = frozenset({"insight", "llm", "analysis", "ml_score", "model"})
_REQUIRED_LABEL_KEYS = ("source", "execution", "excess", "transform", "cost_ref", "spec_ref")


@dataclass(frozen=True)
class RetrainExport:
    """Rows plus the dictionary and label definition needed to read them correctly."""

    columns: dict[str, str]
    label: dict[str, str]
    rows: list[dict[str, object]]

    @property
    def matured(self) -> int:
        """How many rows already carry a realised outcome."""
        return sum(1 for row in self.rows if row["target_status"] == "ready")


def _validate_label(label: Mapping[str, str]) -> dict[str, str]:
    """Require a complete label definition whose source is a realised outcome, not a prediction."""
    missing = [key for key in _REQUIRED_LABEL_KEYS if not label.get(key)]
    if missing:
        raise DataError("retrain_export", None, f"標籤定義缺欄位：{missing}")
    source = str(label["source"]).lower()
    if any(token in source for token in _FORBIDDEN_LABEL_SOURCES):
        raise DataError(
            "retrain_export",
            None,
            f"標籤來源 {label['source']} 是模型輸出而非已實現結果，拒絕當作真實標籤",
        )
    return dict(label)


def _insight_features(pack: InsightPack | None) -> dict[str, object]:
    """Summarise one insight pack as features; its analysis is never treated as an outcome."""
    if pack is None:
        return {
            "insight_model_version": None,
            "insight_support_n": 0,
            "insight_contradict_n": 0,
            "insight_gap_n": 0,
        }
    directions = [item.direction for item in pack.evidence]
    return {
        "insight_model_version": pack.analysis.model_version if pack.analysis else None,
        "insight_support_n": directions.count("support"),
        "insight_contradict_n": directions.count("contradict"),
        "insight_gap_n": directions.count("gap"),
    }


def build_export(
    snapshot: RecommendationSnapshot,
    *,
    horizon_days: int,
    label: Mapping[str, str],
    outcomes: Mapping[str, float] | None = None,
    insight_packs: Sequence[InsightPack] = (),
    excluded_buckets: frozenset[str] = frozenset(),
) -> RetrainExport:
    """Assemble the retraining export for one recommendation run.

    ``outcomes`` holds only the stocks whose forward window has already completed. Any candidate
    absent from it keeps ``target=None`` with ``target_status="immature"``; no value is imputed
    and no row is dropped, so an excluded or unresolved name still travels with the export.
    """
    if horizon_days < 1:
        raise DataError("retrain_export", None, "horizon_days 必須為正整數")
    definition = _validate_label(label)
    realised = dict(outcomes or {})
    unknown = sorted(set(realised) - {c.stock_id for c in snapshot.candidates})
    if unknown:
        raise DataError(
            "retrain_export",
            snapshot.as_of.date(),
            f"outcomes 含不在候選中的股票：{unknown}",
        )
    packs = {pack.insight_run_id: pack for pack in insight_packs}
    rows: list[dict[str, object]] = []
    for candidate in snapshot.candidates:
        pack = packs.get(candidate.insight_run_id) if candidate.insight_run_id else None
        matured = candidate.stock_id in realised
        rows.append(
            {
                "run_id": snapshot.run_id,
                "model_id": snapshot.model_id,
                "artifact_fingerprint": snapshot.artifact_fingerprint,
                "as_of": snapshot.as_of,
                "stock_id": candidate.stock_id,
                "rank": candidate.rank,
                "ml_score": candidate.score,
                "bucket": candidate.bucket,
                "data_status": candidate.data_status,
                "insight_run_id": candidate.insight_run_id,
                **_insight_features(pack),
                "excluded": candidate.bucket in excluded_buckets,
                "horizon_days": horizon_days,
                "target": realised[candidate.stock_id] if matured else None,
                "target_status": "ready" if matured else "immature",
            }
        )
    return RetrainExport(columns=dict(RETRAIN_COLUMNS), label=definition, rows=rows)


def export_as_of(snapshot: RecommendationSnapshot) -> dt.datetime:
    """The point-in-time cutoff every feature in the export must respect."""
    return snapshot.as_of
