"""SPEC-004 AC-10：回測成本與同成交路徑 gross 診斷。"""

from __future__ import annotations

import datetime as dt

import pytest

from twquant.backtest.diagnostics import summarize_backtest
from twquant.backtest.engine import BacktestResult, EquityPoint, Trade
from twquant.backtest.metrics import compute_metrics


def _result() -> BacktestResult:
    days = [dt.date(2026, 1, d) for d in (2, 5, 6)]
    equity = [
        EquityPoint(days[0], 990.0, 490.0, 500.0),
        EquityPoint(days[1], 1020.0, 770.0, 250.0),
        EquityPoint(days[2], 1010.0, 1010.0, 0.0),
    ]
    ledger = [
        Trade(days[0], "1101", "buy", 1000, 0.5, 4.0, 0.0, 6.0),
        Trade(days[1], "1101", "sell", 500, 0.5, 2.0, 1.0, 2.0),
        Trade(days[2], "1101", "sell", 500, 0.5, 1.0, 0.5, 0.5),
    ]
    values = [e.equity for e in equity]
    return BacktestResult(
        equity=equity,
        ledger=ledger,
        unfilled=[],
        metrics=compute_metrics(values, turnover_annualized=1.25),
        initial_capital=1000.0,
        final_equity=values[-1],
    )


def test_ac10_cost_components_equal_ledger() -> None:
    result = _result()
    diag = summarize_backtest(result)

    assert diag.commission == pytest.approx(7.0)
    assert diag.tax == pytest.approx(1.5)
    assert diag.slippage == pytest.approx(8.5)
    assert diag.total_cost == pytest.approx(17.0)
    assert diag.cost_to_initial == pytest.approx(0.017)


def test_ac10_same_path_gross_net_identity() -> None:
    result = _result()
    diag = summarize_backtest(result)

    # 各日累計成本 10、15、17；same-path gross 明確不是零成本重跑。
    assert diag.same_path_gross_equity == pytest.approx((1000.0, 1035.0, 1027.0))
    cumulative = (10.0, 15.0, 17.0)
    for net, gross, cost in zip(
        result.equity, diag.same_path_gross_equity, cumulative, strict=True
    ):
        assert gross == pytest.approx(net.equity + cost)


def test_ac10_average_holding_sessions_uses_position_cycle() -> None:
    diag = summarize_backtest(_result())

    # 1/2 建倉、1/5 部分賣出不結束、1/6 歸零；跨 2 個 equity sessions。
    assert diag.average_holding_sessions == pytest.approx(2.0)


def test_ac10_diagnostics_do_not_mutate_result() -> None:
    result = _result()
    before_equity = tuple(result.equity)
    before_ledger = tuple(result.ledger)

    summarize_backtest(result)

    assert tuple(result.equity) == before_equity
    assert tuple(result.ledger) == before_ledger


def test_ac10_empty_diagnostics_are_finite_zeroes() -> None:
    result = BacktestResult(
        equity=[],
        ledger=[],
        unfilled=[],
        metrics=compute_metrics([], turnover_annualized=0.0),
        initial_capital=1000.0,
        final_equity=1000.0,
    )
    diag = summarize_backtest(result)
    assert diag.commission == 0.0
    assert diag.tax == 0.0
    assert diag.slippage == 0.0
    assert diag.total_cost == 0.0
    assert diag.cost_to_initial == 0.0
    assert diag.same_path_gross_equity == ()
    assert diag.average_holding_sessions == 0.0
    assert diag.trade_count == 0
    assert diag.unfilled_count == 0
    assert diag.unfilled_by_reason == {}
