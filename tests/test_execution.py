"""T-021：PaperBroker + 每日流程（成交同源、idempotency、盤前中止、HALTED、對帳）。"""

from __future__ import annotations

import datetime as dt

import pytest

from twquant.backtest.costs import CostConfig
from twquant.backtest.fill import decide_fill
from twquant.errors import ExecutionError
from twquant.execution.broker import Fill, OrderAck, PaperBroker, Position, make_order
from twquant.execution.daily import (
    build_orders,
    preflight_check,
    reconcile,
    run_session,
)

_CC = CostConfig()
_DAY = dt.date(2026, 6, 30)
_MKT = {"2330": {"adv": 1e12, "limit_up": None, "limit_down": None, "price": 1000.0}}


# ── AC-1：PaperBroker 成交 == decide_fill（同源）───────────────────
def test_paper_fill_equals_decide_fill() -> None:
    broker = PaperBroker(cost_cfg=_CC, _cash=1e8, day=_DAY, market=_MKT)
    order = make_order(_DAY, "2330", "buy", 10_000, 1000.0)
    ack = broker.place(order)
    d = decide_fill(
        side="buy",
        price=1000.0,
        shares=10_000,
        limit_up=None,
        limit_down=None,
        adv=1e12,
        max_participation=0.05,
        available_cash=1e8,
        cost_cfg=_CC,
    )
    assert ack.filled_shares == d.filled_shares
    assert d.cost is not None
    assert ack.commission == pytest.approx(d.cost.commission)
    assert ack.slippage == pytest.approx(d.cost.slippage)


# ── AC-2：idempotency ─────────────────────────────────────────────
def test_idempotency_no_double_fill() -> None:
    broker = PaperBroker(cost_cfg=_CC, _cash=1e8, day=_DAY, market=_MKT)
    order = make_order(_DAY, "2330", "buy", 5_000, 1000.0)
    a1 = broker.place(order)
    a2 = broker.place(order)  # 重送
    assert a1 == a2
    assert len(broker.fills(_DAY)) == 1  # 只成交一次
    assert broker.positions() == [Position("2330", 5_000)]


# ── AC-6：HALTED → raise ──────────────────────────────────────────
def test_halted_rejects() -> None:
    broker = PaperBroker(cost_cfg=_CC, _cash=1e8, day=_DAY, market=_MKT, status="HALTED")
    with pytest.raises(ExecutionError, match="HALTED"):
        broker.place(make_order(_DAY, "2330", "buy", 1000, 1000.0))


# ── AC-3：盤前不符 → 中止、0 次 place ─────────────────────────────
class _SpyBroker:
    name = "spy"

    def __init__(self) -> None:
        self.place_count = 0

    def place(self, order: object) -> OrderAck:
        self.place_count += 1
        return OrderAck("x", "x", "2330", "buy", 0, 0.0, 0.0, 0.0, 0.0, [], "rejected")

    def positions(self) -> list[Position]:
        return [Position("2330", 2000)]  # 券商有 2000

    def cash(self) -> float:
        return 1e6

    def fills(self, day: dt.date) -> list[Fill]:
        return []


def test_preflight_mismatch_aborts_no_place() -> None:
    spy = _SpyBroker()
    # 帳本記 2330 只有 1000 股（差 1000 > 容差）→ 應中止
    res = run_session(
        spy, {"2330": 1.0}, _MKT, 1e6, _DAY, ledger_positions={"2330": 1000}, ledger_cash=1e6
    )
    assert res.aborted is True
    assert spy.place_count == 0


def test_preflight_within_tolerance_ok() -> None:
    pf = preflight_check([Position("2330", 1000)], 1e6, {"2330": 1000}, 1e6)
    assert pf.ok is True


# ── build_orders + 對帳 ───────────────────────────────────────────
def test_build_orders_diff() -> None:
    orders = build_orders({"2330": 1.0}, {}, _MKT, 1e6, _DAY)
    assert len(orders) == 1
    assert orders[0].side == "buy"
    # 1e6 / (1000×1000) = 1 張
    assert orders[0].shares == 1000


def test_reconcile_detects_unexpected_fill() -> None:
    ack = OrderAck("k", "k", "2330", "buy", 1000, 1000.0, 0.0, 0.0, 0.0, [], "filled")
    good = reconcile([ack], [Fill(_DAY, "2330", "buy", 1000, 1000.0, 0.0, 0.0, 0.0)])
    assert good.ok is True
    # 券商多出一筆系統沒送的成交
    extra = reconcile(
        [ack],
        [
            Fill(_DAY, "2330", "buy", 1000, 1000.0, 0.0, 0.0, 0.0),
            Fill(_DAY, "1101", "buy", 2000, 50.0, 0.0, 0.0, 0.0),
        ],
    )
    assert extra.ok is False
    assert ("1101", "buy", 2000) in extra.unexpected


def test_run_session_paper_end_to_end() -> None:
    # 現金留成本餘裕（買 1 張 @1000 需 100 萬 + 費用）
    broker = PaperBroker(cost_cfg=_CC, _cash=2e6, day=_DAY, market=_MKT)
    res = run_session(broker, {"2330": 1.0}, _MKT, 1e6, _DAY, {}, 2e6)
    assert res.aborted is False
    assert res.reconcile is not None and res.reconcile.ok is True
    assert broker.positions() == [Position("2330", 1000)]
