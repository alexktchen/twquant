"""洞悉層（SPEC-008）：用 Claude API 讓系統自己看報表、提策略調整、寫每日摘要。

安全邊界（SPEC-008 §0）：LLM 只能「提案」不能「執行」；洞悉層對 configs/strategies、
configs/risk.yaml、執行層**沒有寫入路徑**（AC-2）。無 ANTHROPIC_API_KEY 時整個量化
系統照常運作（AC-6）——洞悉層是增強、不是依賴。
"""

from twquant.insight.client import InsightClient, LlmResult
from twquant.insight.config import LlmConfig, load_llm_config

__all__ = ["InsightClient", "LlmConfig", "LlmResult", "load_llm_config"]
