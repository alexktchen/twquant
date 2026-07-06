"""策略調整提案（SPEC-008 §2.2）。LLM 是假說產生器，不是決策者。

輸出 Proposal（含 no_action 合法選項）。no_action 不落地提案檔（AC-7）——落地由
reporting.proposals（IO 層）負責，本模組純運算。
"""

from __future__ import annotations

import datetime as dt
import json
from typing import TYPE_CHECKING

from twquant.insight.prompts import ADVISOR_SYSTEM, PROMPT_VERSION
from twquant.insight.schemas import Proposal

if TYPE_CHECKING:
    from twquant.insight.client import InsightClient, LlmResult


def propose_adjustment(
    client: InsightClient, context: dict[str, object], now: dt.datetime
) -> LlmResult | None:
    """產生策略調整提案。回 LlmResult（含 parsed:Proposal 與 usage metadata）或 None。"""
    user = "決策上下文（JSON）：\n" + json.dumps(context, ensure_ascii=False, indent=2)
    return client.complete(
        task="advisor",
        system=ADVISOR_SYSTEM,
        user=user,
        schema=Proposal,
        prompt_version=PROMPT_VERSION,
        now=now,
        max_tokens=16000,
    )
