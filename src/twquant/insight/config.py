"""洞悉層設定（SPEC-008 §3，configs/llm.yaml）。模型字串只在此定義。

供應商（D-185）：`anthropic`（預設，Claude API）與 `cloudflare`（Workers AI，免費額度以 Neurons 計）。
Cloudflare 模型必須給每百萬 token 的 Neurons 費率，否則日免費額度閘算不出來。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import model_validator

from twquant.config import BaseConfig, load_config

Provider = Literal["anthropic", "cloudflare"]


class ModelSpec(BaseConfig):
    id: str
    provider: Provider = "anthropic"
    input_usd_per_1m: float
    output_usd_per_1m: float
    # Cloudflare 公布的每百萬 token Neurons（免費額度的計量單位）；anthropic 模型不填
    input_neurons_per_1m: float | None = None
    output_neurons_per_1m: float | None = None

    @model_validator(mode="after")
    def _cloudflare_needs_neuron_rates(self) -> ModelSpec:
        if self.provider == "cloudflare" and (
            self.input_neurons_per_1m is None or self.output_neurons_per_1m is None
        ):
            raise ValueError(
                f"模型 {self.id}：provider=cloudflare 必須給 input_neurons_per_1m 與 "
                "output_neurons_per_1m（免費額度閘需要）"
            )
        return self


class LlmConfig(BaseConfig):
    usd_twd: float = 32.0
    daily_budget_twd: float = 300.0
    # Cloudflare Workers AI 免費額度每日 10,000 Neurons（UTC 00:00 重置）；預設留一成給其他用途
    daily_neuron_budget: float = 9000.0
    request_timeout_s: float = 120.0  # REST backend 的單次請求逾時
    models: dict[str, ModelSpec]
    tasks: dict[str, str]  # task -> model 名（路由）

    def model_for(self, task: str) -> ModelSpec:
        """任務路由到模型規格；未知任務 → KeyError（呼叫端須用合法任務名）。"""
        return self.models[self.tasks[task]]

    def cost_twd(self, task: str, input_tokens: int, output_tokens: int) -> float:
        """依 token 數與該任務模型定價估成本（TWD）；Cloudflare 記的是公布的超額單價。"""
        m = self.model_for(task)
        usd = input_tokens / 1e6 * m.input_usd_per_1m + output_tokens / 1e6 * m.output_usd_per_1m
        return usd * self.usd_twd

    def neurons(self, task: str, input_tokens: int, output_tokens: int) -> float | None:
        """該任務模型的 Neurons 用量；非 Cloudflare 模型回 None。"""
        return _neurons(self.model_for(task), input_tokens, output_tokens)

    def neurons_for_model(
        self, model_id: str, input_tokens: int, output_tokens: int
    ) -> float | None:
        """依模型 id 算 Neurons（供用量紀錄回算）；未登記或非 Cloudflare 模型回 None。"""
        for spec in self.models.values():
            if spec.id == model_id:
                return _neurons(spec, input_tokens, output_tokens)
        return None


def _neurons(spec: ModelSpec, input_tokens: int, output_tokens: int) -> float | None:
    if spec.input_neurons_per_1m is None or spec.output_neurons_per_1m is None:
        return None
    return (
        input_tokens / 1e6 * spec.input_neurons_per_1m
        + output_tokens / 1e6 * spec.output_neurons_per_1m
    )


def load_llm_config(path: str | Path) -> LlmConfig:
    return load_config(path, LlmConfig)
