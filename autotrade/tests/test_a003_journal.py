"""A-003 事件日誌與狀態還原的驗收測試（SPEC.md §4）。"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from autotrade.broker.sim import SimBroker
from autotrade.cli import main
from autotrade.config import Config, load_config
from autotrade.engine import Engine, RecoveryError
from autotrade.journal import Journal, JournalError, read_journal, recover
from autotrade.ledger import ReconcileError
from autotrade.model import TAIPEI, Fill, OrderRequest, Side, Tick
from autotrade.risk import HALT_DAILY_LOSS, HALT_KILL

CONFIG_PATH = Path(__file__).parents[1] / "configs" / "tmf_sim.toml"
SYMBOL = "TMF"
T0 = datetime(2026, 11, 13, 9, 0, 0, tzinfo=TAIPEI)


def cfg(limit: float | None = None) -> Config:
    base = load_config(CONFIG_PATH)
    return replace(
        base,
        risk=replace(
            base.risk,
            daily_loss_limit_twd=limit,
            max_entries_per_day=10_000,
            max_orders_per_minute=None,  # 這裡每 1.2 秒送一筆來壓日誌還原；頻率上限另有 A-010 AC1 測
        ),
    )


def tick(seconds: float, price: float = 27000.0, days: int = 0) -> Tick:
    return Tick(ts=T0 + timedelta(days=days, seconds=seconds), price=price, qty=1)


@dataclass
class Scripted:
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


def test_ac1_append_read_and_reopen(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    log = Journal(path)
    first = log.append(T0, "note", {"text": "開始"})
    second = log.append(T0 + timedelta(seconds=1), "note", {"n": 2})
    log.close()
    assert second.prev == first.hash and read_journal(path) == [first, second]
    again = Journal(path)
    third = again.append(T0 + timedelta(seconds=2), "note", {"n": 3})
    again.close()
    assert third.seq == 3 and third.prev == second.hash
    assert [r.seq for r in read_journal(path)] == [1, 2, 3]


def test_ac2_tampering_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    log = Journal(path)
    for n in range(3):
        log.append(T0 + timedelta(seconds=n), "fill", {"price": 27000.0 + n})
    log.close()
    lines = path.read_text(encoding="utf-8").splitlines()

    edited = tmp_path / "edited.jsonl"
    edited.write_text(
        "\n".join([lines[0], lines[1].replace("27001.0", "26001.0"), lines[2]]) + "\n"
    )
    with pytest.raises(JournalError, match="第 2 列的雜湊不對"):
        read_journal(edited)

    dropped = tmp_path / "dropped.jsonl"
    dropped.write_text("\n".join([lines[0], lines[2]]) + "\n")
    with pytest.raises(JournalError, match="第 2 列接不上"):
        read_journal(dropped)

    torn = tmp_path / "torn.jsonl"
    torn.write_text("\n".join([lines[0], lines[1][:40]]) + "\n")
    with pytest.raises(JournalError, match="第 2 列讀不懂"):
        read_journal(torn)
    with pytest.raises(JournalError, match="讀不懂"):
        Journal(torn)


def test_ac3_naive_time_raises(tmp_path: Path) -> None:
    log = Journal(tmp_path / "journal.jsonl")
    with pytest.raises(ValueError, match="沒有時區"):
        log.append(datetime(2026, 11, 13, 9, 0, 0), "note", {})
    log.close()


def test_ac4_recovered_state_matches_live_engine_at_any_point(tmp_path: Path) -> None:
    rng = random.Random(20261114)
    config = cfg(limit=5000.0)
    path = tmp_path / "journal.jsonl"
    log = Journal(path)
    plan = {i: rng.choice([Side.BUY, Side.SELL]) for i in range(0, 1500, 3)}
    engine = Engine(config, Scripted(plan), SimBroker(config.sim), sink=log.write)
    price = 27000.0
    for i in range(1500):
        price += rng.choice([-3.0, -1.0, 1.0, 2.0])
        t = tick(i * 0.4, price, days=i // 500)
        engine.on_tick(t)
        if i % 97 == 0 or i == 1499:
            state = recover(read_journal(path), config, t.ts.date())
            assert state.position == engine.ledger.position == engine.broker.position()
            assert state.avg_price == engine.ledger.avg_price
            assert state.realized_twd == engine.ledger.realized_twd
            assert state.costs_twd == engine.ledger.costs_twd
            assert state.entries_today == engine.risk.entries_today
            assert state.halt == engine.risk.halt
            assert state.day_start_realized == engine._day_start_realized
            assert state.open_orders == tuple(sorted(o.order_id for o in engine.oms.working()))
    log.close()
    assert len(engine.ledger.fills) > 100


def test_ac5_restore_takes_over_and_refuses_unsafe_states(tmp_path: Path) -> None:
    config = cfg()
    path = tmp_path / "journal.jsonl"
    log = Journal(path)
    first = Engine(config, Scripted({0: Side.BUY}), SimBroker(config.sim), sink=log.write)
    first.on_tick(tick(0))
    pending = recover(read_journal(path), config, T0.date())
    assert pending.open_orders == ("S000001",)
    with pytest.raises(RecoveryError, match="結果不明的委託"):
        Engine(config, Scripted({}), SimBroker(config.sim)).restore(pending)
    first.on_tick(tick(1))
    log.close()

    state = recover(read_journal(path), config, T0.date())
    assert state.position == 1 and state.open_orders == () and state.entries_today == 1
    with pytest.raises(ReconcileError, match=r"帳上 1 口.*券商回報 0 口"):
        Engine(config, Scripted({}), SimBroker(config.sim)).restore(state)

    broker = SimBroker(config.sim)
    broker._position = 1  # 券商端確實有 1 口
    second = Engine(config, Scripted({1: Side.SELL}), broker)
    second.restore(state)
    for t in [tick(2), tick(3, 27050.0), tick(4, 27050.0)]:
        second.on_tick(t)
    assert second.ledger.position == 0
    exit_cost = config.contract.commission_twd + 5  # 手續費＋期交稅 round(27049×10×0.00002)＝5
    expected = first.ledger.realized_twd + (27049.0 - 27001.0) * 10 - exit_cost
    assert second.finish()["realized_twd"] == expected
    with pytest.raises(RecoveryError, match="已經開始運作"):
        second.restore(state)


def test_ac6_kill_survives_restart_and_daily_loss_halt_does_not_carry_over(tmp_path: Path) -> None:
    config = cfg(limit=300.0)
    path = tmp_path / "journal.jsonl"
    log = Journal(path)
    engine = Engine(config, Scripted({0: Side.BUY}), SimBroker(config.sim), sink=log.write)
    for t in [tick(0), tick(1), tick(2, 26950.0), tick(3, 26950.0)]:
        engine.on_tick(t)
    assert engine.risk.halt == HALT_DAILY_LOSS
    same_day = recover(read_journal(path), config, T0.date())
    next_day = recover(read_journal(path), config, T0.date() + timedelta(days=1))
    assert same_day.halt == HALT_DAILY_LOSS and next_day.halt is None
    assert next_day.entries_today == 0 and next_day.day_start_realized == engine.ledger.realized_twd

    engine.kill(tick(4).ts)
    assert recover(read_journal(path), config, T0.date() + timedelta(days=5)).halt == HALT_KILL
    engine.release_kill(tick(5).ts)
    log.close()
    assert recover(read_journal(path), config, T0.date()).halt is None
    with pytest.raises(JournalError, match="比今天"):
        recover(read_journal(path), config, T0.date() - timedelta(days=1))


def test_ac7_cli_writes_and_verifies_journal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "journal.jsonl"
    log = Journal(path)
    log.append(T0, "note", {"text": "測試"})
    log.close()
    assert main(["verify-journal", str(path)]) == 0
    assert "日誌沒問題：1 筆" in capsys.readouterr().out
