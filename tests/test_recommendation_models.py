"""T-301：ML 選股頁的模型切換與研究模型的每日名單（SPEC-014 §12，D-150）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, cast

import pytest

from twquant.conventions import TZ
from twquant.monitoring import api
from twquant.reporting.insight_run import run_id_for_day
from twquant.reporting.recommendation_export import BucketPolicy, snapshot_from_scores
from twquant.reporting.recommendation_snapshot import (
    MlRoles,
    RecommendationSnapshot,
    SnapshotWriteResult,
    save_snapshot,
)

PROD = "lgbm_prod"
TAIL = "lgbm_tail"
ROLES = MlRoles(production=PROD, research_live=frozenset({TAIL}))


def _run(run_id: str, day: str, model_id: str, minute: int = 5) -> RecommendationSnapshot:
    return RecommendationSnapshot.model_validate(
        {
            "schema_version": 1,
            "run_id": run_id,
            "model_id": model_id,
            "as_of": f"{day}T20:00:00+08:00",
            "generated_at": f"{day}T21:{minute:02d}:00+08:00",
            "published_at": None,
            "mode": "research",
            "artifact_fingerprint": "fp",
            "candidates": [
                {
                    "stock_id": "0050",
                    "rank": 1,
                    "score": 0.3,
                    "bucket": "ml_top20",
                    "reason_ref": None,
                    "insight_run_id": None,
                    "insight_available_at": None,
                    "data_status": "pending",
                }
            ],
        }
    )


def _store(tmp_path: Path) -> None:
    save_snapshot(tmp_path, _run("p1", "2026-09-21", PROD))
    save_snapshot(tmp_path, _run("p2", "2026-09-22", PROD))
    save_snapshot(tmp_path, _run("t2", "2026-09-22", TAIL, minute=40))
    save_snapshot(tmp_path, _run("t3", "2026-09-23", TAIL, minute=40))


def _ids(body: dict[str, object]) -> list[str]:
    return [r["run_id"] for r in cast(list[dict[str, Any]], body["runs"])]


def test_ac301_1_research_policy_keeps_liquidity_and_drops_the_risk_gate() -> None:
    """D-150：研究模型的名單只套流動性閘門。風控閘門會把右尾模型要找的高波動股剔掉。"""
    from twquant.cli import _research_bucket_policy

    base = BucketPolicy(
        top_n=1,
        watch_n=2,
        risk_gate_keep_pct=0.9,
        risk_scores={"1101": 0.01, "2330": 0.95, "2454": 0.5},
        liquid_ids=frozenset({"1101", "2330"}),
    )
    policy = _research_bucket_policy(base)
    assert policy is not None and policy.risk_scores is None
    day = dt.date(2026, 9, 23)
    rows = [
        {
            "date": day,
            "stock_id": sid,
            "score": s,
            "available_at": dt.datetime(2026, 9, 23, 22, tzinfo=TZ),
        }
        for sid, s in (("1101", 0.34), ("2454", 0.30), ("2330", 0.12))
    ]
    snap = snapshot_from_scores(
        rows, model_id=TAIL, day=day, artifact_fingerprint="fp", policy=policy
    )
    buckets = {c.stock_id: c.bucket for c in snap.candidates}
    assert buckets == {
        "1101": "ml_top20",  # 生產政策會把它當成風控剔除
        "2454": "ml_liquidity_excluded",
        "2330": "ml_watch",
    }
    assert _research_bucket_policy(None) is None, "分組停用時研究名單也不分組"


def test_ac301_1_export_policy_follows_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ml export-recommendations` 對研究模型不查風控分數，其餘模型照舊。"""
    from twquant import cli

    calls: list[bool] = []

    def fake(day: dt.date, *, risk_gate: bool = True) -> BucketPolicy:
        calls.append(risk_gate)
        return BucketPolicy(top_n=20, watch_n=50, risk_scores={} if risk_gate else None)

    monkeypatch.setattr(cli, "_bucket_policy", fake)
    # D-163 之後正式配置沒有研究模型；這裡驗的是「有登記時」的分流，用配置副本注入一個
    real = cli._load_ml()
    research = "lgbm_research_probe_h60_20260430_00000000"
    monkeypatch.setattr(
        cli, "_load_ml", lambda: real.model_copy(update={"research_live_models": (research,)})
    )
    day = dt.date(2026, 9, 23)
    assert cli._export_policy(research, day) is not None
    assert cli._export_policy(cli._load_ml().production_ranking_model, day) is not None
    assert calls == [False, True]


def test_ac301_1_each_research_model_is_scored_then_exported(tmp_path: Path) -> None:
    """研究模型逐一打分、匯出；一個失敗不擋其餘，失敗回傳給步驟摘要。"""
    from twquant.cli import _score_and_export_research

    exported: list[str] = []

    def score(name: str) -> Path:
        if name == "bad":
            raise RuntimeError("找不到模型目錄")
        return tmp_path / f"{name}.parquet"

    def export(path: Path, name: str) -> SnapshotWriteResult:
        exported.append(name)
        return SnapshotWriteResult(path=path, created=True)

    saved, failed = _score_and_export_research(["ok1", "bad", "ok2"], score, export)
    assert exported == ["ok1", "ok2"]
    assert saved == ["ok1.parquet", "ok2.parquet"]
    assert failed == ["bad：找不到模型目錄"]


def test_ac301_2_resolve_defaults_to_the_production_model_latest_day(tmp_path: Path) -> None:
    _store(tmp_path)
    body = api.get_platform_recommendations(tmp_path, resolve=True, roles=ROLES)
    assert _ids(body) == ["p2"]
    assert body["resolved"] == {
        "model": PROD,
        "date": "2026-09-22",
        "model_defaulted": True,
        "date_defaulted": True,
    }
    assert cast(list[dict[str, Any]], body["runs"])[0]["lane"] == "production"


def test_ac301_2_resolve_uses_the_chosen_models_own_latest_day(tmp_path: Path) -> None:
    _store(tmp_path)
    tail = api.get_platform_recommendations(tmp_path, model=TAIL, resolve=True, roles=ROLES)
    assert _ids(tail) == ["t3"]
    assert cast(list[dict[str, Any]], tail["runs"])[0]["lane"] == "research_live"
    dated = api.get_platform_recommendations(
        tmp_path, date=dt.date(2026, 9, 21), resolve=True, roles=ROLES
    )
    assert _ids(dated) == ["p1"], "給了日期仍只看預設模型，不把其他模型疊上來"
    by_run = api.get_platform_recommendations(tmp_path, run="t2", resolve=True, roles=ROLES)
    assert _ids(by_run) == ["t2"], "給了 run 就用那個 run 的模型與日期"


def test_ac301_2_without_a_production_run_the_newest_model_is_the_default(
    tmp_path: Path,
) -> None:
    save_snapshot(tmp_path, _run("t3", "2026-09-23", TAIL))
    body = api.get_platform_recommendations(tmp_path, resolve=True, roles=ROLES)
    assert _ids(body) == ["t3"]
    none = api.get_platform_recommendations(tmp_path / "empty", resolve=True, roles=ROLES)
    assert none["runs"] == [] and none["resolved"] == {
        "model": None,
        "date": None,
        "model_defaulted": True,
        "date_defaulted": True,
    }


def test_ac301_2_available_lists_every_model_with_its_days(tmp_path: Path) -> None:
    """模型選單與日期切換的依據：每個有快照的模型、角色與日期（新到舊）；不帶 resolve 時行為不變。"""
    _store(tmp_path)
    body = api.get_platform_recommendations(tmp_path, roles=ROLES)
    assert _ids(body) == ["t3", "t2", "p2", "p1"], "AC-206：不帶 resolve 仍回全部 run"
    models = {m["model_id"]: m for m in cast(dict[str, Any], body["available"])["models"]}
    assert list(models) == [PROD, TAIL], "生產模型排第一"
    assert models[PROD]["lane"] == "production"
    assert models[PROD]["dates"] == ["2026-09-22", "2026-09-21"]
    assert models[TAIL]["lane"] == "research_live"
    assert models[TAIL]["dates"] == ["2026-09-23", "2026-09-22"]


def test_ac301_3_the_insight_step_reads_the_production_run(tmp_path: Path) -> None:
    """研究模型的 run 較晚產生；不指定模型時它會被當成當日名單。"""
    _store(tmp_path)
    day = dt.date(2026, 9, 22)
    assert run_id_for_day(tmp_path, day) == "t2"  # 既有行為：最晚產生者
    assert run_id_for_day(tmp_path, day, model_id=PROD) == "p2"
    assert run_id_for_day(tmp_path, dt.date(2026, 9, 23), model_id=PROD) is None


def test_ac301_3_stock_events_leave_research_models_out(tmp_path: Path) -> None:
    """個股頁的推薦事件維持原意（生產名單）；研究模型的名單在 ML 選股頁按模型看。"""
    _store(tmp_path)
    events = api.get_platform_stock_recommendations("0050", tmp_path, roles=ROLES)["events"]
    assert [e["run_id"] for e in cast(list[dict[str, Any]], events)] == ["p1", "p2"]
