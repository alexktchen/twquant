"""Claude API 封裝：模型路由、用量記錄、成本上限、離線降級（SPEC-008 §3）。

離線（無 ANTHROPIC_API_KEY 且無注入 backend）→ complete 回 None，呼叫端照常降級（AC-6）。
日成本超 daily_budget_twd → 回 None + WARN（AC-4）。schema 連 2 次失敗 → 回 None（AC-1）。
每次成功呼叫寫 llm_log（AC-5）。backend 可注入（測試不打網路）。
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from twquant.reporting.llm_log import record_usage, today_cost_twd

if TYPE_CHECKING:
    from twquant.insight.config import LlmConfig

_log = logging.getLogger("twquant.insight")

# backend(model_id, system, user, schema, max_tokens) -> (parsed_obj, input_tokens, output_tokens)
Backend = Callable[[str, str, str, type, int], tuple[Any, int, int]]


@dataclass(frozen=True)
class LlmResult:
    parsed: Any
    input_tokens: int
    output_tokens: int
    cost_twd: float
    model_id: str


class InsightClient:
    def __init__(
        self,
        cfg: LlmConfig,
        data_dir: str | Path,
        *,
        api_key: str | None = None,
        backend: Backend | None = None,
    ) -> None:
        self._cfg = cfg
        self._dir = data_dir
        self._key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY")
        self._backend = backend

    @property
    def available(self) -> bool:
        """有注入 backend 或有 API key 才可用（否則離線降級）。"""
        return self._backend is not None or bool(self._key)

    def complete(
        self,
        *,
        task: str,
        system: str,
        user: str,
        schema: type,
        prompt_version: str,
        now: dt.datetime,
        max_tokens: int = 8000,
    ) -> LlmResult | None:
        if not self.available:
            _log.info("洞悉層離線（無 API key），略過 task=%s", task)
            return None
        if today_cost_twd(self._dir, now.date()) >= self._cfg.daily_budget_twd:
            _log.warning(
                "洞悉層日成本已達上限 %.0f TWD，停用（task=%s）", self._cfg.daily_budget_twd, task
            )
            return None

        model = self._cfg.model_for(task)
        backend = self._backend or _anthropic_backend(self._key)
        parsed: Any = None
        in_tok = out_tok = 0
        for attempt in range(2):  # schema/呼叫失敗重試 2 次（AC-1）
            try:
                parsed, in_tok, out_tok = backend(model.id, system, user, schema, max_tokens)
                break
            except Exception:  # noqa: BLE001  # 記錄後重試/放棄，不讓例外阻塞 pipeline
                _log.exception("洞悉層呼叫失敗（task=%s attempt=%d）", task, attempt + 1)
                parsed = None
        if parsed is None:
            return None

        cost = self._cfg.cost_twd(task, in_tok, out_tok)
        record_usage(
            self._dir,
            task=task,
            model_id=model.id,
            prompt_version=prompt_version,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_twd=cost,
            now=now,
        )
        return LlmResult(
            parsed=parsed,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_twd=cost,
            model_id=model.id,
        )


def _anthropic_backend(api_key: str | None) -> Backend:
    """真正的 Anthropic SDK backend（僅生產用，CI 無 key 不會走到）。

    依 claude-api skill：messages.parse + output_format + adaptive thinking + prompt cache。
    """

    def backend(
        model_id: str, system: str, user: str, schema: type, max_tokens: int
    ) -> tuple[Any, int, int]:
        import anthropic  # 延遲匯入：離線/測試不需要此套件

        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.parse(
            model=model_id,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_format=schema,
        )
        usage = resp.usage
        return resp.parsed_output, int(usage.input_tokens), int(usage.output_tokens)

    return backend
