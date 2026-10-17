"""LLM 封裝：模型路由、用量記錄、成本上限、離線降級（SPEC-008 §3）。

供應商（D-185）：`anthropic`（Claude API，SDK）與 `cloudflare`（Workers AI REST，免費額度以 Neurons 計）。
離線（該任務的供應商沒有憑證且無注入 backend）→ complete 回 None，呼叫端照常降級（AC-6）。
日成本超 daily_budget_twd、或 Cloudflare 當日（UTC）Neurons 超 daily_neuron_budget → 回 None + WARN（AC-4）。
schema 連 2 次失敗 → 回 None（AC-1）。每次成功呼叫寫 llm_log（AC-5）。backend 與 HTTP 傳輸可注入（測試不打網路）。

`insight/` 是純運算層裡唯一允許呼叫 LLM 的例外（tests/test_io_boundary.py）；Cloudflare backend
用標準庫 urllib，不引入 requests／httpx。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from twquant.conventions import TZ
from twquant.reporting.llm_log import record_usage, today_cost_twd, today_neurons

if TYPE_CHECKING:
    from twquant.insight.config import LlmConfig, ModelSpec

_log = logging.getLogger("twquant.insight")

# backend(model_id, system, user, schema, max_tokens) -> (parsed_obj, input_tokens, output_tokens)
Backend = Callable[[str, str, str, type, int], tuple[Any, int, int]]
# http_post(url, headers, body, timeout_s) -> 回應 bytes；測試注入假傳輸
HttpPost = Callable[[str, dict[str, str], bytes, float], bytes]

_CF_API = "https://api.cloudflare.com/client/v4/accounts"
_ENV_ANTHROPIC = "ANTHROPIC_API_KEY"
_ENV_CF_ACCOUNT = "CLOUDFLARE_ACCOUNT_ID"
_ENV_CF_TOKEN = "CLOUDFLARE_AI_TOKEN"
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
_CODE_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


class LlmRefusal(Exception):
    """模型依安全政策拒答（stop_reason="refusal"）。重試不會改變結果，故不重試。"""

    def __init__(self, category: str) -> None:
        super().__init__(f"refusal:{category}")
        self.category = category


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
        cf_account_id: str | None = None,
        cf_token: str | None = None,
        http_post: HttpPost | None = None,
    ) -> None:
        self._cfg = cfg
        self._dir = data_dir
        self._key = api_key if api_key is not None else os.environ.get(_ENV_ANTHROPIC)
        self._cf_account = (
            cf_account_id if cf_account_id is not None else os.environ.get(_ENV_CF_ACCOUNT)
        )
        self._cf_token = cf_token if cf_token is not None else os.environ.get(_ENV_CF_TOKEN)
        self._backend = backend
        self._http_post = http_post

    @property
    def config(self) -> LlmConfig:
        """The routing/pricing config this client was built with (model ids live only there)."""
        return self._cfg

    @property
    def available(self) -> bool:
        """有注入 backend、或任一供應商的憑證齊全才可用（否則離線降級）。"""
        return self._backend is not None or bool(self._key) or self._cf_ready

    @property
    def _cf_ready(self) -> bool:
        return bool(self._cf_account) and bool(self._cf_token)

    def _missing_credentials(self, spec: ModelSpec) -> list[str]:
        """該模型的供應商缺哪些環境變數；齊全回空清單。"""
        if self._backend is not None:
            return []
        if spec.provider == "cloudflare":
            missing = []
            if not self._cf_account:
                missing.append(_ENV_CF_ACCOUNT)
            if not self._cf_token:
                missing.append(_ENV_CF_TOKEN)
            return missing
        return [] if self._key else [_ENV_ANTHROPIC]

    def unavailable_reason(self, now: dt.datetime, task: str | None = None) -> str | None:
        """為什麼這次不能呼叫模型；可以呼叫時回 None（供呼叫端記錄缺席原因）。

        不給 task 時只看「有沒有任何供應商可用」；給 task 時看該任務路由到的供應商。
        """
        if task is None:
            if not self.available:
                return f"LLM 離線（未設定 {_ENV_ANTHROPIC}），未產生分析"
            spec = None
        else:
            spec = self._cfg.model_for(task)
            missing = self._missing_credentials(spec)
            if missing:
                return f"LLM 離線（未設定 {'／'.join(missing)}），未產生分析"
        if today_cost_twd(self._dir, now.date()) >= self._cfg.daily_budget_twd:
            return f"已達日預算上限 {self._cfg.daily_budget_twd:.0f} TWD，未產生分析"
        if spec is not None and spec.provider == "cloudflare":
            used = today_neurons(self._dir, _utc_day(now), self._cfg.neurons_for_model)
            if used >= self._cfg.daily_neuron_budget:
                return (
                    f"已達 Workers AI 日 Neurons 上限 {self._cfg.daily_neuron_budget:.0f}"
                    f"（UTC 日累計 {used:.0f}），未產生分析"
                )
        return None

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
        """呼叫模型並回傳結果；離線、超預算、拒答或連兩次失敗一律回 None。"""
        return self.complete_detailed(
            task=task,
            system=system,
            user=user,
            schema=schema,
            prompt_version=prompt_version,
            now=now,
            max_tokens=max_tokens,
        )[0]

    def complete_detailed(
        self,
        *,
        task: str,
        system: str,
        user: str,
        schema: type,
        prompt_version: str,
        now: dt.datetime,
        max_tokens: int = 8000,
    ) -> tuple[LlmResult | None, str | None]:
        """同 complete，但一併回傳「為什麼沒有結果」，讓呼叫端能誠實記錄缺席原因。"""
        blocked = self.unavailable_reason(now, task)
        if blocked is not None:
            _log.info("洞悉層略過 task=%s：%s", task, blocked)
            return None, blocked

        model = self._cfg.model_for(task)
        backend = self._backend or self._backend_for(model)
        parsed: Any = None
        in_tok = out_tok = 0
        for attempt in range(2):  # schema/呼叫失敗重試 2 次（AC-1）
            try:
                parsed, in_tok, out_tok = backend(model.id, system, user, schema, max_tokens)
                break
            except LlmRefusal as refusal:
                # 政策拒答重試只是再花一次錢拿到同一個答案，直接記錄並放棄。
                _log.warning("洞悉層被拒答（task=%s category=%s）", task, refusal.category)
                return None, f"refusal:{refusal.category}"
            except Exception:  # noqa: BLE001  # 記錄後重試/放棄，不讓例外阻塞 pipeline
                _log.exception("洞悉層呼叫失敗（task=%s attempt=%d）", task, attempt + 1)
                parsed = None
        if parsed is None:
            return None, "LLM 呼叫或結構化輸出連續失敗，未產生分析"

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
        ), None

    def _backend_for(self, model: ModelSpec) -> Backend:
        if model.provider == "cloudflare":
            # unavailable_reason 已擋過缺憑證；這裡再斷言一次讓型別與事實一致
            assert self._cf_account and self._cf_token
            return _cloudflare_backend(
                self._cf_account,
                self._cf_token,
                self._http_post or _urllib_post,
                self._cfg.request_timeout_s,
            )
        return _anthropic_backend(self._key)


def _utc_day(now: dt.datetime) -> dt.date:
    when = now if now.tzinfo is not None else now.replace(tzinfo=TZ)
    return when.astimezone(dt.UTC).date()


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
        if getattr(resp, "stop_reason", None) == "refusal":
            details = getattr(resp, "stop_details", None)
            raise LlmRefusal(str(getattr(details, "category", None) or "unknown"))
        usage = resp.usage
        return resp.parsed_output, int(usage.input_tokens), int(usage.output_tokens)

    return backend


# ── Cloudflare Workers AI（D-185）─────────────────────────────────────────


def cloudflare_run_url(account_id: str, model_id: str) -> str:
    """Workers AI REST 端點：POST /accounts/{account}/ai/run/{model}。"""
    return f"{_CF_API}/{account_id}/ai/run/{model_id}"


def _as_model(schema: type) -> type[BaseModel]:
    if not issubclass(schema, BaseModel):
        raise TypeError(f"Cloudflare backend 的 schema 必須是 pydantic BaseModel：{schema!r}")
    return schema


def cloudflare_request_body(
    system: str, user: str, schema: type, max_tokens: int
) -> dict[str, Any]:
    """組請求：指令在 system、資料在 user（SPEC-008 §0.3），JSON mode 帶 pydantic 的 JSON Schema。"""
    json_schema = _as_model(schema).model_json_schema()
    return {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "response_format": {"type": "json_schema", "json_schema": json_schema},
    }


def _unwrap_text(text: str) -> str:
    """只做兩件事：去掉推理模型的 <think> 區塊與 code fence（D-185 §6）。其餘交給 pydantic。"""
    stripped = _THINK_BLOCK.sub("", text).strip()
    return _CODE_FENCE.sub("", stripped).strip()


def parse_cloudflare_reply(payload: dict[str, Any], schema: type) -> tuple[Any, int, int]:
    """解析 Workers AI 回應：回 (schema 實例, prompt_tokens, completion_tokens)。

    `success: false` → RuntimeError（帶 Cloudflare 的錯誤原文）；沒有 usage → RuntimeError
    （沒 token 數就不能記帳，不估）；內容不符 schema → pydantic ValidationError（不補值）。
    """
    if not payload.get("success"):
        raise RuntimeError(f"Workers AI 回報失敗：{payload.get('errors')!r}")
    result = payload.get("result") or {}
    content: Any
    if "response" in result:
        content = result["response"]
    else:
        choices = result.get("choices") or []
        if not choices:
            raise RuntimeError(f"Workers AI 回應沒有 response 也沒有 choices：{sorted(result)}")
        content = choices[0]["message"]["content"]
    obj = json.loads(_unwrap_text(content)) if isinstance(content, str) else content
    usage = result.get("usage") or {}
    in_tok, out_tok = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if in_tok is None or out_tok is None:
        raise RuntimeError("Workers AI 回應沒有 usage.prompt_tokens／completion_tokens，無法記帳")
    return _as_model(schema).model_validate(obj), int(in_tok), int(out_tok)


def _urllib_post(url: str, headers: dict[str, str], body: bytes, timeout_s: float) -> bytes:
    """真正的 HTTP 傳輸（標準庫）；HTTP 4xx/5xx 連同回應內文一起 raise，讓 llm-ping 看得到原因。"""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # 固定 https 端點
            return bytes(resp.read())
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Workers AI HTTP {err.code}：{detail[:500]}") from err


def _cloudflare_backend(
    account_id: str, token: str, http_post: HttpPost, timeout_s: float
) -> Backend:
    def backend(
        model_id: str, system: str, user: str, schema: type, max_tokens: int
    ) -> tuple[Any, int, int]:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        body = json.dumps(
            cloudflare_request_body(system, user, schema, max_tokens), ensure_ascii=False
        ).encode("utf-8")
        raw = http_post(cloudflare_run_url(account_id, model_id), headers, body, timeout_s)
        return parse_cloudflare_reply(json.loads(raw.decode("utf-8")), schema)

    return backend
