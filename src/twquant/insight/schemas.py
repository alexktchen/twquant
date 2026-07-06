"""洞悉層結構化輸出模型（SPEC-008 §3）。

這些是 LLM 的輸出結構（messages.parse 的 output_format），故為可變 pydantic BaseModel
（非凍結配置）。驗證失敗由 SDK 層 retry。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


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
