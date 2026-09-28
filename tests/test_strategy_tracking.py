"""T-300：策略持股的每日異動與追蹤（SPEC-014 §11）。

異動與連續天數是**讀取時**由已保存的快照比出來的——只比名單成員，不碰行情
（D-148：第二保留期開啟前不得對保留期做任何績效統計）。
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import pytest

from tests.test_live_research_strategy import _sessions, _slots_cfg, _wh
from twquant.config import load_config
from twquant.conventions import TZ
from twquant.reporting.strategy_targets import (
    LiveStrategies,
    LiveStrategy,
    ResearchEvidence,
    StrategyDisplay,
    StrategyRun,
    StrategyTargetsSnapshot,
    TargetList,
    TargetsFn,
    VaultEvidence,
    build_snapshot,
    daily_series,
    load_snapshots,
    project_snapshot,
    read_snapshot,
    save_snapshot,
    track_runs,
)

VAULT = VaultEvidence(
    total_return=0.2578,
    max_drawdown=-0.1541,
    benchmark_total_return=0.3223,
    benchmark_max_drawdown=-0.1588,
)
RESEARCH = ResearchEvidence(
    decision="D-143 §十一",
    window_start=dt.date(2017, 1, 3),
    window_end=dt.date(2026, 2, 28),
    cagr=0.128,
    max_drawdown=-0.397,
    calmar=0.32,
)
A = LiveStrategy(code="LW-A", config="lw_a", label="甲策略", vault=VAULT)
T = LiveStrategy(code="TAIL", config="tail", label="補位策略", research=RESEARCH)
D1, D2, D3, D4 = (dt.date(2026, 9, d) for d in (17, 18, 21, 22))  # 09-19（五）沒有快照


def _now(day: dt.date, hour: int = 21) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, hour, 0, tzinfo=TZ)


def _snap(
    day: dt.date,
    lists: dict[str, Sequence[str] | Exception],
    strategies: Sequence[LiveStrategy] = (A,),
    *,
    hour: int = 21,
) -> StrategyTargetsSnapshot:
    """每支策略當天的名單（代號序＝順位）；Exception＝那天算不出來。"""

    def compute(strat: LiveStrategy) -> list[tuple[str, float]]:
        got = lists[strat.code]
        if isinstance(got, Exception):
            raise got
        return [(sid, 0.05) for sid in got]

    return build_snapshot(day, strategies, compute, generated_at=_now(day, hour))


def _tracking(series_new_first: Sequence[StrategyTargetsSnapshot], lookback: int = 20) -> Any:
    series = daily_series(series_new_first)
    return track_runs(series[0], series, lookback)


def test_ac300_1_entered_and_exited_are_measured_against_the_previous_snapshot_day() -> None:
    """新進＝今天有、前一份沒有；移出＝前一份有、今天沒有（依前一份的順位）。"""
    before = _snap(D2, {"LW-A": ["1101", "2330", "2317"]})
    today = _snap(D3, {"LW-A": ["2330", "2454", "1101"]})
    got = _tracking([today, before])["LW-A"]
    assert got.status == "compared"
    assert got.previous_as_of == D2, "09-19 沒有快照：前一份是 09-18，不是空白的一天"
    assert got.entered == ("2454",)
    assert [t.stock_id for t in got.exited] == ["2317"]


def test_ac300_1_a_list_that_cannot_be_compared_is_never_marked_all_new() -> None:
    """前一份不存在、沒有這支策略、換了配置、任一天算不出來——都不比較，並說明原因。"""
    first = _snap(D1, {"LW-A": ["1101"]})
    assert _tracking([first])["LW-A"].status == "no_previous"

    newcomer = _snap(D2, {"LW-A": ["1101"], "TAIL": ["3017"]}, (A, T))
    got = _tracking([newcomer, first])
    assert got["TAIL"].status == "strategy_new"
    assert got["TAIL"].entered == () and got["TAIL"].exited == ()

    renamed = LiveStrategy(code="LW-A", config="lw_a_v2", label="甲策略", vault=VAULT)
    changed = _snap(D2, {"LW-A": ["2330"]}, (renamed,))
    assert _tracking([changed, first])["LW-A"].status == "config_changed"

    broken = _snap(D2, {"LW-A": ValueError("該日無 ML 分數")})
    after_broken = _snap(D3, {"LW-A": ["1101"]})
    assert _tracking([after_broken, broken, first])["LW-A"].status == "previous_failed"
    assert _tracking([broken, first])["LW-A"].status == "current_failed"


def test_ac300_1_an_empty_previous_list_is_a_real_list() -> None:
    """前一份空手（flat）是合法名單：今天的每一檔都是新進。今天空手則前一份全數移出。"""
    flat = _snap(D2, {"LW-A": []})
    today = _snap(D3, {"LW-A": ["2330", "2454"]})
    got = _tracking([today, flat])["LW-A"]
    assert got.status == "compared" and got.entered == ("2330", "2454")

    emptied = _snap(D4, {"LW-A": []})
    gone = _tracking([emptied, today, flat])["LW-A"]
    assert gone.status == "compared"
    assert [t.stock_id for t in gone.exited] == ["2330", "2454"]


def test_ac300_1_same_day_duplicates_resolve_to_the_latest_generated() -> None:
    """同一天有兩份快照時，比較對象是該天最晚產生的那份（決定性，不看檔名順序）。"""
    early = _snap(D2, {"LW-A": ["1101"]}, hour=20)
    late = _snap(D2, {"LW-A": ["2330"]}, hour=23)
    today = _snap(D3, {"LW-A": ["2330"]})
    got = _tracking([today, early, late])["LW-A"]
    assert got.entered == () and got.exited == ()


def test_ac300_2_streak_counts_consecutive_snapshot_days() -> None:
    """連續天數＝往回數連續出現的快照日；中間沒有快照的日子不計、也不打斷。"""
    s1 = _snap(D1, {"LW-A": ["2330"]})
    s2 = _snap(D2, {"LW-A": ["2330", "1101"]})
    s3 = _snap(D3, {"LW-A": ["2330", "1101", "2454"]})
    s4 = _snap(D4, {"LW-A": ["2330", "2454"]})
    got = _tracking([s4, s3, s2, s1])["LW-A"].streaks
    assert got["2454"].days == 2 and got["2454"].open is None
    # 2330 從最早一份快照就在：實際可能更久，標 history
    assert got["2330"].days == 4 and got["2330"].open == "history"


def test_ac300_2_open_streaks_say_why_they_may_be_longer() -> None:
    """追到上限標 lookback；遇到算不出來的那天標 failed；策略不存在的那天之前不算（確切）。"""
    s1 = _snap(D1, {"LW-A": ["2330"]})
    s2 = _snap(D2, {"LW-A": ["2330"]})
    s3 = _snap(D3, {"LW-A": ["2330"]})
    s4 = _snap(D4, {"LW-A": ["2330"]})
    capped = _tracking([s4, s3, s2, s1], lookback=3)["LW-A"].streaks["2330"]
    assert (capped.days, capped.open) == (3, "lookback")

    broken = _snap(D2, {"LW-A": ValueError("缺分數")})
    after = _tracking([s4, s3, broken, s1])["LW-A"].streaks["2330"]
    assert (after.days, after.open) == (2, "failed")

    newcomer = _snap(D3, {"LW-A": ["1101"], "TAIL": ["3017"]}, (A, T))
    later = _snap(D4, {"LW-A": ["1101"], "TAIL": ["3017"]}, (A, T))
    tail = _tracking([later, newcomer, s2, s1])["TAIL"].streaks["3017"]
    assert (tail.days, tail.open) == (2, None), "策略上架前沒有名單：天數是確切的"


def _slots(rows: Sequence[tuple[str, float]], n: int = 3) -> TargetsFn:
    return lambda _s: TargetList(
        rows=[(sid, 1.0 / n) for sid, _ in rows], kind="slots", scores=dict(rows)
    )


def test_ac300_3_slot_lists_are_ranked_by_score_and_record_it() -> None:
    """補位型的權重全部相同（1/n）：順位必須依模型分數，不能退回代號順序。"""
    snap = build_snapshot(
        D3,
        [T],
        _slots([("1303", 0.12), ("3055", 0.34), ("2221", 0.30)]),
        generated_at=_now(D3),
    )
    run = snap.strategies[0]
    assert run.kind == "slots"
    assert [(t.rank, t.stock_id, t.score) for t in run.targets] == [
        (1, "3055", 0.34),
        (2, "2221", 0.30),
        (3, "1303", 0.12),
    ]


def test_ac300_3_rebalance_lists_keep_weight_order_and_record_kind() -> None:
    def compute(_s: LiveStrategy) -> TargetList:
        return TargetList(rows=[("2330", 0.05), ("1101", 0.08)], kind="rebalance")

    run = build_snapshot(D3, [A], compute, generated_at=_now(D3)).strategies[0]
    assert run.kind == "rebalance"
    assert [(t.rank, t.stock_id, t.score) for t in run.targets] == [
        (1, "1101", None),
        (2, "2330", None),
    ]


def test_ac300_3_a_slot_list_without_scores_is_rejected() -> None:
    """補位型的順位就是分數順位；沒有分數的補位名單是上游漏了東西，不得存檔。"""
    with pytest.raises(ValueError, match="分數"):
        TargetList(rows=[("2330", 0.5)], kind="slots")
    with pytest.raises(ValueError, match="分數"):
        StrategyRun.model_validate(
            {
                "code": "TAIL",
                "config": "tail",
                "label": "補位策略",
                "status": "ok",
                "kind": "slots",
                "targets": [{"stock_id": "2330", "target_weight": 0.5, "rank": 1}],
            }
        )


def test_ac300_3_live_slot_targets_carry_the_model_scores(tmp_path: Path) -> None:
    """CLI 端：補位型策略的名單帶著排序用的分數與 kind（月頻型另走 build_targets）。"""
    from twquant.cli import _live_targets

    days = _sessions(40)
    got = _live_targets(_slots_cfg(3), _wh(tmp_path, days), days[-1])
    assert got.kind == "slots"
    assert [sid for sid, _ in got.rows] == ["2454", "2330", "1216"]
    assert got.scores is not None
    assert [got.scores[sid] for sid, _ in got.rows] == sorted(got.scores.values(), reverse=True)


def _legacy_file(tmp_path: Path, day: dt.date) -> None:
    """T-300 之前的格式：沒有 kind、沒有 score，補位名單依代號排序。原檔不可改。"""
    payload = {
        "schema_version": 1,
        "run_id": f"stgt_{day:%Y%m%d}_0123456789abcdef",
        "as_of": f"{day.isoformat()}T20:00:00+08:00",
        "generated_at": f"{day.isoformat()}T22:38:42+08:00",
        "mode": "research",
        "strategies": [
            {
                "code": "TAIL",
                "config": "tail",
                "label": "補位策略",
                "note": "",
                "status": "ok",
                "detail": None,
                "exposure": 0.2,
                "targets": [
                    {"stock_id": "1303", "target_weight": 0.1, "rank": 1},
                    {"stock_id": "2221", "target_weight": 0.1, "rank": 2},
                ],
                "vault": None,
                "research": RESEARCH.model_dump(mode="json"),
            }
        ],
    }
    root = tmp_path / "strategy_targets"
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{payload['run_id']}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_ac300_3_legacy_snapshots_still_render_with_kind_from_config(tmp_path: Path) -> None:
    """舊快照照讀；kind 由目前的策略配置補上，分數缺席就是缺席（不補、不猜）。"""
    _legacy_file(tmp_path, D4)
    found, issues = load_snapshots(tmp_path)
    assert issues == [] and found[0].strategies[0].kind is None
    series = daily_series(found)
    display = StrategyDisplay(lookback=20, kinds={"tail": "slots"})
    body = project_snapshot(found[0], series, display)
    run = cast(list[dict[str, Any]], body["strategies"])[0]
    assert run["kind"] == "slots"
    assert [t["score"] for t in run["targets"]] == [None, None]


def test_ac300_4_api_serves_tracking_dates_and_kind(tmp_path: Path) -> None:
    """端點只讀快照：附全部日期（新到舊）、每支策略的異動與每檔的新進／連續天數。"""
    from twquant.monitoring import api

    save_snapshot(tmp_path, _snap(D2, {"LW-A": ["1101", "2330"]}))
    save_snapshot(tmp_path, _snap(D3, {"LW-A": ["2330", "2454"]}))
    display = StrategyDisplay(lookback=20, kinds={"lw_a": "rebalance"})
    body = api.get_platform_strategy_targets(tmp_path, date=None, limit=1, display=display)

    assert body["available_dates"] == [D3.isoformat(), D2.isoformat()]
    assert body["tracking_lookback"] == 20
    run = cast(list[dict[str, Any]], body["snapshots"])[0]["strategies"][0]
    assert run["kind"] == "rebalance", "新快照沒記 kind 時由配置補上"
    assert run["tracking"]["status"] == "compared"
    assert run["tracking"]["previous_as_of"] == D2.isoformat()
    assert [t["stock_id"] for t in run["tracking"]["exited"]] == ["1101"]
    rows = {t["stock_id"]: t for t in run["targets"]}
    assert rows["2454"]["entered"] is True and rows["2454"]["streak_days"] == 1
    assert rows["2330"]["entered"] is False and rows["2330"]["streak_days"] == 2
    assert rows["2330"]["streak_open"] == "history"


def test_ac300_4_a_date_without_snapshot_still_lists_the_dates(tmp_path: Path) -> None:
    """選到沒有快照的日子：名單為空（尚未產生），但日期清單照給，頁面才切得回去。"""
    from twquant.monitoring import api

    save_snapshot(tmp_path, _snap(D2, {"LW-A": ["1101"]}))
    body = api.get_platform_strategy_targets(
        tmp_path, date=D1, limit=1, display=StrategyDisplay(lookback=20, kinds={})
    )
    assert body["snapshots"] == []
    assert body["available_dates"] == [D2.isoformat()]


def test_ac300_4_live_registry_declares_the_tracking_lookback() -> None:
    """回看上限是配置（黃金規則 8），且登記表裡每支策略的配置都判得出 kind。"""
    from twquant.monitoring.api import get_strategy_display

    root = Path(__file__).resolve().parents[1] / "configs"
    registry = load_config(root / "strategies_live.yaml", LiveStrategies)
    display = get_strategy_display()
    assert display.lookback == registry.tracking_lookback >= 2
    assert display.kinds["lw_e0_ctrl"] == "rebalance"
    assert set(display.kinds) == {s.config for s in registry.strategies}


def test_ac300_4_round_trip_keeps_kind_and_scores(tmp_path: Path) -> None:
    """新欄位要能原樣存檔、讀回；內容相同的重跑仍然冪等。"""
    snap = build_snapshot(
        D3, [T], _slots([("3055", 0.34), ("2221", 0.30)], n=2), generated_at=_now(D3)
    )
    first = save_snapshot(tmp_path, snap)
    again = save_snapshot(tmp_path, snap.model_copy(update={"generated_at": _now(D3, 23)}))
    assert first.created and not again.created
    back = read_snapshot(first.path).strategies[0]
    assert back.kind == "slots" and [t.score for t in back.targets] == [0.34, 0.30]
