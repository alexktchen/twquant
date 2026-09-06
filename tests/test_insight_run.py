"""T-219 / PLAN §3.3: insight run orchestration — idempotency and per-candidate isolation."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest

from twquant.conventions import TZ
from twquant.errors import DataError
from twquant.insight.client import InsightClient
from twquant.insight.config import LlmConfig
from twquant.insight.schemas import CandidateAnalysis
from twquant.reporting.insight_evidence import index_packs
from twquant.reporting.insight_run import run_insights
from twquant.reporting.recommendation_snapshot import RecommendationSnapshot, save_snapshot

AS_OF = dt.datetime(2026, 9, 4, 20, 0, tzinfo=TZ)
BEFORE = dt.datetime(2026, 9, 3, 9, 0, tzinfo=TZ)
NOW = dt.datetime(2026, 9, 4, 20, 30, tzinfo=TZ)
RUN = "mlscore_20260904_abc"


def _candidate(stock_id: str, rank: int) -> dict[str, Any]:
    return {
        "stock_id": stock_id,
        "rank": rank,
        "score": 1.0 / rank,
        "bucket": "ml_candidate",
        "reason_ref": None,
        "insight_run_id": None,
        "insight_available_at": None,
        "data_status": "pending",
    }


def _snapshot(data_dir: Path, stocks: list[str]) -> RecommendationSnapshot:
    snapshot = RecommendationSnapshot.model_validate(
        {
            "schema_version": 1,
            "run_id": RUN,
            "model_id": "lgbm_net_h20",
            "as_of": AS_OF,
            "generated_at": dt.datetime(2026, 9, 4, 20, 5, tzinfo=TZ),
            "published_at": None,
            "mode": "research",
            "artifact_fingerprint": "fp",
            "candidates": [_candidate(s, i + 1) for i, s in enumerate(stocks)],
        }
    )
    save_snapshot(data_dir, snapshot)
    return snapshot


def _sections(yoy: float = 0.18) -> dict[str, Any]:
    return {
        "ml": {
            "date": dt.date(2026, 9, 4),
            "score": 0.13,
            "rank_pct": 0.99,
            "model_id": "lgbm_net_h20",
            "available_at": BEFORE,
        },
        "monthly_revenue": [
            {
                "date": dt.date(2026, 8, 10),
                "year_month": "2026-07",
                "revenue": 1234567.0,
                "yoy": yoy,
                "available_at": BEFORE,
                "available_at_estimated": False,
            }
        ],
        "institutional": [],
        "margin": [],
        "stock_valuation": None,
        "industry": None,
        "corporate_events": [],
        "disposition": [],
    }


def _fetcher(
    overrides: dict[str, Any] | None = None, *, fails: frozenset[str] = frozenset()
) -> Any:
    seen: list[str] = []

    def fetch(stock_id: str) -> dict[str, Any]:
        seen.append(stock_id)
        if stock_id in fails:
            raise RuntimeError(f"serving 讀取失敗：{stock_id}")
        section: dict[str, Any] = (overrides or {}).get(stock_id, _sections())
        return section

    fetch.seen = seen  # type: ignore[attr-defined]
    return fetch


def _client(tmp_path: Path, *, offline: bool = False) -> InsightClient:
    cfg = LlmConfig.model_validate(
        {
            "usd_twd": 32.0,
            "daily_budget_twd": 300.0,
            "models": {
                "opus": {"id": "claude-opus-5", "input_usd_per_1m": 5.0, "output_usd_per_1m": 25.0}
            },
            "tasks": {"candidate_analysis": "opus"},
        }
    )
    if offline:
        return InsightClient(cfg, tmp_path, api_key=None, backend=None)

    def backend(
        model_id: str, system: str, user: str, schema: type, max_tokens: int
    ) -> tuple[Any, int, int]:
        return (
            CandidateAnalysis.model_validate(
                {
                    "stance": "support",
                    "supporting_evidence_ids": ["fundamentals_revenue_yoy"],
                    "contradicting_evidence_ids": [],
                    "event_horizon": "medium",
                    "risks": [],
                    "missing_data": [],
                    "recommended_bucket": "watch",
                    "rationale": "月營收年增支持基本面。",
                }
            ),
            8000,
            1000,
        )

    return InsightClient(cfg, tmp_path, api_key=None, backend=backend)


def test_ac219_1_only_the_top_n_candidates_are_analysed(tmp_path: Path) -> None:
    _snapshot(tmp_path, ["0050", "2330", "2317", "2454"])
    fetch = _fetcher()

    summary = run_insights(
        tmp_path,
        recommendation_run_id=RUN,
        top_n=2,
        client=_client(tmp_path),
        now=NOW,
        fetch_sections=fetch,
    )

    assert fetch.seen == ["0050", "2330"]  # Rank order, capped at N.
    assert summary.analysed == 2
    assert summary.saved == 2
    assert len(index_packs(tmp_path).packs) == 2


def test_ac219_1_rerunning_the_same_inputs_creates_nothing_new(tmp_path: Path) -> None:
    _snapshot(tmp_path, ["0050"])
    first = run_insights(
        tmp_path,
        recommendation_run_id=RUN,
        top_n=1,
        client=_client(tmp_path),
        now=NOW,
        fetch_sections=_fetcher(),
    )
    again = run_insights(
        tmp_path,
        recommendation_run_id=RUN,
        top_n=1,
        client=_client(tmp_path),
        now=NOW,
        fetch_sections=_fetcher(),
    )

    assert (first.saved, first.reused) == (1, 0)
    assert (again.saved, again.reused) == (0, 1)
    assert len(index_packs(tmp_path).packs) == 1


def test_ac219_1_changed_evidence_makes_a_new_pack_and_keeps_the_old_one(tmp_path: Path) -> None:
    _snapshot(tmp_path, ["0050"])
    run_insights(
        tmp_path,
        recommendation_run_id=RUN,
        top_n=1,
        client=_client(tmp_path),
        now=NOW,
        fetch_sections=_fetcher(),
    )
    run_insights(
        tmp_path,
        recommendation_run_id=RUN,
        top_n=1,
        client=_client(tmp_path),
        now=NOW,
        fetch_sections=_fetcher({"0050": _sections(yoy=0.42)}),
    )

    packs = index_packs(tmp_path).packs
    assert len(packs) == 2  # The earlier analysis is evidence of what was known then.
    assert len({p.insight_run_id for p in packs}) == 2


def test_ac219_2_one_candidate_failing_does_not_stop_the_others(tmp_path: Path) -> None:
    _snapshot(tmp_path, ["0050", "2330", "2317"])

    summary = run_insights(
        tmp_path,
        recommendation_run_id=RUN,
        top_n=3,
        client=_client(tmp_path),
        now=NOW,
        fetch_sections=_fetcher(fails=frozenset({"2330"})),
    )

    assert summary.saved == 2
    assert [f["stock_id"] for f in summary.failures] == ["2330"]
    saved = {p.stock_id for p in index_packs(tmp_path).packs}
    assert saved == {"0050", "2317"}
    # The failure detail never leaks a filesystem path to the caller.
    assert all(str(tmp_path) not in f["detail"] for f in summary.failures)


def test_ac219_2_an_offline_llm_still_saves_the_evidence_with_a_stated_reason(
    tmp_path: Path,
) -> None:
    _snapshot(tmp_path, ["0050"])

    summary = run_insights(
        tmp_path,
        recommendation_run_id=RUN,
        top_n=1,
        client=_client(tmp_path, offline=True),
        now=NOW,
        fetch_sections=_fetcher(),
    )

    assert summary.analysed == 0
    assert summary.absent == 1
    assert summary.saved == 1
    pack = index_packs(tmp_path).packs[0]
    assert pack.analysis is None
    assert "離線" in (pack.analysis_absent_reason or "")
    assert len(pack.evidence) > 0  # Evidence is still worth keeping without an analysis.


def test_ac219_3_the_pack_links_back_to_the_run_without_touching_the_snapshot(
    tmp_path: Path,
) -> None:
    _snapshot(tmp_path, ["0050"])
    snapshot_file = tmp_path / "recommendations" / f"{RUN}.json"
    before = snapshot_file.read_bytes()

    run_insights(
        tmp_path,
        recommendation_run_id=RUN,
        top_n=1,
        client=_client(tmp_path),
        now=NOW,
        fetch_sections=_fetcher(),
    )

    # PLAN §3.3: a published snapshot is immutable; the link lives on the pack.
    assert snapshot_file.read_bytes() == before
    pack = index_packs(tmp_path).packs[0]
    assert pack.recommendation_run_id == RUN
    assert pack.stock_id == "0050"
    assert pack.as_of == AS_OF


def test_ac219_3_an_unknown_run_is_refused_before_any_work(tmp_path: Path) -> None:
    fetch = _fetcher()
    with pytest.raises(DataError, match="找不到"):
        run_insights(
            tmp_path,
            recommendation_run_id="mlscore_absent",
            top_n=1,
            client=_client(tmp_path),
            now=NOW,
            fetch_sections=fetch,
        )
    assert fetch.seen == []
