"""異常事件解釋（SPEC-008 §2.3）。漂移超標/閘門失敗/kill-switch 變更時觸發。"""

from __future__ import annotations

import datetime as dt
import json
from typing import TYPE_CHECKING

from twquant.insight.prompts import EXPLAINER_SYSTEM, PROMPT_VERSION
from twquant.insight.schemas import Explanation

if TYPE_CHECKING:
    from twquant.insight.client import InsightClient


def explain_event(
    client: InsightClient, event: dict[str, object], now: dt.datetime
) -> Explanation | None:
    """由事件上下文產生原因排序 + 建議檢查步驟。離線→None。"""
    user = "異常事件上下文（JSON）：\n" + json.dumps(event, ensure_ascii=False, indent=2)
    res = client.complete(
        task="explainer",
        system=EXPLAINER_SYSTEM,
        user=user,
        schema=Explanation,
        prompt_version=PROMPT_VERSION,
        now=now,
        max_tokens=3000,
    )
    if res is None:
        return None
    exp: Explanation = res.parsed
    return exp
