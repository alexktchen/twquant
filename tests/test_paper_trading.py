"""D-142 §二：紙上交易的每日一步，以及它與回測引擎的**逐筆一致性**。

這裡最重要的一條是 `test_ac_parity_with_the_backtest_engine`：D-142 的整個論證建立在
「前向紀錄可以拿來跟 E0 的回測比較」之上。如果兩邊的成交語意有任何差異，那個比較就是假的，
而差異會是靜默的——所以用同一份行情、同一份目標名單，逐筆比對。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

import twquant.factors.builtins  # noqa: F401
from tests.conftest import write_stock_meta
from twquant.backtest.costs import CostConfig
from twquant.backtest.engine import _price_panel, run_backtest
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.errors import ConfigError, DataError
from twquant.reporting.paper_ledger import (
    DayRecord,
    OrderRow,
    PaperTradingConfig,
    TrackOpened,
    append_event,
    load_track,
)
from twquant.reporting.paper_trading import assert_trackable, is_signal_day, step_day
from twquant.strategy.build import build_targets
from twquant.strategy.config import SignalConfig, StrategyConfig

_CC = CostConfig()
CODE = "PT-PARITY"
CAPITAL = 10_000_000.0
BASE = dt.date(2026, 4, 1)
START = dt.date(2026, 6, 1)
END = dt.date(2026, 7, 31)


def _cfg() -> PaperTradingConfig:
    return PaperTradingConfig.model_validate(
        {
            "registration": "D-142",
            "strategy": "parity",
            "model_id": "none",
            "max_participation": 0.05,
            "max_record_lag_days": 3,
            "max_mark_lag_days": 30,
            "tracks": [{"code": CODE, "label": "對照軌", "capital_twd": CAPITAL}],
            "verdict": {
                "min_signal_days": 250,
                "min_closed_trades": 200,
                "min_months": 12,
                "forward_rank_ic": 0.03,
                "month_block_t": 2.0,
            },
        }
    )


def _strategy(schedule: str = "monthly") -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            "name": "parity",
            "signals": [SignalConfig(factor="price.mom_20d", weight=1.0)],
            "selection": {"method": "top_n", "top_n": 2},
            "weighting": "equal",
            "max_weight_per_stock": 0.6,
            "universe": {"min_adv_twd": None, "min_price": None},
            "rebalance": {"schedule": schedule, "execution": "next_open"},
            "overlays": [],
        }
    )


def _warehouse(tmp_path: Path) -> Warehouse:
    """三檔、120 天。第 75 天起 2330 反轉下跌，好讓第二個再平衡日真的換股。"""
    rows = []
    for i in range(120):
        day = BASE + dt.timedelta(days=i)
        for sid, slope in {"1101": 1.0, "2330": 2.0, "2454": 0.5}.items():
            price = 100.0 + slope * i
            if sid == "2330" and i >= 75:
                price = 100.0 + 2.0 * 75 - 3.0 * (i - 75)
            if sid == "2454" and i >= 75:
                price = 100.0 + 0.5 * 75 + 4.0 * (i - 75)
            rows.append(
                {
                    "date": day,
                    "stock_id": sid,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": 100_000,
                    "amount": 1.0e9,
                    "limit_up_price": None,
                    "limit_down_price": None,
                    "available_at": dt.datetime(day.year, day.month, day.day, 18, tzinfo=TZ),
                }
            )
    frame = pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(schemas.TW_DT),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )
    warehouse = Warehouse(tmp_path)
    warehouse.write("clean", "prices_daily", frame, source="twse")
    write_stock_meta(warehouse, frame)
    return warehouse


def _open_track(root: Path, cfg: PaperTradingConfig, start: dt.date) -> None:
    append_event(
        root,
        CODE,
        TrackOpened(
            code=CODE,
            label="對照軌",
            registration="D-142",
            strategy="parity",
            strategy_fingerprint="a" * 64,
            cost_fingerprint="b" * 64,
            model_id="none",
            capital_twd=CAPITAL,
            start_date=start,
            credit_dividends=False,
        ),
        day=start,
        recorded_at=dt.datetime(start.year, start.month, start.day, 19, tzinfo=TZ),
        cfg=cfg,
    )


def _walk_forward(root: Path, warehouse: Warehouse, strat: StrategyConfig) -> list[DayRecord]:
    """一天一天往前走，就像排程器每晚做的那樣。每天都寫進帳本、每天都重讀狀態。"""
    cfg = _cfg()
    panel = _price_panel(warehouse, START, END)
    sessions = sorted(panel)
    _open_track(root, cfg, sessions[0])
    records: list[DayRecord] = []
    for day in sessions:
        state = load_track(root, CODE)
        this_month = [
            d for d in sessions if (d.year, d.month) == (day.year, day.month) and d <= day
        ]
        targets = None
        if is_signal_day(strat, day, this_month):
            frame = build_targets(strat, warehouse, day)
            targets = [
                OrderRow(stock_id=str(r["stock_id"]), target_weight=float(r["target_weight"]))
                for r in frame.to_dicts()
            ]
        record = step_day(
            state,
            day,
            panel[day],
            targets=targets,
            cost_cfg=_CC,
            max_participation=0.05,
        )
        append_event(
            root,
            CODE,
            record,
            day=day,
            recorded_at=dt.datetime(day.year, day.month, day.day, 19, tzinfo=TZ),
            cfg=cfg,
        )
        records.append(record)
    return records


def test_ac_parity_with_the_backtest_engine(tmp_path: Path) -> None:
    """同一份行情、同一份名單 → 逐筆成交與每日權益都必須一致。"""
    warehouse = _warehouse(tmp_path / "wh")
    strat = _strategy()
    expected = run_backtest(
        strat, _CC, START, END, warehouse, CAPITAL, 0.05, credit_dividends=False
    )
    records = _walk_forward(tmp_path / "ledger", warehouse, strat)

    engine_fills = [
        (t.date, t.stock_id, t.side, t.shares, round(t.price, 6), round(t.commission, 6))
        for t in expected.ledger
    ]
    panel_days = sorted(_price_panel(warehouse, START, END))
    paper_fills = [
        (day, f.stock_id, f.side, f.shares, round(f.price, 6), round(f.commission, 6))
        for day, rec in zip(panel_days, records, strict=True)
        for f in rec.fills
    ]
    assert paper_fills == engine_fills
    assert engine_fills, "測試資料沒有產生任何成交——這條測試就什麼都沒驗到"

    for point, record in zip(expected.equity, records, strict=True):
        assert record.equity == pytest.approx(point.equity, rel=1e-12)
        assert record.cash == pytest.approx(point.cash, rel=1e-12)


def test_ac_round_trip_net_return_is_after_all_costs(tmp_path: Path) -> None:
    """一筆來回結算時，報酬必須扣掉手續費、證交稅與滑價——而且與現金流對得起來。"""
    warehouse = _warehouse(tmp_path / "wh")
    _walk_forward(tmp_path / "ledger", warehouse, _strategy())
    state = load_track(tmp_path / "ledger", CODE)
    assert state.closed, "測試資料沒有任何平倉——換股那一段沒被走到"
    for trade in state.closed:
        gross = trade.proceeds / trade.cost - 1.0
        assert trade.net_return == pytest.approx(gross, abs=1e-12)
        assert trade.closed_on > trade.opened_on
        assert trade.held_sessions > 0


def test_ac_a_suspended_holding_is_marked_stale_not_silently_repriced(tmp_path: Path) -> None:
    cfg = _cfg()
    root = tmp_path / "ledger"
    day1 = dt.date(2026, 6, 1)
    _open_track(root, cfg, day1)
    prices = {"2330": {"open": 100.0, "close": 100.0, "_prev_close": 100.0, "_adv": 1e9}}
    first = step_day(
        load_track(root, CODE),
        day1,
        prices,
        targets=[OrderRow(stock_id="2330", target_weight=0.5)],
        cost_cfg=_CC,
        max_participation=0.05,
    )
    append_event(root, CODE, first, day=day1, recorded_at=_noon(day1), cfg=cfg)
    day2 = dt.date(2026, 6, 2)
    bought = step_day(
        load_track(root, CODE), day2, prices, targets=None, cost_cfg=_CC, max_participation=0.05
    )
    append_event(root, CODE, bought, day=day2, recorded_at=_noon(day2), cfg=cfg)
    assert bought.holdings and bought.holdings[0].stock_id == "2330"

    day3 = dt.date(2026, 6, 3)
    halted = step_day(
        load_track(root, CODE), day3, {}, targets=None, cost_cfg=_CC, max_participation=0.05
    )
    assert halted.stale_marks == ("2330",)
    assert halted.holdings[0].close == pytest.approx(100.0)


def test_ac_unfilled_is_recorded_not_swallowed(tmp_path: Path) -> None:
    """買不起一張（T-135）必須留下紀錄——靜默的話曝險會憑空少一塊。"""
    cfg = _cfg()
    root = tmp_path / "ledger"
    day1 = dt.date(2026, 6, 1)
    _open_track(root, cfg, day1)
    # 一張 900 萬、權重只給 1%：目標金額連一張都不到。
    prices = {"9999": {"open": 9000.0, "close": 9000.0, "_prev_close": 9000.0, "_adv": 1e12}}
    first = step_day(
        load_track(root, CODE),
        day1,
        prices,
        targets=[OrderRow(stock_id="9999", target_weight=0.01)],
        cost_cfg=_CC,
        max_participation=0.05,
    )
    append_event(root, CODE, first, day=day1, recorded_at=_noon(day1), cfg=cfg)
    day2 = dt.date(2026, 6, 2)
    record = step_day(
        load_track(root, CODE), day2, prices, targets=None, cost_cfg=_CC, max_participation=0.05
    )
    assert record.fills == ()
    assert any("9999" in u and "lot" in u for u in record.unfilled)


def test_ac_signal_day_is_the_first_session_of_the_month() -> None:
    strat = _strategy()
    sessions = [dt.date(2026, 7, d) for d in (2, 3, 6)]
    assert is_signal_day(strat, dt.date(2026, 7, 2), sessions[:1]) is True
    assert is_signal_day(strat, dt.date(2026, 7, 3), sessions[:2]) is False
    assert is_signal_day(strat, dt.date(2026, 7, 6), sessions) is False


def test_daily_schedule_is_refused_until_it_is_implemented() -> None:
    with pytest.raises(ConfigError, match="只支援月頻"):
        is_signal_day(_strategy("daily"), dt.date(2026, 7, 2), [dt.date(2026, 7, 2)])


def test_a_strategy_with_exit_rules_is_refused() -> None:
    strat = StrategyConfig.model_validate(
        {
            "name": "with_exits",
            "signals": [SignalConfig(factor="price.mom_20d", weight=1.0)],
            "selection": {"method": "top_n", "top_n": 2},
            "universe": {"min_adv_twd": None, "min_price": None},
            "rebalance": {"schedule": "monthly", "execution": "next_open"},
            "exit_rules": [{"kind": "stop_loss", "pct": 0.15}],
        }
    )
    with pytest.raises(ConfigError, match="出場規則"):
        assert_trackable(strat)
    assert_trackable(_strategy())  # E0 這一型（無出場規則、next_open）必須過


def test_step_day_refuses_an_unopened_track(tmp_path: Path) -> None:
    from twquant.reporting.paper_ledger import TrackState

    with pytest.raises(DataError, match="還沒開"):
        step_day(
            TrackState(),
            dt.date(2026, 6, 1),
            {},
            targets=None,
            cost_cfg=_CC,
            max_participation=0.05,
        )


def _noon(day: dt.date) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, 19, tzinfo=TZ)
