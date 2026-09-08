"""洞悉層結構化輸出模型（SPEC-008 §3）。

這些是 LLM 的輸出結構（messages.parse 的 output_format），故為可變 pydantic BaseModel
（非凍結配置）。驗證失敗由 SDK 層 retry。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class ProposedChange(BaseModel):
    yaml_path: str  # 具體到 YAML 欄位路徑，如 "signals.0.weight"
    old_value: str = ""
    new_value: str


class Proposal(BaseModel):
    """策略調整提案（advisor 輸出，SPEC-008 §2.2）。"""

    action: Literal["no_action", "param_change", "factor_reweight", "factor_swap", "risk_adjust"]
    target_strategy: str
    changes: list[ProposedChange] = []
    rationale: str  # 繁中，引用輸入數據的具體數字
    expected_impact: str
    confidence: Literal["low", "medium", "high"]


class DailyBrief(BaseModel):
    """每日洞悉摘要（daily_brief 輸出，SPEC-008 §2.1）。"""

    summary: str  # 3-6 句繁中，≤ 500 字
    highlights: list[str] = []


class Explanation(BaseModel):
    """異常事件解釋（explainer 輸出，SPEC-008 §2.3）。"""

    likely_causes: list[str]  # 依可能性排序
    suggested_checks: list[str]


class InsightRisk(BaseModel):
    """One named risk the analysis found, tied to the evidence that raised it."""

    kind: str
    evidence_ids: list[str] = []
    note: str = ""


class CandidateAnalysis(BaseModel):
    """一檔候選的結構化洞悉輸出（PRD-002 §5、PLAN §5）。

    這是 LLM 的輸出結構，不是最終分類：`recommended_bucket` 只是**提案欄位**，
    最終 bucket 由版本化規則計算（D-081c），兩者分開保存。
    """

    model_config = ConfigDict(extra="forbid")

    analysis_schema_version: Literal[2] = 2
    stance: Literal["support", "neutral", "conflict", "insufficient_data"]
    supporting_evidence_ids: list[str] = []
    contradicting_evidence_ids: list[str] = []
    event_horizon: Literal["short", "medium", "long", "unknown"] = "unknown"
    risks: list[InsightRisk] = []
    missing_data: list[str] = []
    recommended_bucket: Literal["select", "watch", "exclude"]
    rationale: str

    @model_validator(mode="after")
    def _stance_matches_its_evidence(self) -> CandidateAnalysis:
        """一個立場必須被它自己列出的證據撐住，否則就是沒有依據的結論。"""
        overlap = set(self.supporting_evidence_ids) & set(self.contradicting_evidence_ids)
        if overlap:
            raise ValueError(f"evidence 不得同時列為支持與反對：{sorted(overlap)}")
        if self.stance == "support" and not self.supporting_evidence_ids:
            raise ValueError("stance=support 必須提供 supporting_evidence_ids")
        if self.stance == "insufficient_data" and not self.missing_data:
            raise ValueError("stance=insufficient_data 必須在 missing_data 說明缺什麼")
        return self
