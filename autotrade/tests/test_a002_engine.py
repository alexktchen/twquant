"""A-002 模擬券商、委託管理、重播行情、主引擎的驗收測試（SPEC.md §4）。"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from autotrade.broker.base import Ack, BrokerEvent, Cancelled, Filled
from autotrade.broker.sim import SimBroker
from autotrade.cli import main, run_replay
from autotrade.config import Config, load_config
from autotrade.engine import EndOfDayError, Engine
from autotrade.feed.replay import FeedError, read_taifex_day
from autotrade.model import TAIPEI, Fill, OrderRequest, Side, Tick
from autotrade.oms import OrderManager, OrderState, OrderStateError
from autotrade.risk import HALT_DAILY_LOSS, HALT_KILL, HALT_MISMATCH

CONFIG_PATH = Path(__file__).parents[1] / "configs" / "tmf_sim.toml"
SYMBOL = "TMF"
DAY = date(2026, 11, 13)
T0 = datetime(2026, 11, 13, 9, 0, 0, tzinfo=TAIPEI)


def cfg(limit: float | None = None) -> Config:
    base = load_config(CONFIG_PATH)
    return replace(base, risk=replace(base.risk, daily_loss_limit_twd=limit))


def tick(seconds: float, price: float = 27000.0) -> Tick:
    return Tick(ts=T0 + timedelta(seconds=seconds), price=price, qty=1)


def req(side: Side, seconds: float = 0.0, limit: float | None = None, qty: int = 1) -> OrderRequest:
    ts = T0 + timedelta(seconds=seconds)
    return OrderRequest(ts=ts, symbol=SYMBOL, side=side, qty=qty, limit_price=limit)


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


def run(strategy: Scripted, ticks: list[Tick], config: Config | None = None) -> Engine:
    config = config or cfg()
    engine = Engine(config, strategy, SimBroker(config.sim))
    for t in ticks:
        engine.on_tick(t)
    return engine


def kinds(engine: Engine) -> list[str]:
    return [e.kind for e in engine.events]


def test_ac1_market_order_fills_after_latency_with_slippage() -> None:
    broker = SimBroker(cfg().sim)
    order_id = broker.submit(req(Side.BUY))
    assert broker.on_tick(tick(0.4)) == []
    events = broker.on_tick(tick(0.5, 27010.0))
    assert isinstance(events[0], Ack) and isinstance(events[1], Filled)
    assert events[1].order_id == order_id and events[1].fill.price == 27011.0
    assert broker.position() == 1
    broker.submit(req(Side.SELL, seconds=1.0))
    sold = broker.on_tick(tick(2.0, 27020.0))[1]
    assert isinstance(sold, Filled) and sold.fill.price == 27019.0 and broker.position() == 0


def test_ac1_limit_order_fills_only_when_crossed_and_can_be_cancelled() -> None:
    broker = SimBroker(cfg().sim)
    order_id = broker.submit(req(Side.BUY, limit=26990.0))
    assert [type(e) for e in broker.on_tick(tick(1.0, 27000.0))] == [Ack]
    filled = broker.on_tick(tick(2.0, 26985.0))[0]
    assert isinstance(filled, Filled) and filled.fill.price == 26990.0
    other = broker.submit(req(Side.SELL, seconds=3.0, limit=27100.0))
    broker.on_tick(tick(4.0))
    broker.cancel(other)
    assert broker.on_tick(tick(5.0, 27200.0)) == [Cancelled(other, tick(5.0).ts)]
    assert order_id != other and broker.position() == 1


def test_ac2_order_state_machine_rejects_illegal_events() -> None:
    oms = OrderManager()
    oms.add("A1", req(Side.BUY, qty=2))
    one = Fill(ts=T0, symbol=SYMBOL, side=Side.BUY, qty=1, price=27000.0)
    assert oms.apply(Filled("A1", one)) == one
    assert oms.orders["A1"].state == OrderState.ACKED and oms.pending_signed_qty() == 1
    oms.apply(Filled("A1", one))
    assert oms.orders["A1"].filled_qty == 2 and oms.working() == []
    with pytest.raises(OrderStateError, match="已是 filled"):
        oms.apply(Ack("A1", T0))
    with pytest.raises(OrderStateError, match="不認得"):
        oms.apply(Ack("ZZ", T0))
    with pytest.raises(OrderStateError, match="重複"):
        oms.add("A1", req(Side.BUY))
    oms.add("A2", req(Side.BUY))
    with pytest.raises(OrderStateError, match="超過委託"):
        oms.apply(Filled("A2", replace(one, qty=2)))
    oms.add("A3", req(Side.BUY))
    with pytest.raises(OrderStateError, match="買賣別不同"):
        oms.apply(Filled("A3", replace(one, side=Side.SELL)))


def test_ac3_pending_orders_count_toward_position_limit() -> None:
    engine = run(Scripted({0: Side.BUY, 1: Side.BUY}), [tick(0), tick(0.1), tick(1.0), tick(2.0)])
    assert engine.ledger.position == 1
    assert kinds(engine).count("order") == 1 and kinds(engine).count("reject") == 1
    assert len(engine.strategy.rejects) == 1  # type: ignore[attr-defined]


def test_ac3_invariant_position_within_limits_under_random_orders() -> None:
    rng = random.Random(20261113)
    plan = {i: rng.choice([Side.BUY, Side.SELL]) for i in range(0, 3000, rng.choice([1, 2, 3]))}
    config = cfg()
    engine = Engine(config, Scripted(plan), SimBroker(config.sim))
    price = 27000.0
    for i in range(3000):
        price += rng.choice([-2.0, -1.0, 1.0, 2.0])
        engine.on_tick(tick(i * 0.3, price))
        assert config.risk.min_position <= engine.ledger.position <= config.risk.max_position
        assert engine.ledger.position == engine.broker.position()


def test_ac4_daily_loss_limit_flattens_and_blocks_entries() -> None:
    ticks = [tick(0), tick(1, 27000.0), tick(2, 26950.0), tick(3, 26950.0), tick(4), tick(5)]
    engine = run(Scripted({0: Side.BUY, 4: Side.BUY}), ticks, cfg(limit=300.0))
    assert engine.risk.halt == HALT_DAILY_LOSS
    assert engine.ledger.position == 0 and engine.broker.position() == 0
    assert kinds(engine).count("fill") == 2 and kinds(engine)[-1] == "reject"
    assert engine.finish()["fills"] == 2


def test_ac5_force_flat_time_closes_position() -> None:
    base = cfg()
    config = replace(
        base, risk=replace(base.risk, entry_end=time(9, 0, 5), force_flat_time=time(9, 0, 10))
    )
    engine = run(Scripted({0: Side.BUY}), [tick(0), tick(1), tick(9), tick(10), tick(11)], config)
    assert engine.ledger.position == 0 and engine.risk.halt is None
    assert any("強制平倉時間" in e.text for e in engine.events)


def test_ac6_stale_entry_is_cancelled() -> None:
    @dataclass
    class LimitOnce(Scripted):
        def on_tick(self, tick: Tick, position: int) -> list[OrderRequest]:
            out = super().on_tick(tick, position)
            return [replace(r, limit_price=26000.0) for r in out]

    engine = run(LimitOnce({0: Side.BUY}), [tick(0), tick(5), tick(11), tick(12)])
    order = next(iter(engine.oms.orders.values()))
    assert order.state == OrderState.CANCELLED and engine.ledger.position == 0
    assert engine.risk.halt is None and kinds(engine).count("cancel") == 1


@dataclass
class StuckExitBroker:
    """賣單永遠不成交的券商：用來測平倉單逾時。"""

    inner: SimBroker
    stuck: set[str] = field(default_factory=set)
    release: bool = False

    def submit(self, req: OrderRequest) -> str:
        order_id = self.inner.submit(req)
        if req.side == Side.SELL and not self.release:
            self.stuck.add(order_id)
        return order_id

    def cancel(self, order_id: str) -> None:
        self.stuck.discard(order_id)
        self.release = True
        self.inner.cancel(order_id)

    def on_tick(self, tick: Tick) -> list[BrokerEvent]:
        events = self.inner.on_tick(tick)
        blocked = [e for e in events if isinstance(e, Filled) and e.order_id in self.stuck]
        if blocked:
            raise AssertionError("測試券商設計錯誤：卡住的單不該成交")
        return events

    def position(self) -> int:
        return self.inner.position()

    def open_order_ids(self) -> frozenset[str]:
        return self.inner.open_order_ids()

    def resync(self) -> list[BrokerEvent]:
        return self.inner.resync()


def test_ac6_stale_exit_triggers_kill_and_market_flatten() -> None:
    config = cfg()
    far = replace(config.sim, latency_s=3600.0)  # 第一張賣單在測試期間不會成交
    broker = StuckExitBroker(SimBroker(far))
    engine = Engine(config, Scripted({}), broker)
    engine.ledger.apply(Fill(ts=T0, symbol=SYMBOL, side=Side.BUY, qty=1, price=27000.0))
    broker.inner._position = 1
    engine.on_tick(tick(0))
    engine._submit(req(Side.SELL), tick(0).ts, from_strategy=True)
    engine.on_tick(tick(11))
    assert engine.risk.halt == HALT_KILL and "cancel" in kinds(engine)
    engine.on_tick(tick(12))
    states = [o.state for o in engine.oms.orders.values()]
    assert states[0] == OrderState.CANCELLED and len(states) == 2
    assert engine.strategy.rejects == ["已刪單"]  # type: ignore[attr-defined]


def test_ac7_finish_raises_with_open_position_and_mismatch_is_corrected() -> None:
    engine = run(Scripted({0: Side.BUY}), [tick(0), tick(1)])
    with pytest.raises(EndOfDayError, match="部位 1 口"):
        engine.finish()

    class Drifting(SimBroker):
        def position(self) -> int:
            return super().position() + 1

    config = cfg()
    drifting = Engine(config, Scripted({0: Side.BUY}), Drifting(config.sim))
    drifting.on_tick(tick(0))
    # D-223（2026-11-13）：盤中對不上不再 raise，改成把帳修正為券商的數字並停手平倉（A-010 AC6）
    drifting.on_tick(tick(1))
    assert drifting.ledger.position == 2 and drifting.risk.halt == HALT_MISMATCH
    assert "correction" in kinds(drifting)


HEADER = "成交日期,商品代號,到期月份(週別),成交時間,成交價格,成交數量(B+S),近月價格,遠月價格,開盤集合競價"
ROWS = [
    "20261113,TMF   ,202611     ,084459,27000,2,-,-,*",  # 盤前，時段外
    "20261113,TMF   ,202611     ,084500,27001,2,-,-, ",
    "20261113,TMF   ,202611     ,084500,27002,4,-,-, ",
    "20261113,TMF   ,202612     ,084501,27050,2,-,-, ",  # 量較小的月份
    "20261113,TMF   ,202611/202612,084501,49,2,27001,27050, ",  # 價差單
    "20261113,MXF   ,202611     ,084501,27003,2,-,-, ",  # 別的商品
    "20261112,TMF   ,202611     ,090000,26900,2,-,-, ",  # 別的日子
    "20261113,TMF   ,202611     ,90000,27010,2,-,-, ",  # 時間少一位
]


def write_csv(path: Path, rows: list[str], encoding: str = "big5") -> Path:
    path.write_bytes(("\n".join([HEADER, *rows]) + "\n").encode(encoding))
    return path


def test_ac8_replay_feed_reads_front_contract_in_session(tmp_path: Path) -> None:
    path = write_csv(tmp_path / "Daily_2026_11_13.csv", ROWS)
    contract, ticks = read_taifex_day(path, SYMBOL, DAY, time(8, 45), time(13, 45))
    assert contract == "202611"
    assert [(t.price, t.qty) for t in ticks] == [(27001.0, 2), (27002.0, 4), (27010.0, 2)]
    assert ticks[1].ts - ticks[0].ts == timedelta(microseconds=1)
    assert ticks[2].ts == datetime(2026, 11, 13, 9, 0, 0, tzinfo=TAIPEI)


def test_ac8_replay_feed_raises_on_bad_rows(tmp_path: Path) -> None:
    short = write_csv(tmp_path / "short.csv", ["20261113,TMF,202611"])
    with pytest.raises(FeedError, match="第 2 列只有 3 欄"):
        read_taifex_day(short, SYMBOL, DAY, time(8, 45), time(13, 45))
    bad = write_csv(tmp_path / "bad.csv", ["20261113,TMF,202611,090000,abc,2"])
    with pytest.raises(FeedError, match="讀不懂"):
        read_taifex_day(bad, SYMBOL, DAY, time(8, 45), time(13, 45))
    empty = write_csv(tmp_path / "empty.csv", [ROWS[5]])
    with pytest.raises(FeedError, match="沒有 TMF"):
        read_taifex_day(empty, SYMBOL, DAY, time(8, 45), time(13, 45))


def test_ac9_same_input_gives_identical_events() -> None:
    ticks = [tick(i, 27000.0 + (i % 7) - 3) for i in range(200)]
    plan = {0: Side.BUY, 20: Side.SELL, 40: Side.BUY, 41: Side.BUY, 90: Side.SELL}
    first, second = run(Scripted(dict(plan)), ticks), run(Scripted(dict(plan)), ticks)
    assert first.events == second.events and len(first.ledger.fills) == 4
    assert first.ledger.realized_twd == second.ledger.realized_twd


def test_ac10_kill_flattens_and_survives_day_change() -> None:
    engine = run(Scripted({0: Side.BUY, 4: Side.BUY}), [tick(0), tick(1)])
    assert engine.ledger.position == 1
    engine.kill(tick(1).ts)
    for t in [tick(2), tick(3), tick(4), tick(5)]:
        engine.on_tick(t)
    assert engine.ledger.position == 0 and engine.risk.halt == HALT_KILL
    next_day = Tick(ts=T0 + timedelta(days=3), price=27000.0, qty=1)
    engine.strategy.plan[engine.strategy.seen] = Side.BUY  # type: ignore[attr-defined]
    engine.on_tick(next_day)
    assert engine.risk.halt == HALT_KILL and kinds(engine)[-1] == "reject"


def test_ac11_cli_replay_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_csv(tmp_path / "Daily_2026_11_13.csv", ROWS, encoding="utf-8-sig")
    summary = run_replay(CONFIG_PATH, path, DAY, "none")
    assert summary == {
        "day": "2026-11-13",
        "contract": "202611",
        "ticks": 3,
        "fills": 0,
        "realized_twd": 0.0,
        "costs_twd": 0.0,
        "entries": 0,
    }
    assert main(["check-config", str(CONFIG_PATH)]) == 0
    assert "設定沒問題" in capsys.readouterr().out
