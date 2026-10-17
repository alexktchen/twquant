"""T-373／D-187：雲端 pipeline 開洞悉步驟——證據包進 PG、第二班不重呼叫模型、欄位漂移檢查。不連網、不連 DB。"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from twquant.config import ServingConfig, load_config
from twquant.conventions import TZ
from twquant.data.serving import schema as sch
from twquant.errors import DataError
from twquant.insight.client import InsightClient
from twquant.insight.config import LlmConfig
from twquant.insight.schemas import CandidateAnalysis
from twquant.reporting.insight_evidence import InsightPack, index_packs, save_pack
from twquant.reporting.insight_run import run_insights
from twquant.reporting.insight_store import packs_frame, restore_packs
from twquant.reporting.recommendation_snapshot import RecommendationSnapshot, save_snapshot

_ROOT = Path(__file__).resolve().parent.parent
AS_OF = dt.datetime(2026, 10, 16, 20, 0, tzinfo=TZ)
NOW = dt.datetime(2026, 10, 16, 21, 0, tzinfo=TZ)
RUN = "mlscore_20261016_abcdef0123456789"


def _snapshot(tmp_path: Path) -> None:
    save_snapshot(
        tmp_path,
        RecommendationSnapshot.model_validate(
            {
                "schema_version": 1,
                "run_id": RUN,
                "model_id": "lgbm_net_h20",
                "as_of": AS_OF,
                "generated_at": AS_OF,
                "published_at": None,
                "mode": "research",
                "artifact_fingerprint": "fp",
                "candidates": [
                    {
                        "stock_id": "2330",
                        "rank": 1,
                        "score": 0.9,
                        "bucket": "ml_candidate",
                        "reason_ref": None,
                        "insight_run_id": None,
                        "insight_available_at": None,
                        "data_status": "pending",
                    }
                ],
            }
        ),
    )


def _sections(_: str) -> dict[str, Any]:
    return {
        "monthly_revenue": [
            {
                "date": dt.date(2026, 10, 10),
                "year_month": "2026-09",
                "revenue": 100.0,
                "yoy": 0.2,
                "available_at": AS_OF - dt.timedelta(days=1),
                "available_at_estimated": False,
            }
        ],
        "institutional": [],
        "margin": [],
        "stock_valuation": None,
        "industry": None,
        "corporate_events": [],
        "disposition": [],
        "material_news": {"window_days": 30, "rows": []},
    }


def _client(tmp_path: Path, calls: list[str]) -> InsightClient:
    cfg = LlmConfig.model_validate(
        {
            "models": {"m": {"id": "claude-x", "input_usd_per_1m": 1.0, "output_usd_per_1m": 1.0}},
            "tasks": {"candidate_analysis": "m"},
        }
    )

    def backend(model_id: str, system: str, user: str, schema: type, max_tokens: int) -> Any:
        calls.append(model_id)
        reply = CandidateAnalysis.model_validate(
            {
                "stance": "support",
                "supporting_evidence_ids": ["fundamentals_revenue_yoy"],
                "recommended_bucket": "watch",
                "rationale": "月營收年增 20%。",
            }
        )
        return reply, 100, 50

    return InsightClient(cfg, tmp_path, api_key="k", backend=backend)


class _Cursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows
        self.seen: list[tuple[str, tuple[Any, ...]]] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.seen.append((sql, params))

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


class _Conn:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.cur = _Cursor(rows)

    def cursor(self) -> _Cursor:
        return self.cur


# ── AC-1：證據包 → PG 列；pack 欄是整包 JSON，欄位與 spec 同名同序 ───────────────
def test_ac1_packs_frame_matches_spec_and_carries_the_whole_pack(tmp_path: Path) -> None:
    _snapshot(tmp_path)
    calls: list[str] = []
    run_insights(
        tmp_path,
        recommendation_run_id=RUN,
        top_n=1,
        client=_client(tmp_path, calls),
        now=NOW,
        fetch_sections=_sections,
    )
    packs = index_packs(tmp_path).packs
    df = packs_frame(packs, NOW)
    assert tuple(df.columns) == sch.INSIGHT_PACKS_SPEC.column_names
    row = df.row(0, named=True)
    assert (row["stock_id"], row["recommendation_run_id"], row["date"]) == (
        "2330",
        RUN,
        AS_OF.date(),
    )
    assert (row["has_analysis"], row["stance"], row["recommended_bucket"]) == (
        True,
        "support",
        "watch",
    )
    assert InsightPack.model_validate(json.loads(row["pack"])) == packs[0]
    assert "insight_packs" in sch.ALL_TABLES and sch.INSIGHT_PACKS_SPEC.pk == ("insight_run_id",)


# ── AC-2：PG 寫回本機後，同一批證據不再呼叫語言模型 ───────────────────────────────
def test_ac2_restored_packs_stop_the_second_run_from_calling_the_model(tmp_path: Path) -> None:
    _snapshot(tmp_path)
    calls: list[str] = []
    first = run_insights(
        tmp_path,
        recommendation_run_id=RUN,
        top_n=1,
        client=_client(tmp_path, calls),
        now=NOW,
        fetch_sections=_sections,
    )
    pack = index_packs(tmp_path).packs[0]
    payload = pack.model_dump(mode="json")

    # 另一台機器（空的 data_dir）：PG 有、本機沒有 → 寫回 1 份，再跑不呼叫模型
    fresh = tmp_path / "runner2"
    fresh.mkdir()
    _snapshot(fresh)
    assert restore_packs(_Conn([(payload,)]), "serving", fresh, RUN) == 1
    second = run_insights(
        fresh,
        recommendation_run_id=RUN,
        top_n=1,
        client=_client(fresh, calls),
        now=NOW + dt.timedelta(hours=3),
        fetch_sections=_sections,
    )
    assert len(calls) == 1
    assert (first.saved, first.reused, second.saved, second.reused) == (1, 0, 0, 1)
    assert second.analysed == 1 and index_packs(fresh).packs == [pack]
    # 本機已有同 id 不同內容：保留本機，不覆寫、不 raise
    other = dict(payload, analysis_absent_reason="x", analysis=None)
    assert restore_packs(_Conn([(other,)]), "serving", fresh, RUN) == 0
    assert index_packs(fresh).packs == [pack]


# ── AC-3：PG 既有表欄位與程式不同 → 清楚說怎麼修，不在 COPY 半路噴錯 ───────────────
def test_ac3_column_drift_names_the_fix() -> None:
    spec = sch.MATERIAL_NEWS_SPEC
    good = _Conn([(c,) for c in spec.column_names])
    sch.assert_columns(good, spec, "serving")
    sch.assert_columns(_Conn([]), spec, "serving")  # 表不存在：ensure_tables 會建
    stale = _Conn([(c,) for c in ("date", "stock_id", "seq", "subject", "available_at")])
    with pytest.raises(DataError, match=r'DROP TABLE "serving"\."material_news"'):
        sch.assert_columns(stale, spec, "serving")


# ── AC-4：雲端 pipeline 跑洞悉步驟、帶 Cloudflare 憑證；PG 副本有滾動窗 ──────────────
def test_ac4_cloud_pipeline_runs_insights_with_credentials_and_prunes_the_copy() -> None:
    scfg = load_config(_ROOT / "configs" / "serving.yaml", ServingConfig)
    assert "洞悉分析" not in scfg.cloud.skip_pipeline_steps
    assert "重大訊息" not in scfg.cloud.skip_pipeline_steps
    assert scfg.cloud.windows["insight_packs"] >= 20
    wf = (_ROOT / ".github" / "workflows" / "daily-pipeline.yml").read_text(encoding="utf-8")
    assert "CLOUDFLARE_AI_TOKEN: ${{ secrets.CLOUDFLARE_AI_TOKEN }}" in wf
    assert "CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}" in wf
    source = (_ROOT / "src" / "twquant" / "cli.py").read_text(encoding="utf-8")
    step = source[source.index("def _insight_step") : source.index('PipelineStep("擷取"')]
    assert "restore_packs(" in step and "publish_packs(" in step
    fn = (_ROOT / "frontend" / "functions" / "api" / "platform" / "insights.ts").read_text(
        encoding="utf-8"
    )
    assert "serving.insight_packs" in fn and "packs:" in fn


def test_ac4_saved_pack_survives_a_json_roundtrip_the_way_pg_returns_it(tmp_path: Path) -> None:
    pack = InsightPack.model_validate(
        {
            "schema_version": 1,
            "insight_run_id": "ins_0123456789abcdef",
            "recommendation_run_id": RUN,
            "as_of": AS_OF,
            "stock_id": "2330",
            "evidence": [],
            "analysis": None,
            "analysis_absent_reason": "LLM 離線",
        }
    )
    save_pack(tmp_path, pack)
    frame = packs_frame([pack], NOW)
    assert frame["has_analysis"].to_list() == [False] and frame["stance"].to_list() == [None]
    # psycopg 把 jsonb 解成 dict；restore 要吃 dict 也要吃字串
    other = tmp_path / "other"
    other.mkdir()
    assert restore_packs(_Conn([(frame["pack"][0],)]), "serving", other, RUN) == 1
    assert index_packs(other).packs == [pack]
