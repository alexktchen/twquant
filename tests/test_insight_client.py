"""T-026：洞悉層 LLM 基座（路由、離線降級、預算閘、用量記錄、schema 重試）。

不打真實 API：以注入 backend 測全流程。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from twquant.insight.client import Backend, InsightClient
from twquant.insight.config import LlmConfig, load_llm_config
from twquant.insight.schemas import DailyBrief
from twquant.reporting.llm_log import read_usage, record_usage, today_cost_twd

_NOW = dt.datetime(2026, 6, 30, 17, 0)


def _cfg() -> LlmConfig:
    return LlmConfig.model_validate(
        {
            "usd_twd": 32.0,
            "daily_budget_twd": 100.0,
            "models": {
                "opus": {
                    "id": "claude-opus-4-8",
                    "input_usd_per_1m": 5.0,
                    "output_usd_per_1m": 25.0,
                },
                "haiku": {
                    "id": "claude-haiku-4-5",
                    "input_usd_per_1m": 1.0,
                    "output_usd_per_1m": 5.0,
                },
            },
            "tasks": {"daily_brief": "haiku", "advisor": "opus"},
        }
    )


def _ok_backend(obj: object) -> Backend:
    def backend(
        model_id: str, system: str, user: str, schema: type, max_tokens: int
    ) -> tuple[object, int, int]:
        return obj, 1000, 500

    return backend


# ── 路由 + 成本 ─────────────────────────────────────────────────────
def test_routing_and_cost() -> None:
    cfg = _cfg()
    assert cfg.model_for("advisor").id == "claude-opus-4-8"
    assert cfg.model_for("daily_brief").id == "claude-haiku-4-5"
    # haiku 1000 in + 500 out：(1000/1e6×1 + 500/1e6×5)×32 = (0.001+0.0025)×32 = 0.112 TWD
    assert abs(cfg.cost_twd("daily_brief", 1000, 500) - 0.112) < 1e-9


# ── 離線降級（AC-6）────────────────────────────────────────────────
def test_offline_returns_none(tmp_path: Path) -> None:
    client = InsightClient(_cfg(), tmp_path, api_key=None, backend=None)
    assert client.available is False
    out = client.complete(
        task="daily_brief", system="S", user="U", schema=DailyBrief, prompt_version="v1", now=_NOW
    )
    assert out is None  # 離線 → None，呼叫端照常降級


# ── 成功呼叫 + 用量記錄（AC-5）─────────────────────────────────────
def test_complete_logs_usage(tmp_path: Path) -> None:
    brief = DailyBrief(summary="今日投信買超集中電子。", highlights=["台積電領漲"])
    client = InsightClient(_cfg(), tmp_path, backend=_ok_backend(brief))
    res = client.complete(
        task="daily_brief", system="S", user="U", schema=DailyBrief, prompt_version="v1", now=_NOW
    )
    assert res is not None
    assert res.parsed.summary.startswith("今日")
    assert res.input_tokens == 1000 and res.output_tokens == 500
    log = read_usage(tmp_path)
    assert log.height == 1
    assert log["model_id"][0] == "claude-haiku-4-5"
    assert abs(log["cost_twd"][0] - 0.112) < 1e-9


# ── 預算閘（AC-4）──────────────────────────────────────────────────
def test_budget_gate_blocks(tmp_path: Path) -> None:
    # 先塞爆當日成本
    record_usage(
        tmp_path,
        task="advisor",
        model_id="claude-opus-4-8",
        prompt_version="v1",
        input_tokens=0,
        output_tokens=0,
        cost_twd=150.0,  # > 100 上限
        now=_NOW,
    )
    assert today_cost_twd(tmp_path, _NOW.date()) == 150.0
    client = InsightClient(_cfg(), tmp_path, backend=_ok_backend(DailyBrief(summary="x")))
    out = client.complete(
        task="daily_brief", system="S", user="U", schema=DailyBrief, prompt_version="v1", now=_NOW
    )
    assert out is None  # 超預算 → 拒絕
    # 次日恢復（不同日期）
    tomorrow = _NOW + dt.timedelta(days=1)
    assert today_cost_twd(tmp_path, tomorrow.date()) == 0.0


# ── schema/呼叫連續失敗 → None（AC-1）─────────────────────────────
def test_retry_exhausted_returns_none(tmp_path: Path) -> None:
    def bad_backend(
        model_id: str, system: str, user: str, schema: type, max_tokens: int
    ) -> tuple[object, int, int]:
        raise ValueError("壞 JSON")

    client = InsightClient(_cfg(), tmp_path, backend=bad_backend)
    out = client.complete(
        task="advisor", system="S", user="U", schema=DailyBrief, prompt_version="v1", now=_NOW
    )
    assert out is None
    assert read_usage(tmp_path).is_empty()  # 失敗不記帳


def test_sample_llm_yaml_loads() -> None:
    cfg = load_llm_config(Path("configs/llm.yaml"))
    assert cfg.model_for("advisor").id == "claude-opus-4-8"
    assert cfg.daily_budget_twd > 0
