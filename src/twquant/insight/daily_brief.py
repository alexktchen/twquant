"""每日洞悉摘要（SPEC-008 §2.1）。純運算層：組 payload → client → 結構化輸出。

離線或降級時回 None，呼叫端（日報）照常產出、僅少 LLM 摘要欄位（AC-6/AC-8）。
"""

from __future__ import annotations

import datetime as dt
import json
from typing import TYPE_CHECKING

from twquant.insight.prompts import DAILY_BRIEF_SYSTEM, PROMPT_VERSION
from twquant.insight.schemas import DailyBrief

if TYPE_CHECKING:
    from twquant.insight.client import InsightClient


def build_daily_brief(
    client: InsightClient, report: dict[str, object], now: dt.datetime
) -> DailyBrief | None:
    """由當日結構化報表產生摘要。資料進 user turn（SPEC §0.3）。"""
    user = "當日報表數據（JSON）：\n" + json.dumps(report, ensure_ascii=False, indent=2)
    res = client.complete(
        task="daily_brief",
        system=DAILY_BRIEF_SYSTEM,
        user=user,
        schema=DailyBrief,
        prompt_version=PROMPT_VERSION,
        now=now,
        max_tokens=2000,
    )
    if res is None:
        return None
    brief: DailyBrief = res.parsed
    return brief
