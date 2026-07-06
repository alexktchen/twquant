"""洞悉層設定（SPEC-008 §3，configs/llm.yaml）。模型字串只在此定義。"""

from __future__ import annotations

from pathlib import Path

from twquant.config import BaseConfig, load_config


class ModelSpec(BaseConfig):
    id: str
    input_usd_per_1m: float
    output_usd_per_1m: float


class LlmConfig(BaseConfig):
    usd_twd: float = 32.0
    daily_budget_twd: float = 300.0
    models: dict[str, ModelSpec]
    tasks: dict[str, str]  # task -> model 名（路由）

    def model_for(self, task: str) -> ModelSpec:
        """任務路由到模型規格；未知任務 → KeyError（呼叫端須用合法任務名）。"""
        return self.models[self.tasks[task]]

    def cost_twd(self, task: str, input_tokens: int, output_tokens: int) -> float:
        """依 token 數與該任務模型定價估成本（TWD）。"""
        m = self.model_for(task)
        usd = input_tokens / 1e6 * m.input_usd_per_1m + output_tokens / 1e6 * m.output_usd_per_1m
        return usd * self.usd_twd


def load_llm_config(path: str | Path) -> LlmConfig:
    return load_config(path, LlmConfig)
