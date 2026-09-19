"""T-231／D-114 §四：回測引擎的每日出場掛鉤。

語意：每個交易日**收盤後**檢查持股，觸發者**隔日開盤**出清；出清後留現金，
直到下一個再平衡日由選股重新決定。`exit_rules` 為空時行為與既有引擎逐位元相同。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

import twquant.factors.builtins  # noqa: F401
from tests.conftest import write_stock_meta
from twquant.backtest import costs as ct
from twquant.backtest.engine import BacktestResult, Trade, run_backtest
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.reporting.research_log import backtest_params_hash
from twquant.strategy.config import ExitRuleConfig, StrategyConfig

BASE = dt.date(2026, 3, 2)  # 週一
N_DAYS = 70
PEAK = 45  # 2330 漲到第 45 天，之後每天跌 3%


def _close(sid: str, i: int) -> float:
    if sid == "2330":
        top = 100.0 + 2.0 * PEAK
        return 100.0 + 2.0 * i if i <= PEAK else top * (0.97 ** (i - PEAK))
    return 100.0 + {"1101": 0.5, "2454": 0.25}[sid] * i


def _wh(tmp_path: Path) -> Warehouse:
    rows = []
    for i in range(N_DAYS):
        d = BASE + dt.timedelta(days=i)
        for sid in ("1101", "2330", "2454"):
            c = _close(sid, i)
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": c,
                    "high": c,
                    "low": c,
                    "close": c,
                    "volume": 100_000,
                    "amount": 1.0e9,
                    "limit_up_price": None,
                    "limit_down_price": None,
                    "available_at": dt.datetime(d.year, d.month, d.day, 18, tzinfo=TZ),
                }
            )
    df = pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(schemas.TW_DT),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", df, source="twse")
    write_stock_meta(wh, df)
    return wh


def _cfg(exit_rules: list[dict[str, object]] | None = None) -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            "name": "exit_bt",
            "signals": [{"factor": "price.mom_20d", "weight": 1.0}],
            "selection": {"method": "top_n", "top_n": 2},
            "weighting": "equal",
            "max_weight_per_stock": 0.6,
            "universe": {"min_adv_twd": None, "min_price": None, "max_jump_ratio": None},
            "rebalance": {"schedule": "monthly"},
            "overlays": [],
            "exit_rules": exit_rules or [],
        }
    )


def _run(tmp_path: Path, exit_rules: list[dict[str, object]] | None = None) -> BacktestResult:
    return run_backtest(
        _cfg(exit_rules),
        ct.CostConfig(),
        BASE + dt.timedelta(days=25),
        BASE + dt.timedelta(days=N_DAYS - 1),
        _wh(tmp_path),
        initial_capital=10_000_000.0,
    )


def _exit_sells(res: BacktestResult, sid: str) -> list[Trade]:
    return [
        t for t in res.ledger if t.stock_id == sid and t.side == "sell" and t.reason != "rebalance"
    ]


def test_ac1_empty_exit_rules_is_bit_identical_to_legacy(tmp_path: Path) -> None:
    a, b = _run(tmp_path / "a"), _run(tmp_path / "b", [])
    assert a.ledger == b.ledger
    assert a.equity == b.equity
    assert all(t.reason == "rebalance" for t in a.ledger)


def test_ac2_stop_loss_sells_next_open_after_the_triggering_close(tmp_path: Path) -> None:
    res = _run(tmp_path, [{"kind": "stop_loss", "pct": 0.10}])
    sells = _exit_sells(res, "2330")
    assert len(sells) == 1
    sell = sells[0]
    assert sell.reason == "stop_loss"
    buys = [
        t for t in res.ledger if t.stock_id == "2330" and t.side == "buy" and t.date < sell.date
    ]
    avg = sum(t.price * t.shares for t in buys) / sum(t.shares for t in buys)
    trigger = sell.date - dt.timedelta(days=1)
    i = (trigger - BASE).days
    assert _close("2330", i) / avg - 1.0 <= -0.10  # 觸發日收盤已達停損
    assert _close("2330", i - 1) / avg - 1.0 > -0.10  # 前一天還沒——不早賣
    assert sell.price == pytest.approx(_close("2330", i + 1))  # 成交在隔日開盤，不是觸發日


def test_ac3_below_ma_exit(tmp_path: Path) -> None:
    res = _run(tmp_path, [{"kind": "below_ma", "window": 5}])
    sells = _exit_sells(res, "2330")
    assert sells and sells[0].reason == "below_ma_5"
    # 第 PEAK+1 天收盤首次跌破 5 日均線 → 第 PEAK+2 天開盤賣
    assert sells[0].date == BASE + dt.timedelta(days=PEAK + 2)


def test_ac4_exited_cash_is_not_redeployed_until_next_rebalance(tmp_path: Path) -> None:
    res = _run(tmp_path, [{"kind": "below_ma", "window": 5}])
    sell = _exit_sells(res, "2330")[0]
    later_rebalance_days = {
        t.date for t in res.ledger if t.reason == "rebalance" and t.date > sell.date
    }
    nxt = min(later_rebalance_days) if later_rebalance_days else dt.date.max
    between = [t for t in res.ledger if sell.date < t.date < nxt]
    assert [t for t in between if t.side == "buy"] == []
    point = next(e for e in res.equity if e.date == sell.date)
    assert point.cash > 0.3 * point.equity  # 賣掉的那一半留成現金


def test_ac5_score_below_uses_factor_value_known_at_the_close(tmp_path: Path) -> None:
    rule = {"kind": "score_below", "factor": "price.mom_20d", "threshold": 0.0}
    res = _run(tmp_path, [rule])
    sells = _exit_sells(res, "2330")
    assert sells and sells[0].reason == "score_below:price.mom_20d"
    i = (sells[0].date - BASE).days - 1  # 觸發日
    assert _close("2330", i) < _close("2330", i - 20)
    assert _close("2330", i - 1) >= _close("2330", i - 21)


def test_ac6_any_rule_triggers_and_first_listed_names_the_reason(tmp_path: Path) -> None:
    rules = [{"kind": "stop_loss", "pct": 0.5}, {"kind": "below_ma", "window": 5}]
    res = _run(tmp_path, rules)
    assert _exit_sells(res, "2330")[0].reason == "below_ma_5"


def test_ac7_deterministic(tmp_path: Path) -> None:
    rules = [{"kind": "below_ma", "window": 5}]
    a, b = _run(tmp_path / "a", rules), _run(tmp_path / "b", rules)
    assert a.ledger == b.ledger and a.equity == b.equity


@pytest.mark.parametrize(
    "rule",
    [
        {"kind": "stop_loss"},
        {"kind": "stop_loss", "pct": 1.5},
        {"kind": "below_ma", "window": 1},
        {"kind": "score_below", "factor": "price.mom_20d"},
        {"kind": "score_below", "threshold": 0.1},
        {"kind": "stop_loss", "pct": 0.1, "window": 5},
    ],
)
def test_ac8_rule_config_is_validated(rule: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ExitRuleConfig.model_validate(rule)


def test_ac9_params_hash_unchanged_without_rules_and_changes_with_them() -> None:
    def _hash(cfg: StrategyConfig) -> str:
        return backtest_params_hash(
            strategy=cfg,
            cost=ct.CostConfig(),
            start=BASE,
            end=BASE + dt.timedelta(days=10),
            initial_capital=1.0e7,
            max_participation=0.05,
            source="lake",
        )

    legacy = _cfg().model_dump(mode="json", exclude={"name", "description", "trial_scope"})
    assert "exit_rules" in legacy  # 欄位存在，但空值不得進 hash
    base = _hash(_cfg())
    # 加欄位**之前**由同一份配置算出的值：既有 175 個試驗的 hash 不得因為多了一個空欄位而全部改變
    assert base == "f107d78f779f6a66"
    with_rule = _hash(_cfg([{"kind": "stop_loss", "pct": 0.15}]))
    other = _hash(_cfg([{"kind": "stop_loss", "pct": 0.20}]))
    assert len({base, with_rule, other}) == 3
