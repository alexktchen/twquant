"""T-318／D-164：每日選股看板——帳本轉進出與持股、下一個交易日的計畫、API 空狀態。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import cast

import pytest

import twquant.factors.builtins  # noqa: F401
from tests.test_backtest_slots import BASE, N_DAYS, START, _wh
from twquant.backtest import costs as ct
from twquant.backtest.engine import NextPlan, SplitAdjustment, Trade, run_backtest
from twquant.config import load_config
from twquant.reporting.pick_board import (
    BoardStrategy,
    PickBoardConfig,
    board_payload,
    load_board,
    new_names,
    plan_payload,
    replay_days,
    save_board,
    strategy_payload,
)
from twquant.strategy.config import StrategyConfig

D = [dt.date(2026, 9, 1) + dt.timedelta(days=i) for i in range(6)]
ROOT = Path(__file__).resolve().parents[1] / "configs"


def _t(day: int, sid: str, side: str, shares: int, reason: str = "entry") -> Trade:
    return Trade(D[day], sid, side, shares, 10.0, 0.0, 0.0, 0.0, reason)


def test_ac1_first_buy_is_entry_and_sell_to_zero_is_exit() -> None:
    days = replay_days(
        D,
        [
            _t(0, "2330", "buy", 100),
            _t(1, "0050", "buy", 50),
            _t(3, "2330", "sell", 100, "time_stop"),
        ],
        [],
    )
    assert [r["stock_id"] for r in days[0].entered] == ["2330"]
    assert [r["stock_id"] for r in days[1].entered] == ["0050"]  # 前導零原樣保留
    assert days[3].exited == [
        {"stock_id": "2330", "reason": "time_stop", "entry_date": "2026-09-01", "held_sessions": 3}
    ]
    assert [h["stock_id"] for h in days[2].holdings] == ["2330", "0050"]  # 依進場先後
    assert days[2].holdings[0]["held_sessions"] == 2
    assert [h["stock_id"] for h in days[5].holdings] == ["0050"]


def test_ac1_topups_and_trims_are_not_entries_or_exits() -> None:
    days = replay_days(
        D,
        [
            _t(0, "2330", "buy", 100),
            _t(2, "2330", "buy", 20, "rebalance"),
            _t(4, "2330", "sell", 60, "rebalance"),
        ],
        [],
    )
    assert [len(d.entered) for d in days] == [1, 0, 0, 0, 0, 0]
    assert all(not d.exited for d in days)
    assert days[5].holdings[0]["entry_date"] == "2026-09-01"


def test_ac1_splits_adjust_shares_so_the_full_sale_still_closes() -> None:
    split = SplitAdjustment(D[2], "2330", 0.5, 100, 200)
    days = replay_days(
        D, [_t(0, "2330", "buy", 100), _t(4, "2330", "sell", 200, "time_stop")], [split]
    )
    assert [r["stock_id"] for r in days[4].exited] == ["2330"]


def test_ac1_ledger_that_does_not_reconcile_raises() -> None:
    with pytest.raises(ValueError, match="手上只有"):
        replay_days(D, [_t(0, "2330", "buy", 100), _t(1, "2330", "sell", 101, "x")], [])
    with pytest.raises(ValueError, match="不在重播日曆"):
        replay_days(D[:2], [_t(4, "2330", "buy", 100)], [])


def test_ac2_plan_rebalance_is_the_difference_to_the_target() -> None:
    plan = NextPlan(rebalance=(("1101", 0.5), ("2330", 0.5), ("2454", 0.0)), exits=(), entries=())
    got = plan_payload(plan, {"2330", "2603"})
    assert got == {
        "rebalance": True,
        "buy": ["1101"],
        "sell": [{"stock_id": "2603", "reason": "rebalance"}],
    }


def test_ac2_plan_slots_lists_entries_and_exits() -> None:
    plan = NextPlan(rebalance=None, exits=(("2330", "time_stop"),), entries=("1101", "2330"))
    got = plan_payload(plan, {"2330"})
    assert got["buy"] == ["1101"]  # 已持有的不列為買進
    assert got["sell"] == [{"stock_id": "2330", "reason": "time_stop"}]


def _slot_cfg() -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            "name": "slot_plan",
            "signals": [{"factor": "price.mom_20d", "weight": 1.0}],
            "universe": {"min_adv_twd": None, "min_price": None, "max_jump_ratio": None},
            "overlays": [],
            "exit_rules": [{"kind": "time_stop", "sessions": 5}],
            "slots": {"n": 2, "entry_threshold": 0.05},
        }
    )


def test_ac2_next_plan_is_exactly_what_the_engine_executes_the_next_open(tmp_path: Path) -> None:
    """重播到第 k 天的「明日計畫」＝重播到第 k+1 天時，第 k+1 天開盤實際成交的單。"""
    wh = _wh(tmp_path)
    cfg = _slot_cfg()
    start = BASE + dt.timedelta(days=START)
    checked = 0
    for k in range(START + 1, N_DAYS - 1):
        day, nxt = BASE + dt.timedelta(days=k), BASE + dt.timedelta(days=k + 1)
        plan = run_backtest(cfg, ct.CostConfig(), start, day, wh).next_plan
        done = [
            t for t in run_backtest(cfg, ct.CostConfig(), start, nxt, wh).ledger if t.date == nxt
        ]
        assert plan is not None
        assert sorted(sid for sid, _ in plan.exits) == sorted(
            t.stock_id for t in done if t.side == "sell"
        )
        bought = [t.stock_id for t in done if t.side == "buy"]
        assert bought == [sid for sid in plan.entries if sid in bought]  # 依計畫順序、只少不多
        checked += bool(plan.exits or plan.entries)
    assert checked >= 2, "fixture 應至少有兩天有進出，否則這條測試沒驗到東西"


def test_ac3_payload_shows_only_the_display_window_and_its_names() -> None:
    days = replay_days(D, [_t(0, "2330", "buy", 100), _t(5, "0050", "buy", 10)], [])
    strat = BoardStrategy(code="QUIET", name="潛伏飆股", config="quiet_s0", summary="測試")
    plan = NextPlan(rebalance=None, exits=(), entries=("1101",))
    one = strategy_payload(
        strat, "slots", 20, 15, days, plan, display_sessions=2, equity=[(D[0], 1.0), (D[5], 1.1)]
    )
    shown = cast("list[dict[str, object]]", one["days"])
    assert [d["date"] for d in shown] == ["2026-09-05", "2026-09-06"]
    assert one["equity"] == [
        {"date": "2026-09-01", "value": 1.0},
        {"date": "2026-09-06", "value": 1.1},
    ], "淨值曲線是整段重播，不只顯示窗"
    cfg = PickBoardConfig(
        replay_start=D[0],
        display_sessions=2,
        initial_capital=1.0,
        lot_size=1,
        max_participation=0.05,
        watch_lookback_sessions=2,
        html_window_sessions=6,
        strategies=(strat,),
    )
    board = board_payload(
        as_of=D[5],
        generated_at=dt.datetime(2026, 9, 6, 20, tzinfo=dt.UTC),
        cfg=cfg,
        strategies=[one],
        stocks={"2330": {"name": "台積電"}, "9999": {"name": "無關"}},
    )
    stocks = cast("dict[str, dict[str, str]]", board["stocks"])
    assert set(stocks) == {"2330", "0050", "1101"}
    assert stocks["0050"] == {}
    # D-165 起帶淨值曲線（使用者裁決）；仍不帶任何事後算好的績效統計（報酬率、夏普等由前端就淨值畫）
    assert "return" not in str(board) and "sharpe" not in str(board)
    assert board["initial_capital"] == 1.0


def test_ac4_api_serves_the_saved_board_and_is_empty_before_the_first_run(tmp_path: Path) -> None:
    from twquant.monitoring import api

    assert api.get_platform_pick_board(tmp_path) == {"board": None}
    save_board(tmp_path, {"schema_version": 1, "as_of": "2026-09-24", "strategies": []})
    assert load_board(tmp_path) == api.get_platform_pick_board(tmp_path)["board"]


def test_ac5_board_config_loads_and_points_at_real_strategies() -> None:
    cfg = load_config(ROOT / "pick_board.yaml", PickBoardConfig)
    # D-163 兩條正式線＋D-174（使用者 2026-10-11 決定）第三條「高分爆量（驗證中）」＋D-215 第四條「帶量突破」
    assert [s.code for s in cfg.strategies] == ["CORE", "QUIET", "QUIETV", "BREAKOUT"]
    for s in cfg.strategies:
        assert (ROOT / "strategies" / f"{s.config}.yaml").exists(), s.config


def test_ac6_new_names_are_those_absent_from_the_top_n_for_the_lookback() -> None:
    ranked = [
        (D[0], ["2330", "0050"]),
        (D[1], ["2330", "1101"]),
        (D[2], ["2330", "2454"]),
        (D[3], ["0050", "2454"]),
        (D[4], ["0050", "1101"]),
    ]
    got = new_names(ranked, lookback=2)
    assert list(got) == [D[2], D[3], D[4]], "前 lookback 天沒有完整回看，不輸出"
    assert got[D[2]] == ["2454"]
    assert got[D[3]] == ["0050"], "0050 在第 0 天出現過，但已超過 2 天回看"
    assert got[D[4]] == ["1101"]


def test_ac6_watch_list_must_cover_every_shown_day() -> None:
    days = replay_days(D, [], [])
    strat = BoardStrategy(
        code="QUIET", name="潛伏飆股", config="quiet_s0", summary="測試", watch=True
    )
    full: dict[dt.date, list[str]] = {d: [] for d in D}
    one = strategy_payload(strat, "slots", 20, 15, days, None, 2, {**full, D[5]: ["1101"]})
    shown = cast("list[dict[str, object]]", one["days"])
    assert shown[-1]["watch_new"] == ["1101"]
    with pytest.raises(ValueError, match="觀察名單缺"):
        strategy_payload(strat, "slots", 20, 15, days, None, 2, {D[4]: []})


def test_ac7_picks_list_the_full_top_n_even_when_every_slot_is_full() -> None:
    days = replay_days(D, [_t(0, "2330", "buy", 100)], [])
    strat = BoardStrategy(
        code="QUIET", name="潛伏飆股", config="quiet_s0", summary="測試", watch=True
    )
    # ranked 的值型別是 list[str] | None（模型沒打分那天是 None），dict 不變性要求這裡明寫
    top: dict[dt.date, list[str] | None] = {d: ["2330", "1101", "0050"] for d in D}
    new = {d: ["0050"] for d in D}
    one = strategy_payload(strat, "slots", 20, 1, days, None, 2, new, top)
    last = cast("list[dict[str, object]]", one["days"])[-1]
    assert last["picks"] == [
        {"stock_id": "2330", "rank": 1, "held": True, "new": False},
        {"stock_id": "1101", "rank": 2, "held": False, "new": False},
        {"stock_id": "0050", "rank": 3, "held": False, "new": True},
    ], "唯一的位子已被 2330 佔住，其餘兩檔照樣列為推薦"
    unscored = strategy_payload(strat, "slots", 20, 1, days, None, 2, new, {**top, D[5]: None})
    last = cast("list[dict[str, object]]", unscored["days"])[-1]
    assert last["picks"] is None and last["picks_missing"] == "no_scores", (
        "沒打分要明示，不是空名單"
    )
    with pytest.raises(ValueError, match="推薦名單缺"):
        strategy_payload(strat, "slots", 20, 1, days, None, 2, new, {D[5]: []})


def _tp(day: int, sid: str, side: str, shares: int, price: float, reason: str = "entry") -> Trade:
    return Trade(D[day], sid, side, shares, price, 0.0, 0.0, 0.0, reason)


def test_ac8_changes_run_from_fill_price_to_close_or_sale() -> None:
    closes = {
        D[0]: {"2330": 11.0, "1101": 20.0},
        D[1]: {"2330": 12.0, "1101": 22.0},
        # D[2]：2330 停牌，沿用上一個收盤
        D[2]: {"1101": 24.0},
        D[3]: {"2330": 9.0},
        D[4]: {"2330": 9.0},
        D[5]: {"2330": 13.0},
    }
    days = replay_days(
        D,
        [
            _tp(0, "2330", "buy", 100, 10.0),
            _tp(0, "1101", "buy", 10, 20.0),
            _tp(1, "1101", "sell", 5, 22.0, "trim"),  # 減碼：已賣金額留著算
            _tp(3, "1101", "sell", 5, 18.0, "time_stop"),
        ],
        [],
        closes,
    )
    held = {str(h["stock_id"]): h["change"] for h in days[2].holdings}
    assert held["2330"] == pytest.approx(0.2), "停牌沿用 D[1] 收盤 12"
    assert held["1101"] == pytest.approx((5 * 22 + 5 * 24) / 200 - 1)
    assert days[3].exited[0]["change"] == pytest.approx((5 * 22 + 5 * 18) / 200 - 1)
    by_sid = {str(e["stock_id"]): e for e in days[0].entered}
    assert by_sid["1101"]["change_to"] == "exit"
    assert by_sid["1101"]["change"] == days[3].exited[0]["change"]
    assert by_sid["2330"]["change_to"] == "now"
    assert by_sid["2330"]["change"] == pytest.approx(0.3), "仍持有：算到最後一天收盤 13"


def test_ac8_split_keeps_the_change_on_the_same_money() -> None:
    days = replay_days(
        D[:3],
        [_tp(0, "2330", "buy", 100, 10.0)],
        [SplitAdjustment(D[1], "2330", 0.5, 100, 200)],
        {D[0]: {"2330": 10.0}, D[1]: {"2330": 5.5}, D[2]: {"2330": 5.5}},
    )
    assert days[1].holdings[0]["change"] == pytest.approx(0.1)


def test_ac8_missing_close_on_the_buy_day_raises() -> None:
    with pytest.raises(ValueError, match="沒有收盤價"):
        replay_days(D[:2], [_tp(0, "2330", "buy", 100, 10.0)], [], {D[1]: {"2330": 10.0}})


def test_d174_only_threshold_line_allows_empty_list() -> None:
    """D-174／D-215：只有「高分爆量」「帶量突破」宣告當天可以沒有股票；兩條正式線空名單仍是錯誤。"""
    from twquant.config import load_config
    from twquant.reporting.pick_board import PickBoardConfig

    cfg = load_config(Path("configs/pick_board.yaml"), PickBoardConfig)
    assert {s.code: s.allow_empty for s in cfg.strategies} == {
        "CORE": False,
        "QUIET": False,
        "QUIETV": True,
        "BREAKOUT": True,
    }
