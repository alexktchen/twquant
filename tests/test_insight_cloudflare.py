"""T-368／D-185：洞悉層的 Cloudflare Workers AI backend。

全部注入假 HTTP 傳輸，不連網（AC-5）。測試名稱含 AC 編號。
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from twquant.conventions import TZ
from twquant.insight.client import (
    InsightClient,
    cloudflare_request_body,
    cloudflare_run_url,
    parse_cloudflare_reply,
)
from twquant.insight.config import LlmConfig, load_llm_config
from twquant.insight.schemas import DailyBrief
from twquant.reporting.llm_log import read_usage, record_usage, today_neurons

_NOW = dt.datetime(2026, 10, 17, 17, 40, tzinfo=TZ)
_ACCOUNT = "acct-0123"
_TOKEN = "tok-not-a-real-secret"
_CF_MODEL = "@cf/qwen/qwen3-30b-a3b-fp8"


def _cfg(**patch: Any) -> LlmConfig:
    row: dict[str, Any] = {
        "usd_twd": 32.0,
        "daily_budget_twd": 300.0,
        "daily_neuron_budget": 9000.0,
        "models": {
            "opus": {"id": "claude-opus-5", "input_usd_per_1m": 5.0, "output_usd_per_1m": 25.0},
            "cf": {
                "id": _CF_MODEL,
                "provider": "cloudflare",
                "input_usd_per_1m": 0.051,
                "output_usd_per_1m": 0.335,
                "input_neurons_per_1m": 4625.0,
                "output_neurons_per_1m": 30475.0,
            },
        },
        "tasks": {"daily_brief": "cf", "advisor": "opus"},
    }
    row.update(patch)
    return LlmConfig.model_validate(row)


def _payload(
    response: Any, *, usage: dict[str, int] | None = None, success: bool = True
) -> dict[str, Any]:
    body: dict[str, Any] = {"result": {"response": response}, "success": success, "errors": []}
    if usage is not None:
        body["result"]["usage"] = usage
    return body


class _FakePost:
    """記下每次請求、回固定回應的假傳輸。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, str], dict[str, Any], float]] = []

    def __call__(self, url: str, headers: dict[str, str], body: bytes, timeout_s: float) -> bytes:
        self.calls.append((url, headers, json.loads(body), timeout_s))
        return json.dumps(self.payload).encode("utf-8")


# ── AC-1：provider 預設 anthropic；cloudflare 缺 Neurons 費率即拒絕 ───────────
def test_ac1_provider_defaults_to_anthropic_and_cloudflare_requires_neuron_rates() -> None:
    cfg = _cfg()
    assert cfg.model_for("advisor").provider == "anthropic"
    assert cfg.model_for("daily_brief").provider == "cloudflare"
    # 既有寫法（沒有 provider、沒有 Neurons）照常載入，成本公式不變
    assert abs(cfg.cost_twd("advisor", 1000, 500) - (0.005 + 0.0125) * 32) < 1e-9
    assert cfg.neurons("advisor", 1000, 500) is None
    # cloudflare：Neurons 依公布費率線性計
    cf_neurons = cfg.neurons("daily_brief", 1_000_000, 1_000_000)
    assert cf_neurons is not None and abs(cf_neurons - (4625.0 + 30475.0)) < 1e-9
    with pytest.raises(ValidationError, match="neurons"):
        _cfg(
            models={
                "cf": {
                    "id": _CF_MODEL,
                    "provider": "cloudflare",
                    "input_usd_per_1m": 0.051,
                    "output_usd_per_1m": 0.335,
                }
            },
            tasks={"daily_brief": "cf"},
        )


# ── AC-2：請求形狀與回應解析 ───────────────────────────────────────────
def test_ac2_request_body_carries_schema_and_messages() -> None:
    url = cloudflare_run_url(_ACCOUNT, _CF_MODEL)
    assert url == f"https://api.cloudflare.com/client/v4/accounts/{_ACCOUNT}/ai/run/{_CF_MODEL}"
    body = cloudflare_request_body("SYS", "USER", DailyBrief, 777)
    assert body["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USER"},
    ]
    assert body["max_tokens"] == 777
    expected = DailyBrief.model_json_schema()
    expected["required"] = list(expected["properties"])  # 送出時所有欄位必填（見下方 nested 測試）
    assert body["response_format"] == {"type": "json_schema", "json_schema": expected}


def test_ac2_reply_object_or_wrapped_string_parses_and_usage_is_required() -> None:
    usage = {"prompt_tokens": 120, "completion_tokens": 30}
    obj = {"summary": "今日平靜。", "highlights": ["無"]}
    parsed, i, o = parse_cloudflare_reply(_payload(obj, usage=usage), DailyBrief)
    assert isinstance(parsed, DailyBrief) and parsed.summary == "今日平靜。" and (i, o) == (120, 30)

    wrapped = "<think>先想一下\n</think>\n```json\n" + json.dumps(obj, ensure_ascii=False) + "\n```"
    parsed2, _, _ = parse_cloudflare_reply(_payload(wrapped, usage=usage), DailyBrief)
    assert parsed2 == parsed

    # OpenAI 形狀（choices[0].message.content）也接受
    openai_like = {
        "result": {"choices": [{"message": {"content": json.dumps(obj)}}], "usage": usage},
        "success": True,
        "errors": [],
    }
    parsed3, _, _ = parse_cloudflare_reply(openai_like, DailyBrief)
    assert parsed3 == parsed

    with pytest.raises(RuntimeError, match="usage"):
        parse_cloudflare_reply(_payload(obj), DailyBrief)  # 沒有 usage → 不能記帳 → 失敗
    with pytest.raises(RuntimeError, match="Workers AI"):
        parse_cloudflare_reply(
            {"success": False, "errors": [{"code": 7, "message": "bad"}], "result": None},
            DailyBrief,
        )
    with pytest.raises(ValidationError):
        parse_cloudflare_reply(_payload({"nope": 1}, usage=usage), DailyBrief)  # 不符 schema 不補值


def _real_shape(response: Any, content: Any, completion_tokens: int) -> dict[str, Any]:
    """2026-10-17 實連 qwen3 的回應形狀：response 與 choices 兩個欄位同時存在。"""
    return {
        "result": {
            "response": response,
            "choices": [{"message": {"content": content, "reasoning": "好的，用户……"}}],
            "usage": {"prompt_tokens": 53, "completion_tokens": completion_tokens},
        },
        "success": True,
        "errors": [],
    }


def test_ac2_empty_reply_after_reasoning_names_max_tokens() -> None:
    # 實連：max_tokens=200 全被推理用掉，response 與 content 都是 null
    with pytest.raises(RuntimeError, match="max_tokens"):
        parse_cloudflare_reply(_real_shape(None, None, 200), DailyBrief)


def test_ac2_null_response_falls_back_to_choices_content() -> None:
    obj = {"summary": "今日平靜。", "highlights": ["無"]}
    parsed, _, o = parse_cloudflare_reply(
        _real_shape(None, json.dumps(obj, ensure_ascii=False), 300), DailyBrief
    )
    assert parsed.summary == "今日平靜。" and o == 300


# ── AC-3：依供應商選 backend；只有 ANTHROPIC key 不算可用 ──────────────────
def test_ac3_cloudflare_task_uses_cloudflare_backend_and_logs(tmp_path: Path) -> None:
    post = _FakePost(
        _payload(
            {"summary": "法人買超集中。", "highlights": []},
            usage={"prompt_tokens": 8000, "completion_tokens": 1000},
        )
    )
    client = InsightClient(
        _cfg(), tmp_path, api_key=None, cf_account_id=_ACCOUNT, cf_token=_TOKEN, http_post=post
    )
    assert client.unavailable_reason(_NOW, "daily_brief") is None
    res = client.complete(
        task="daily_brief", system="S", user="U", schema=DailyBrief, prompt_version="v1", now=_NOW
    )
    assert res is not None and res.model_id == _CF_MODEL
    assert res.input_tokens == 8000 and res.output_tokens == 1000
    url, headers, body, timeout_s = post.calls[0]
    assert url == cloudflare_run_url(_ACCOUNT, _CF_MODEL)
    assert headers["Authorization"] == f"Bearer {_TOKEN}"
    assert headers["Content-Type"] == "application/json"
    assert body["messages"][1]["content"] == "U"
    assert timeout_s == _cfg().request_timeout_s
    log = read_usage(tmp_path)
    assert log.height == 1 and log["model_id"][0] == _CF_MODEL
    # 超額等值成本：(8000×0.051 + 1000×0.335)/1e6 × 32
    assert abs(log["cost_twd"][0] - (8000 * 0.051 + 1000 * 0.335) / 1e6 * 32) < 1e-9


def test_ac3_anthropic_key_alone_does_not_make_cloudflare_task_available(tmp_path: Path) -> None:
    client = InsightClient(_cfg(), tmp_path, api_key="sk-x", cf_account_id=None, cf_token=None)
    reason = client.unavailable_reason(_NOW, "daily_brief")
    assert (
        reason is not None and "CLOUDFLARE_ACCOUNT_ID" in reason and "CLOUDFLARE_AI_TOKEN" in reason
    )
    # 只缺 token 時只點名 token
    half = InsightClient(_cfg(), tmp_path, cf_account_id=_ACCOUNT, cf_token=None)
    reason2 = half.unavailable_reason(_NOW, "daily_brief")
    assert reason2 is not None and "CLOUDFLARE_AI_TOKEN" in reason2
    assert "CLOUDFLARE_ACCOUNT_ID" not in reason2
    # anthropic 任務不受 Cloudflare 憑證影響
    assert client.unavailable_reason(_NOW, "advisor") is None
    out = client.complete(
        task="daily_brief", system="S", user="U", schema=DailyBrief, prompt_version="v1", now=_NOW
    )
    assert out is None


# ── AC-4：Neurons 日預算閘，依 UTC 日 ────────────────────────────────────
def test_ac4_neuron_budget_counts_by_utc_day_and_blocks(tmp_path: Path) -> None:
    cfg = _cfg(daily_neuron_budget=900.0)  # 兩筆共 985.95 Neurons，超過
    # 台北 10-18 02:00 ＝ UTC 10-17 18:00，與 10-17 17:40（UTC 09:40）同一個 UTC 日
    late = dt.datetime(2026, 10, 18, 2, 0, tzinfo=TZ)
    for when in (_NOW, late):
        record_usage(
            tmp_path,
            task="daily_brief",
            model_id=_CF_MODEL,
            prompt_version="v1",
            input_tokens=100_000,  # 462.5 Neurons
            output_tokens=1_000,  # 30.5 Neurons
            cost_twd=0.0,
            now=when,
        )
    utc_day = dt.date(2026, 10, 17)
    used = today_neurons(tmp_path, utc_day, cfg.neurons_for_model)
    assert abs(used - 2 * (462.5 + 30.475)) < 1e-6
    assert today_neurons(tmp_path, dt.date(2026, 10, 18), cfg.neurons_for_model) == 0.0
    # anthropic 模型的用量不算 Neurons
    record_usage(
        tmp_path,
        task="advisor",
        model_id="claude-opus-5",
        prompt_version="v1",
        input_tokens=10_000_000,
        output_tokens=0,
        cost_twd=1.0,
        now=_NOW,
    )
    assert abs(today_neurons(tmp_path, utc_day, cfg.neurons_for_model) - used) < 1e-6

    post = _FakePost(_payload({"summary": "x"}, usage={"prompt_tokens": 1, "completion_tokens": 1}))
    client = InsightClient(cfg, tmp_path, cf_account_id=_ACCOUNT, cf_token=_TOKEN, http_post=post)
    reason = client.unavailable_reason(late, "daily_brief")
    assert reason is not None and "Neurons" in reason
    assert (
        client.complete(
            task="daily_brief",
            system="S",
            user="U",
            schema=DailyBrief,
            prompt_version="v1",
            now=late,
        )
        is None
    )
    assert post.calls == []  # 被閘住就不該打出去


def test_ac4_naive_timestamps_in_log_are_read_as_taipei(tmp_path: Path) -> None:
    cfg = _cfg()
    naive = dt.datetime(2026, 10, 18, 2, 0)  # 既有測試／舊紀錄可能是 naive；視為台北時間
    record_usage(
        tmp_path,
        task="daily_brief",
        model_id=_CF_MODEL,
        prompt_version="v1",
        input_tokens=1_000_000,
        output_tokens=0,
        cost_twd=0.0,
        now=naive,
    )
    assert today_neurons(tmp_path, dt.date(2026, 10, 17), cfg.neurons_for_model) == 4625.0


# ── AC-5：失敗重試後放棄、不記帳；傳輸例外不外洩到呼叫端 ───────────────────
def test_ac5_transport_failure_degrades_to_named_absence(tmp_path: Path) -> None:
    def boom(url: str, headers: dict[str, str], body: bytes, timeout_s: float) -> bytes:
        raise OSError("connection refused")

    client = InsightClient(
        _cfg(), tmp_path, cf_account_id=_ACCOUNT, cf_token=_TOKEN, http_post=boom
    )
    res, absent = client.complete_detailed(
        task="daily_brief", system="S", user="U", schema=DailyBrief, prompt_version="v1", now=_NOW
    )
    assert res is None and absent is not None and "連續失敗" in absent
    assert read_usage(tmp_path).is_empty()


# ── AC-6：正式 llm.yaml 的路由 ─────────────────────────────────────────
def test_ac6_llm_yaml_routes_pipeline_tasks_to_cloudflare() -> None:
    cfg = load_llm_config(Path("configs/llm.yaml"))
    for task in ("candidate_analysis", "daily_brief"):
        spec = cfg.model_for(task)
        assert spec.provider == "cloudflare" and spec.id.startswith("@cf/")
        assert spec.input_neurons_per_1m and spec.output_neurons_per_1m
    for task in ("advisor", "explainer"):
        assert cfg.model_for(task).provider == "anthropic"
    assert 0 < cfg.daily_neuron_budget <= 10_000  # 免費額度 10,000／日
    # 每晚 30 檔 × (8k 進、1k 出) 要落在免費額度內（D-185 §2 的估算）
    nightly = 30 * (cfg.neurons("candidate_analysis", 8000, 1000) or 0.0)
    assert nightly < cfg.daily_neuron_budget


class _Tiny(BaseModel):
    ok: bool


def test_ac2_schema_with_nested_defs_marks_every_field_required() -> None:
    # 實連 qwen3（2026-10-17）：有預設值的欄位不在 required 裡，受限解碼就會省略
    # supporting_evidence_ids，stance=support 因此 6 次全數驗證失敗；全部標必填後 8/8 通過。
    from twquant.insight.schemas import CandidateAnalysis

    pristine = CandidateAnalysis.model_json_schema()
    body = cloudflare_request_body("S", "U", CandidateAnalysis, 10)
    schema = body["response_format"]["json_schema"]
    assert "$defs" in schema and "InsightRisk" in schema["$defs"]
    assert set(schema["required"]) == set(schema["properties"])
    assert "supporting_evidence_ids" in schema["required"]
    risk = schema["$defs"]["InsightRisk"]
    assert set(risk["required"]) == set(risk["properties"])
    assert CandidateAnalysis.model_json_schema() == pristine  # 不改到 pydantic 的快取
    assert cloudflare_request_body("S", "U", _Tiny, 1)["response_format"]["json_schema"][
        "properties"
    ] == {"ok": {"title": "Ok", "type": "boolean"}}


def test_ac4_same_timestamp_records_do_not_overwrite_each_other(tmp_path: Path) -> None:
    # run_insights 對整批候選傳同一個 now；每筆用量都要留下來，預算閘才算得對
    for _ in range(3):
        record_usage(
            tmp_path,
            task="daily_brief",
            model_id=_CF_MODEL,
            prompt_version="v1",
            input_tokens=1000,
            output_tokens=10,
            cost_twd=0.0,
            now=_NOW,
        )
    assert read_usage(tmp_path).height == 3
