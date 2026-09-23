"""D-112：策略持股快照。

全部用注入的 stub 計算函數——本模組的職責是「組裝與保存」，不是算權重。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from pathlib import Path

import pytest

from twquant.config import load_config
from twquant.conventions import TZ
from twquant.errors import DataError
from twquant.reporting.strategy_targets import (
    LiveStrategies,
    LiveStrategy,
    TargetsFn,
    VaultEvidence,
    build_snapshot,
    read_snapshot,
    run_id_for,
    save_snapshot,
    select_snapshots,
)

DAY = dt.date(2026, 9, 18)
NOW = dt.datetime(2026, 9, 18, 20, 30, tzinfo=TZ)
VAULT = VaultEvidence(
    total_return=0.2578,
    max_drawdown=-0.1541,
    benchmark_total_return=0.3223,
    benchmark_max_drawdown=-0.1588,
)
A = LiveStrategy(code="LW-A", config="lw_a", label="甲策略", vault=VAULT)
B = LiveStrategy(code="LW-B", config="lw_b", label="乙策略")


def _fixed(rows: Sequence[tuple[str, float]]) -> TargetsFn:
    return lambda _strat: list(rows)


def test_ac1_targets_are_ranked_by_weight_then_id() -> None:
    """名單依權重由大到小、同權重依代號排序——決定性，重跑不會換順序。"""
    snap = build_snapshot(
        DAY, [A], _fixed([("2330", 0.05), ("0050", 0.05), ("1101", 0.08)]), generated_at=NOW
    )
    run = snap.strategies[0]
    assert run.status == "ok"
    assert [(t.rank, t.stock_id) for t in run.targets] == [(1, "1101"), (2, "0050"), (3, "2330")]
    assert run.exposure == pytest.approx(0.18)


def test_ac2_vault_evidence_travels_with_the_list() -> None:
    """金庫成績必須跟著名單走：D-107 判定無策略可部署，名單不得單獨呈現。"""
    snap = build_snapshot(DAY, [A, B], _fixed([("2330", 0.05)]), generated_at=NOW)
    assert snap.strategies[0].vault == VAULT
    assert snap.strategies[1].vault is None
    assert snap.mode == "research"


def test_ac3_one_failure_is_recorded_not_dropped() -> None:
    """單支失敗不影響其餘，且一定留下紀錄（黃金規則 5：不靜默）。"""

    def compute(strat: LiveStrategy) -> list[tuple[str, float]]:
        if strat.code == "LW-A":
            raise DataError("ml_scores", DAY, "該日無 ML 分數")
        return [("2330", 0.05)]

    snap = build_snapshot(DAY, [A, B], compute, generated_at=NOW)
    failed, ok = snap.strategies
    assert failed.status == "failed" and "DataError" in (failed.detail or "")
    assert failed.targets == ()
    assert ok.status == "ok"


def test_ac3_programming_errors_are_not_swallowed() -> None:
    """已知形狀以外的例外是程式錯誤，要往上炸，不得被記成「今天算不出來」。"""

    def compute(_strat: LiveStrategy) -> list[tuple[str, float]]:
        raise KeyError("target_weight")

    with pytest.raises(KeyError):
        build_snapshot(DAY, [A], compute, generated_at=NOW)


def test_ac4_flat_day_is_distinct_from_failure() -> None:
    """空手日是合法結果，與失敗分開記。"""
    run = build_snapshot(DAY, [A], _fixed([]), generated_at=NOW).strategies[0]
    assert run.status == "flat" and run.targets == () and run.exposure == 0.0


def test_ac5_weights_over_one_are_rejected() -> None:
    """不變量：權重和 ≤ 1。"""
    with pytest.raises(ValueError, match="權重和"):
        build_snapshot(DAY, [A], _fixed([("2330", 0.6), ("2317", 0.6)]), generated_at=NOW)


def test_ac5_negative_weight_is_rejected() -> None:
    """本系統沒有放空引擎；負權重一定是上游算錯。"""
    with pytest.raises(ValueError, match="負權重"):
        build_snapshot(DAY, [A], _fixed([("2330", -0.05)]), generated_at=NOW)


def test_ac5_numeric_stock_id_is_rejected() -> None:
    """黃金規則 4：代號是字串。50 進來代表 "0050" 的前導零已經丟了。"""
    with pytest.raises(ValueError, match="stock_id"):
        build_snapshot(DAY, [A], lambda _s: [(50, 0.05)], generated_at=NOW)  # type: ignore[list-item]


def test_ac6_empty_registry_refuses_empty_snapshot() -> None:
    with pytest.raises(DataError):
        build_snapshot(DAY, [], _fixed([]), generated_at=NOW)


def test_ac7_same_content_is_idempotent_even_if_clock_differs(tmp_path: Path) -> None:
    """同一天同內容重跑成功——`generated_at` 不參與比對，時鐘不該製造衝突。"""
    rows = [("2330", 0.05)]
    first = save_snapshot(tmp_path, build_snapshot(DAY, [A], _fixed(rows), generated_at=NOW))
    later = NOW + dt.timedelta(hours=2)
    second = save_snapshot(tmp_path, build_snapshot(DAY, [A], _fixed(rows), generated_at=later))
    assert first.created and not second.created
    assert read_snapshot(first.path).generated_at == NOW  # 既有檔案未被改寫


def test_ac7_different_content_refuses_overwrite(tmp_path: Path) -> None:
    """同一天算出不同名單＝上游資料變了。拒絕覆寫，逼人去查原因。"""
    save_snapshot(tmp_path, build_snapshot(DAY, [A], _fixed([("2330", 0.05)]), generated_at=NOW))
    with pytest.raises(DataError, match="拒絕覆寫"):
        save_snapshot(
            tmp_path, build_snapshot(DAY, [A], _fixed([("2317", 0.05)]), generated_at=NOW)
        )


def test_ac8_run_id_depends_on_day_and_strategy_set() -> None:
    assert run_id_for(DAY, ["a", "b"]) == run_id_for(DAY, ["b", "a"])
    assert run_id_for(DAY, ["a"]) != run_id_for(DAY, ["a", "b"])
    assert run_id_for(DAY, ["a"]) != run_id_for(DAY + dt.timedelta(days=1), ["a"])


def test_ac9_select_returns_newest_first_and_reports_bad_files(tmp_path: Path) -> None:
    """壞檔列為問題項，不丟棄也不讓整個讀取失敗。"""
    for offset in (0, 1):
        day = DAY + dt.timedelta(days=offset)
        save_snapshot(
            tmp_path, build_snapshot(day, [A], _fixed([("2330", 0.05)]), generated_at=NOW)
        )
    bad = tmp_path / "strategy_targets" / "stgt_20260101_0123456789abcdef.json"
    bad.write_text("{not json", encoding="utf-8")

    found, issues = select_snapshots(tmp_path, limit=5)
    assert [s.as_of.date() for s in found] == [DAY + dt.timedelta(days=1), DAY]
    assert [i["run_id"] for i in issues] == [bad.stem]

    only, _ = select_snapshots(tmp_path, date=DAY, limit=5)
    assert [s.as_of.date() for s in only] == [DAY]


def test_ac10_live_registry_loads_and_every_strategy_config_exists() -> None:
    """登記表裡每一支都指得到真的策略配置，且都帶金庫成績。"""
    root = Path(__file__).resolve().parents[1] / "configs"
    registry = load_config(root / "strategies_live.yaml", LiveStrategies)
    assert registry.strategies, "登記表不得為空"
    for strat in registry.strategies:
        assert (root / "strategies" / f"{strat.config}.yaml").exists(), strat.config
        # D-147：成績可以是金庫期或研究期（標明類型），但不得兩者皆無
        assert strat.vault is not None or strat.research is not None, (
            f"{strat.code} 沒有任何成績——名單不得在沒有成績時呈現"
        )


def test_ac11_api_serves_saved_snapshots_with_evidence(tmp_path: Path) -> None:
    """端點只讀快照、不做計算；金庫成績跟著名單一起回傳。

    依本 repo 慣例直接呼叫端點函數（見 test_platform_api.py），不走 TestClient。
    """
    from twquant.monitoring import api

    save_snapshot(tmp_path, build_snapshot(DAY, [A], _fixed([("0050", 0.05)]), generated_at=NOW))
    body = api.get_platform_strategy_targets(tmp_path, date=None, limit=1)
    snapshots = body["snapshots"]
    assert isinstance(snapshots, list) and len(snapshots) == 1
    run = snapshots[0]["strategies"][0]
    assert run["targets"][0]["stock_id"] == "0050"  # 前導零原樣保留
    assert run["vault"]["benchmark_total_return"] == pytest.approx(0.3223)
    assert snapshots[0]["mode"] == "research"


def test_ac11_api_with_no_snapshots_is_empty_not_error(tmp_path: Path) -> None:
    """尚未產生任何快照是合法狀態（空清單），不是 5xx。"""
    from twquant.monitoring import api

    body = api.get_platform_strategy_targets(tmp_path, date=None, limit=1)
    assert body["snapshots"] == [] and body["issues"] == []


def test_ac12_registry_rejects_a_strategy_without_any_evidence() -> None:
    """D-147：「名單不得在沒有成績時呈現」由登記表本身把關——連載入都載不進來。"""
    with pytest.raises(ValueError, match="沒有任何成績"):
        LiveStrategies.model_validate(
            {"strategies": [{"code": "X", "config": "x", "label": "裸策略"}]}
        )


def test_ac13_research_evidence_is_carried_into_the_run() -> None:
    """研究期成績跟金庫成績一樣，一律跟著名單走（頁面靠它標明「不是金庫期」）。"""
    root = Path(__file__).resolve().parents[1] / "configs"
    registry = load_config(root / "strategies_live.yaml", LiveStrategies)
    tail = next(s for s in registry.strategies if s.code == "TAIL-B")
    assert tail.vault is None, "TAIL-B 沒有金庫期成績，不得假造"
    assert tail.research is not None and tail.research.decision.startswith("D-143")
    assert tail.research.max_drawdown < 0
