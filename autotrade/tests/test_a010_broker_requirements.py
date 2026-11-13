"""A-010：期貨商對程式交易的六項要求（下單頻率、單筆口數、停損、斷線重連、被阻擋時暫停、部位比對與修正）。"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from autotrade.broker.base import BrokerEvent, Rejected
from autotrade.broker.sim import SimBroker
from autotrade.config import Config, ConfigError, RiskConfig, load_config, parse_config
from autotrade.engine import Engine
from autotrade.journal import Journal, read_journal, recover
from autotrade.model import TAIPEI, Fill, OrderRequest, Side, Tick
from autotrade.risk import (
    HALT_BROKER_REJECT,
    HALT_MISMATCH,
    HALT_ORDER_RATE,
    RiskManager,
)

CONFIG_PATH = Path(__file__).parents[1] / "configs" / "tmf_sim.toml"
SYMBOL = "TMF"
T0 = datetime(2026, 11, 13, 9, 0, 0, tzinfo=TAIPEI)
MANY = 10_000


def cfg(**risk: object) -> Config:
    base = load_config(CONFIG_PATH)
    defaults: dict[str, object] = {
        "max_entries_per_day": MANY,
        "max_orders_per_minute": None,
        "max_consecutive_broker_rejects": None,
        "position_stop_loss_twd": None,
    }
    return replace(base, risk=replace(base.risk, **{**defaults, **risk}))  # type: ignore[arg-type]


def tick(seconds: float, price: float = 27000.0) -> Tick:
    return Tick(ts=T0 + timedelta(seconds=seconds), price=price, qty=1)


@dataclass
class Scripted:
    """照劇本送單的測試策略：{第幾筆行情: 買賣別}。"""

    plan: dict[int, Side]
    seen: int = 0
    fills: list[Fill] = field(default_factory=list)
    rejects: list[str] = field(default_factory=list)

    def on_tick(self, tick: Tick, position: int) -> list[OrderRequest]:
        side = self.plan.get(self.seen)
        self.seen += 1
        if side is None:
            return []
        return [OrderRequest(ts=tick.ts, symbol=SYMBOL, side=side, qty=1)]

    def on_fill(self, fill: Fill) -> None:
        self.fills.append(fill)

    def on_reject(self, req: OrderRequest, reason: str) -> None:
        self.rejects.append(reason)


@dataclass
class FlakyBroker:
    """會出狀況的測試券商：拒單、斷線時把回報扣住、委託沒送到、部位多出來、多出不認得的委託。"""

    inner: SimBroker
    reject_all: bool = False
    reject_once: int = 0
    drop_next: bool = False
    down: bool = False
    drift: int = 0
    foreign: set[str] = field(default_factory=set)
    submitted: list[OrderRequest] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    _held: list[BrokerEvent] = field(default_factory=list)
    _reject_queue: list[str] = field(default_factory=list)
    _fake_seq: int = 0

    def submit(self, req: OrderRequest) -> str:
        self.submitted.append(req)
        if self.reject_all or self.reject_once > 0 or self.drop_next:
            self._fake_seq += 1
            order_id = f"X{self._fake_seq:06d}"
            if self.drop_next:
                self.drop_next = False
            else:
                self.reject_once = max(0, self.reject_once - 1)
                self._reject_queue.append(order_id)
            return order_id
        return self.inner.submit(req)

    def cancel(self, order_id: str) -> None:
        self.cancelled.append(order_id)
        if order_id not in self.foreign:
            self.inner.cancel(order_id)

    def on_tick(self, tick: Tick) -> list[BrokerEvent]:
        events: list[BrokerEvent] = [
            Rejected(order_id, tick.ts, "測試：券商拒絕") for order_id in self._reject_queue
        ]
        self._reject_queue.clear()
        events += self.inner.on_tick(tick)
        if self.down:
            self._held += events
            return []
        return events

    def resync(self) -> list[BrokerEvent]:
        held, self._held = self._held, []
        return held

    def open_order_ids(self) -> frozenset[str]:
        return self.inner.open_order_ids() | frozenset(self.foreign)

    def position(self) -> int:
        return self.inner.position() + self.drift


def engine_with(
    plan: dict[int, Side], config: Config, **broker: object
) -> tuple[Engine, FlakyBroker]:
    flaky = FlakyBroker(SimBroker(config.sim), **broker)  # type: ignore[arg-type]
    return Engine(config, Scripted(plan), flaky), flaky


def kinds(engine: Engine) -> list[str]:
    return [e.kind for e in engine.events]


def scripted(engine: Engine) -> Scripted:
    assert isinstance(engine.strategy, Scripted)
    return engine.strategy


# ---- AC1 下單頻率限制（問卷第 4 題）


def test_ac1_order_rate_limit_suspends_all_orders_until_released() -> None:
    engine, broker = engine_with(
        {0: Side.BUY, 1: Side.SELL, 2: Side.BUY, 3: Side.SELL}, cfg(max_orders_per_minute=3)
    )
    for s in range(6):
        engine.on_tick(tick(s))
    assert len(broker.submitted) == 3
    assert engine.risk.halt == HALT_ORDER_RATE
    assert engine.ledger.position == 1  # 第四筆（平倉）也被擋：暫停是「什麼都不送」
    assert [e.kind for e in engine.events if e.kind == "halt"] == ["halt"]
    engine.release_kill(tick(70).ts)
    engine.on_tick(tick(70))
    engine._submit(OrderRequest(ts=tick(70).ts, symbol=SYMBOL, side=Side.SELL, qty=1), tick(70).ts)
    assert len(broker.submitted) == 4


def test_ac1_invariant_never_more_than_limit_in_any_minute() -> None:
    limit = 4
    rng = random.Random(10)
    plan = {i: rng.choice([Side.BUY, Side.SELL]) for i in range(600) if rng.random() < 0.3}
    engine, broker = engine_with(plan, cfg(max_orders_per_minute=limit))
    for s in range(600):
        engine.on_tick(tick(s))
        if engine.risk.halt == HALT_ORDER_RATE and rng.random() < 0.05:
            engine.release_kill(tick(s).ts)
    times = [r.ts for r in broker.submitted]
    assert len(times) > limit
    for i, start in enumerate(times):
        assert sum(1 for t in times[i:] if t - start < timedelta(minutes=1)) <= limit


# ---- AC2 單筆最大委託口數（問卷第 5 題）


def test_ac2_single_order_quantity_is_capped() -> None:
    rm = RiskManager(cfg().risk)
    rm.on_quote(T0)
    too_big = OrderRequest(ts=T0, symbol=SYMBOL, side=Side.BUY, qty=cfg().risk.max_order_qty + 1)
    decision = rm.check(too_big, 0, T0)
    assert not decision.allowed and "單筆" in decision.reason


# ---- AC3 停損機制（問卷第 6 題）


def test_ac3_position_stop_loss_flattens_once_without_halting() -> None:
    engine, broker = engine_with({0: Side.BUY}, cfg(position_stop_loss_twd=500.0))
    engine.on_tick(tick(0))
    engine.on_tick(tick(1))  # 27001 成交
    engine.on_tick(tick(2, 26960.0))  # 未實現 -410：還沒到
    assert len(broker.submitted) == 1
    engine.on_tick(tick(3, 26940.0))  # 未實現 -610：停損
    engine.on_tick(tick(4, 26930.0))
    engine.on_tick(tick(5, 26930.0))
    assert engine.ledger.position == 0 and len(broker.submitted) == 2
    assert kinds(engine).count("stop") == 1
    assert engine.risk.halt is None
    assert scripted(engine).fills[-1].side == Side.SELL


def test_ac3_live_mode_requires_all_three_limits() -> None:
    risk_keys = {
        "min_position": 0,
        "max_position": 1,
        "max_order_qty": 1,
        "max_entries_per_day": 6,
        "entry_start": "08:45:00",
        "entry_end": "13:00:00",
        "max_quote_age_s": 5.0,
        "force_flat_time": "13:30:00",
        "order_timeout_s": 10.0,
        "daily_loss_limit_twd": 3000.0,
    }
    extra = {
        "max_orders_per_minute": 10,
        "max_consecutive_broker_rejects": 3,
        "position_stop_loss_twd": 1500.0,
    }

    def raw(risk: dict[str, object]) -> dict[str, object]:
        return {
            "mode": "live",
            "contract": {
                "symbol": SYMBOL,
                "point_value": 10.0,
                "commission_twd": 15.0,
                "tax_rate": 0.00002,
            },
            "risk": risk,
            "session": {"start": "08:45:00", "end": "13:45:00"},
            "sim": {"latency_s": 0.5, "slippage_points": 1.0},
        }

    assert parse_config(raw({**risk_keys, **extra})).risk.position_stop_loss_twd == 1500.0
    for missing in extra:
        partial = {k: v for k, v in extra.items() if k != missing}
        with pytest.raises(ConfigError, match=missing):
            parse_config(raw({**risk_keys, **partial}))
    for bad in extra:
        with pytest.raises(ConfigError, match=bad):
            parse_config(raw({**risk_keys, **extra, bad: 0}))


# ---- AC4 斷線重連不重複送單（問卷第 7 題）


def test_ac4_no_orders_while_disconnected_and_missed_fill_is_replayed_not_resent() -> None:
    engine, broker = engine_with({0: Side.BUY, 2: Side.BUY}, cfg())
    engine.on_tick(tick(0))
    broker.down = True
    engine.on_disconnect(tick(0.5).ts)
    engine.on_tick(tick(1))  # 券商端已成交，但回報收不到
    engine.on_tick(tick(2))  # 策略又想買：連線中斷，不送
    assert len(broker.submitted) == 1 and engine.ledger.position == 0
    assert scripted(engine).rejects == ["連線中斷"]
    broker.down = False
    engine.on_reconnect(tick(3).ts)
    assert engine.ledger.position == 1 and len(broker.submitted) == 1
    assert engine.risk.halt is None and "correction" not in kinds(engine)
    engine.on_tick(tick(4))
    assert len(broker.submitted) == 1


def test_ac4_order_that_never_arrived_is_closed_locally_and_not_resent() -> None:
    engine, broker = engine_with({0: Side.BUY}, cfg(), drop_next=True)
    engine.on_tick(tick(0))
    engine.on_disconnect(tick(0.5).ts)
    engine.on_reconnect(tick(3).ts)
    assert engine.oms.working() == []
    assert scripted(engine).rejects == ["重連後券商沒有這張委託"]
    assert engine.risk.halt is None and engine.ledger.position == 0
    for s in range(4, 20):
        engine.on_tick(tick(s))
    assert len(broker.submitted) == 1


def test_ac4_unknown_order_at_broker_is_cancelled_and_halts() -> None:
    engine, broker = engine_with({}, cfg())
    engine.on_tick(tick(0))
    engine.on_disconnect(tick(1).ts)
    broker.foreign.add("Z999")
    engine.on_reconnect(tick(2).ts)
    assert broker.cancelled == ["Z999"]
    assert engine.risk.halt == HALT_MISMATCH


def test_ac4_stale_order_cancel_waits_for_reconnect() -> None:
    engine, broker = engine_with({0: Side.BUY}, cfg())
    broker.inner._latency = timedelta(hours=1)  # 這張單在測試期間不會成交
    engine.on_tick(tick(0))
    engine.on_disconnect(tick(1).ts)
    engine.on_tick(tick(15))  # 已逾時，但連線中斷：不送刪單
    assert broker.cancelled == []
    engine.on_reconnect(tick(16).ts)
    engine.on_tick(tick(17))
    assert len(broker.cancelled) == 1


# ---- AC5 被期貨商阻擋時自動暫停送單（問卷第 8 題）


def test_ac5_consecutive_broker_rejects_suspend_everything() -> None:
    engine, broker = engine_with(
        {0: Side.BUY, 2: Side.BUY, 4: Side.BUY, 6: Side.BUY},
        cfg(max_consecutive_broker_rejects=2),
        reject_all=True,
    )
    for s in range(9):
        engine.on_tick(tick(s))
    assert len(broker.submitted) == 2
    assert engine.risk.halt == HALT_BROKER_REJECT
    exit_req = OrderRequest(ts=tick(9).ts, symbol=SYMBOL, side=Side.SELL, qty=1)
    decision = engine.risk.check(exit_req, 1, tick(9).ts)
    assert not decision.allowed and "暫停送單" in decision.reason


def test_ac5_a_good_report_resets_the_reject_count() -> None:
    engine, broker = engine_with(
        {0: Side.BUY, 2: Side.BUY, 4: Side.SELL, 6: Side.BUY},
        cfg(max_consecutive_broker_rejects=2),
        reject_once=1,
    )
    for s in range(6):
        engine.on_tick(tick(s))
    broker.reject_once = 1
    for s in range(6, 9):
        engine.on_tick(tick(s))
    assert engine.risk.halt is None and len(broker.submitted) == 4


def test_ac5_suspended_engine_does_not_spam_flatten_orders() -> None:
    engine, broker = engine_with({0: Side.BUY}, cfg(max_orders_per_minute=1))
    engine.on_tick(tick(0))
    engine.on_tick(tick(1))
    engine._submit(OrderRequest(ts=tick(2).ts, symbol=SYMBOL, side=Side.SELL, qty=1), tick(2).ts)
    assert engine.risk.halt == HALT_ORDER_RATE
    before = len(engine.events)
    for s in range(3, 30):
        engine.on_tick(tick(s))
    assert len(broker.submitted) == 1 and len(engine.events) == before


# ---- AC6 部位比對與修正（問卷第 9 題）


def test_ac6_mismatch_is_corrected_to_broker_position_then_flattened(tmp_path: Path) -> None:
    config = cfg()
    engine, broker = engine_with({0: Side.BUY}, config)
    journal = Journal(tmp_path / "journal.jsonl")
    engine.sink = journal.write
    engine.on_tick(tick(0))
    broker.drift = 1  # 券商端多出 1 口（例如人工另外下了單）
    engine.on_tick(tick(1))
    assert engine.ledger.position == 2
    assert engine.risk.halt == HALT_MISMATCH
    correction = next(e for e in engine.events if e.kind == "correction")
    assert "帳上 1 口" in correction.text and "券商 2 口" in correction.text
    seen = scripted(engine).seen
    for s in range(2, 8):
        engine.on_tick(tick(s))
    assert broker.position() == 0 and engine.ledger.position == 0
    assert all(r.qty <= config.risk.max_order_qty for r in broker.submitted)
    assert scripted(engine).seen == seen  # 部位對不上之後不再問策略
    journal.close()
    restored = recover(read_journal(tmp_path / "journal.jsonl"), config, T0.date())
    assert restored.position == 0 and restored.halt == HALT_MISMATCH
    assert restored.open_orders == ()


def test_ac6_recovered_position_matches_right_after_correction(tmp_path: Path) -> None:
    config = cfg()
    engine, broker = engine_with({0: Side.BUY}, config)
    journal = Journal(tmp_path / "journal.jsonl")
    engine.sink = journal.write
    engine.on_tick(tick(0))
    broker.drift = 2
    engine.on_tick(tick(1))
    journal.close()
    restored = recover(read_journal(tmp_path / "journal.jsonl"), config, T0.date())
    assert restored.position == engine.ledger.position == 3
    assert restored.avg_price == engine.ledger.avg_price


def test_ac6_reducing_orders_pass_even_when_position_is_out_of_range() -> None:
    risk: RiskConfig = cfg().risk
    rm = RiskManager(risk)
    rm.on_quote(T0)
    sell = OrderRequest(ts=T0, symbol=SYMBOL, side=Side.SELL, qty=1)
    assert rm.check(sell, risk.max_position + 2, T0).allowed
    buy = OrderRequest(ts=T0, symbol=SYMBOL, side=Side.BUY, qty=1)
    assert rm.check(buy, risk.min_position - 2, T0).allowed
    assert not rm.check(buy, risk.max_position, T0).allowed
