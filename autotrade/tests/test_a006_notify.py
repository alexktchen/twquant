"""A-006：成交與異常通知手機（SPEC.md §4）。"""

from __future__ import annotations

import ast
import email.message
import io
import json
import random
import re
import socket
import threading
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from autotrade import cli, notify, risk
from autotrade import engine as engine_module
from autotrade.broker.base import BrokerEvent, Cancelled
from autotrade.broker.sim import SimBroker
from autotrade.cli import main
from autotrade.config import (
    NOTIFY_KINDS,
    NOTIFY_LIVE_REQUIRED,
    Config,
    ConfigError,
    ExportConfig,
    load_config,
    parse_config,
)
from autotrade.delivery import (
    ENV_CHAT,
    ENV_TOKEN,
    Failure,
    MemorySender,
    Outbox,
    Outgoing,
    PrintSender,
    Sender,
    SendError,
    TelegramSender,
    http_post,
    telegram_from_env,
    telegram_tell_chat_ids,
)
from autotrade.engine import Engine, Event
from autotrade.export import snapshot
from autotrade.journal import Journal, read_journal, recover
from autotrade.model import TAIPEI, Command, OrderRequest, Side, Tick
from autotrade.notify import HALT_SHORT, Book, Level, Message, Notifier, Throttle, plain
from test_a002_engine import write_csv
from test_a010_broker_requirements import FlakyBroker, Scripted, scripted

CONFIG_PATH = Path(__file__).parents[1] / "configs" / "tmf_sim.toml"
T0 = datetime(2026, 11, 13, 9, 0, 0, tzinfo=TAIPEI)
MANY = 10_000
TOKEN = "12345:fake-token-for-tests-only"  # 假的，而且故意不像真的金鑰（真的更長）
CHAT = "987654321"
WHERE = "【模擬】微台"
PLAIN = WHERE + "｜"
WARN = "⚠️ 注意" + PLAIN
ACT = "🚨 要處理" + PLAIN
ALL: dict[str, object] = {"kinds": NOTIFY_KINDS}
Overrides = dict[str, object] | None


def cfg(notify_over: Overrides = None, **risk_over: object) -> Config:
    base = load_config(CONFIG_PATH)
    assert base.notify is not None
    risk_defaults: dict[str, object] = {
        "max_entries_per_day": MANY,
        "max_orders_per_minute": None,
        "max_consecutive_broker_rejects": None,
        "position_stop_loss_twd": None,
    }
    fast: dict[str, object] = {"retry_wait_s": 0.0, "close_wait_s": 30.0}
    return replace(
        base,
        risk=replace(base.risk, **{**risk_defaults, **risk_over}),  # type: ignore[arg-type]
        notify=replace(base.notify, **{**fast, **(notify_over or {})}),  # type: ignore[arg-type]
        export=ExportConfig(max_events=30),
    )


def at(seconds: float, days: int = 0) -> datetime:
    return T0 + timedelta(days=days, seconds=seconds)


def tick(seconds: float, price: float = 27000.0, days: int = 0) -> Tick:
    return Tick(ts=at(seconds, days), price=price, qty=1)


def ev(seconds: float, kind: str, text: str, **data: Any) -> Event:
    return Event(at(seconds), kind, text, data)


def item(n: int, *, silent: bool = True) -> Outgoing:
    return Outgoing(ts=at(n), title=f"第 {n} 則", text=f"第 {n} 則\n內容", silent=silent)


def down() -> SendError:
    return SendError("連不上", retryable=True)


class Script:
    """照劇本成功或失敗的假送出端：劇本用完就都成功。"""

    def __init__(self, *errors: BaseException | None) -> None:
        self.errors = list(errors)
        self.sent: list[tuple[str, bool]] = []

    def send(self, text: str, *, silent: bool) -> None:
        error = self.errors.pop(0) if self.errors else None
        if error is not None:
            raise error
        self.sent.append((text, silent))


class Failing:
    """每次都失敗的假送出端。"""

    def __init__(self, error: BaseException) -> None:
        self.error = error

    def send(self, text: str, *, silent: bool) -> None:
        raise self.error


class Stuck:
    """送出時卡住不回來的假送出端，放行後才成功。"""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.sent: list[str] = []

    def send(self, text: str, *, silent: bool) -> None:
        self.entered.set()
        if not self.release.wait(timeout=60):
            raise AssertionError("測試沒有放行卡住的送出")
        self.sent.append(text)


def outbox_for(
    sender: Sender, *, queue_size: int = 200, waits: list[float] | None = None
) -> Outbox:
    """不開背景執行緒的佇列：測試自己呼叫 drain；等待不真的睡，只記下要等幾秒。"""

    def wait(seconds: float) -> bool:
        if waits is not None:
            waits.append(seconds)
        return False

    return Outbox(sender, warn_after_attempts=3, retry_wait_s=2.0, queue_size=queue_size, wait=wait)


@dataclass
class Rig:
    """一個接好通知的測試引擎。"""

    engine: Engine
    broker: Any
    notifier: Notifier
    outbox: Outbox
    sender: MemorySender

    def run(self, ticks: list[Tick]) -> None:
        for t in ticks:
            self.engine.on_tick(t)
            self.notifier.poll(t.ts)

    def done(self) -> list[str]:
        self.notifier.close()
        return [text for text, _ in self.sender.sent]

    def silent(self, text: str) -> bool:
        (flag,) = [silent for sent, silent in self.sender.sent if sent == text]
        return flag


def rig_with(strategy: Scripted, broker: Any, config: Config) -> Rig:
    assert config.notify is not None
    sender = MemorySender()
    outbox = Outbox.from_config(sender, config.notify, wait=lambda seconds: False)
    notifier = Notifier(config, outbox)
    engine = Engine(config, strategy, broker, sink=notifier.on_event)
    return Rig(engine, broker, notifier, outbox, sender)


def rig(plan: dict[int, Side], config: Config | None = None, **broker: object) -> Rig:
    config = config or cfg()
    flaky = FlakyBroker(SimBroker(config.sim), **broker)  # type: ignore[arg-type]
    return rig_with(Scripted(plan), flaky, config)


def find(texts: list[str], needle: str) -> str:
    hits = [t for t in texts if needle in t]
    assert len(hits) == 1, f"要剛好一則含「{needle}」，找到 {len(hits)} 則：{texts}"
    return hits[0]


# ---- 情境：每個都回傳跑完、還沒結束通知的 Rig


def round_trip(over: Overrides = None) -> Rig:
    """買 1 口（27001 成交）→ 漲到 27050 → 賣掉（27049 成交）。"""
    r = rig({0: Side.BUY, 3: Side.SELL}, cfg(over))
    r.run(
        [tick(i, p) for i, p in enumerate([27000.0, 27000.0, 27050.0, 27050.0, 27050.0, 27050.0])]
    )
    return r


def daily_loss(over: Overrides = None) -> Rig:
    """買 1 口後跌 51 點：碰到每日虧損上限 300 元，系統平倉；之後再想買被風控擋下。"""
    r = rig({0: Side.BUY, 4: Side.BUY}, cfg(over, daily_loss_limit_twd=300.0))
    r.run([tick(0), tick(1), tick(2, 26950.0), tick(3, 26950.0), tick(4), tick(5)])
    return r


def killed_then_released(over: Overrides = None) -> Rig:
    r = rig({0: Side.BUY}, cfg(over))
    r.run([tick(0), tick(1)])
    r.engine.kill(at(2))
    r.run([tick(3), tick(4)])
    r.engine.release_kill(at(5))
    return r


def mismatch(over: Overrides = None) -> Rig:
    """券商端多出 1 口：帳改成券商的 2 口、停手、分兩次平倉。"""
    r = rig({0: Side.BUY}, cfg(over))
    r.run([tick(0)])
    r.broker.drift = 1
    r.run([tick(s) for s in range(1, 8)])
    return r


def broker_rejects(over: Overrides = None) -> Rig:
    """券商每筆都拒絕：連續 2 筆就暫停送單，之後再想買被擋下兩次。"""
    plan = {0: Side.BUY, 2: Side.BUY, 4: Side.BUY, 6: Side.BUY}
    r = rig(plan, cfg(over, max_consecutive_broker_rejects=2), reject_all=True)
    r.run([tick(s) for s in range(9)])
    return r


def order_rate(over: Overrides = None) -> Rig:
    """一分鐘內第 4 筆委託（平倉單）碰到上限 3 筆：暫停送單，留著 1 口。"""
    plan = {0: Side.BUY, 1: Side.SELL, 2: Side.BUY, 3: Side.SELL}
    r = rig(plan, cfg(over, max_orders_per_minute=3))
    r.run([tick(s) for s in range(6)])
    return r


def disconnected_storm(n: int, over: Overrides = None) -> Rig:
    """斷線後策略每秒都想買：第 1～n-1 秒各被擋一次。"""
    r = rig({i: Side.BUY for i in range(1, n)}, cfg(over))
    r.run([tick(0)])
    r.engine.on_disconnect(at(0.5))
    r.run([tick(s) for s in range(1, n)])
    return r


def flapping(over: Overrides = None) -> Rig:
    """連線斷、通、斷、通、斷。"""
    r = rig({}, cfg(over))
    r.run([tick(0)])
    for s in range(1, 6):
        if s % 2:
            r.engine.on_disconnect(at(s))
        else:
            r.engine.on_reconnect(at(s))
    return r


def foreign(over: Overrides = None) -> Rig:
    r = rig({}, cfg(over))
    r.run([tick(0)])
    r.engine.on_disconnect(at(1))
    r.broker.foreign.add("Z999")
    r.engine.on_reconnect(at(2))
    return r


def stopped_out(over: Overrides = None) -> Rig:
    r = rig({0: Side.BUY}, cfg(over, position_stop_loss_twd=500.0))
    r.run(
        [tick(0), tick(1), tick(2, 26960.0), tick(3, 26940.0), tick(4, 26930.0), tick(5, 26930.0)]
    )
    return r


def exit_rejected(over: Overrides = None) -> Rig:
    """有 1 口時，平倉單被券商拒絕。"""
    r = rig({0: Side.BUY, 2: Side.SELL}, cfg(over))
    r.run([tick(0), tick(1)])
    r.broker.reject_once = 1
    r.run([tick(2), tick(3)])
    return r


def lost_on_reconnect(over: Overrides = None) -> Rig:
    """委託送出後斷線，重連時券商沒有這張委託。"""
    r = rig({0: Side.BUY}, cfg(over), drop_next=True)
    r.run([tick(0)])
    r.engine.on_disconnect(at(0.5))
    r.engine.on_reconnect(at(3))
    return r


@dataclass
class LimitBuyer(Scripted):
    """掛一張不會成交的限價買單。"""

    def on_tick(self, tick: Tick, position: int) -> list[OrderRequest]:
        return [replace(r, limit_price=26000.0) for r in super().on_tick(tick, position)]


def stale_entry(over: Overrides = None) -> Rig:
    config = cfg(over)
    r = rig_with(LimitBuyer({0: Side.BUY}), FlakyBroker(SimBroker(config.sim)), config)
    r.run([tick(0), tick(5), tick(11), tick(12)])
    return r


@dataclass
class HoldExitBroker:
    """賣單送出後一直沒結果的測試券商；刪掉那張之後的賣單才正常成交。用來測平倉單逾時。"""

    inner: SimBroker
    held: dict[str, OrderRequest] = field(default_factory=dict)
    cancels: list[str] = field(default_factory=list)
    released: bool = False

    def submit(self, req: OrderRequest) -> str:
        if req.side == Side.SELL and not self.released:
            order_id = f"H{len(self.held) + 1:06d}"
            self.held[order_id] = req
            return order_id
        return self.inner.submit(req)

    def cancel(self, order_id: str) -> None:
        if order_id in self.held:
            self.cancels.append(order_id)
        else:
            self.inner.cancel(order_id)

    def on_tick(self, tick: Tick) -> list[BrokerEvent]:
        events: list[BrokerEvent] = [Cancelled(order_id, tick.ts) for order_id in self.cancels]
        for order_id in self.cancels:
            del self.held[order_id]
            self.released = True
        self.cancels.clear()
        return events + self.inner.on_tick(tick)

    def position(self) -> int:
        return self.inner.position()

    def open_order_ids(self) -> frozenset[str]:
        return self.inner.open_order_ids() | frozenset(self.held)

    def resync(self) -> list[BrokerEvent]:
        return []


def stale_exit(over: Overrides = None) -> Rig:
    """平倉單 11 秒沒成交：刪單、緊急停止，刪單回報後重送市價平倉。"""
    config = cfg(over)
    r = rig_with(
        Scripted({0: Side.BUY, 2: Side.SELL}), HoldExitBroker(SimBroker(config.sim)), config
    )
    r.run([tick(0), tick(1), tick(2), tick(13), tick(14), tick(15)])
    return r


def kill_while_suspended(over: Overrides = None) -> Rig:
    r = order_rate(over)
    r.engine.kill(at(10))
    return r


def release_while_daily_loss(over: Overrides = None) -> Rig:
    r = daily_loss(over)
    r.engine.release_kill(at(10))
    return r


def remote_stop(over: Overrides = None) -> Rig:
    """有 1 口時從網站按了緊急停止（A-011）；之後遠端停止這條路斷了又恢復；還有一筆不認得的遠端指令。"""
    r = rig({0: Side.BUY}, cfg(over))
    r.run([tick(0), tick(1)])
    day = at(0).date()
    r.engine.on_control([Command(id=7, action="stop", day=day, created_at=at(1))], at(2))
    r.run([tick(3), tick(4)])
    r.engine.note_control_link(at(5), up=False, detail="讀不到遠端指令：還沒有指令檔 commands.json")
    r.engine.note_control_link(at(9), up=True, detail="遠端指令恢復可以讀")
    # D-229 之後 resume 是認得的動作；這裡要的是「不認得的」，換成 release
    r.engine.on_control([Command(id=8, action="release", day=day, created_at=at(9))], at(10))
    return r


SCENARIOS: list[Callable[[Overrides], Rig]] = [
    round_trip,
    daily_loss,
    killed_then_released,
    mismatch,
    broker_rejects,
    order_rate,
    lambda over: disconnected_storm(8, over),
    flapping,
    foreign,
    stopped_out,
    exit_rejected,
    lost_on_reconnect,
    stale_entry,
    stale_exit,
    kill_while_suspended,
    remote_stop,
    release_while_daily_loss,
]


# ---- AC1 成交通知


def test_ac1_fill_messages_say_side_price_position_and_pnl() -> None:
    r = round_trip()
    assert r.done() == [
        "【模擬】微台｜買進 1 口，成交 27,001\n"
        "時間 11/13 09:00:01\n"
        "帳上部位：多 1 口（均價 27,001）\n"
        "這筆的成本 20 元",
        "【模擬】微台｜賣出 1 口，成交 27,049｜這一趟賺 440 元\n"
        "時間 11/13 09:00:04\n"
        "帳上部位：空手\n"
        "這一趟＝這一筆 +460 元＋這一趟前面的 -20 元（成本都扣了）\n"
        "今天已實現 +440 元（已扣成本）",
    ]
    assert [silent for _, silent in r.sender.sent] == [True, True]  # 一般通知靜音送達


def test_ac1_losing_trip_shows_loss_and_day_total() -> None:
    sell = find(daily_loss().done(), "賣出 1 口")
    assert "成交 26,949｜這一趟賠 560 元" in sell
    assert "這一趟＝這一筆 -540 元＋這一趟前面的 -20 元（成本都扣了）" in sell
    assert "今天已實現 -560 元（已扣成本）" in sell


def test_ac1_partial_exit_reports_the_fill_and_the_flat_exit_reports_the_whole_trip() -> None:
    plan = {0: Side.BUY, 2: Side.BUY, 4: Side.SELL, 6: Side.SELL}
    r = rig(plan, cfg(max_position=2))
    r.run([tick(s) for s in range(9)])
    first, second = [t for t in r.done() if "賣出 1 口" in t]
    assert (
        "成交 26,999｜這筆賠 40 元" in first and "這一趟" not in first
    )  # 還有 1 口，這一趟還沒結束
    assert "帳上部位：多 1 口（均價 27,001）" in first and "今天已實現 -80 元" in first
    assert "成交 26,999｜這一趟賠 120 元" in second and "帳上部位：空手" in second
    assert "這一趟＝這一筆 -40 元＋這一趟前面的 -80 元（成本都扣了）" in second
    assert "今天已實現 -120 元（已扣成本）" in second
    assert r.engine.ledger.realized_twd == -120.0


def test_ac1_fill_amounts_match_website_snapshot_and_sum_to_day_realized() -> None:
    rng = random.Random(20261113)
    r = rig({i: rng.choice([Side.BUY, Side.SELL]) for i in range(0, 600, 3)})
    price = 27000.0
    for i in range(600):
        price += rng.choice([-3.0, -1.0, 1.0, 2.0])
        r.run([tick(i * 0.4, price)])
    book = Book(r.engine.cfg.contract)
    per_fill: list[float] = []
    for event in r.engine.events:
        seen = book.apply(event)
        if event.kind == "fill":
            assert seen.pnl_twd is not None
            per_fill.append(seen.pnl_twd)
    snap = snapshot(r.engine, at(600), source="replay", status="live", strategy="scripted")
    fills = snap["fills"]
    assert isinstance(fills, list) and len(fills) > 50
    assert per_fill == [f["pnl_twd"] for f in fills]
    assert sum(per_fill) == pytest.approx(snap["realized_twd"])
    assert book.day_realized == pytest.approx(snap["realized_twd"])
    assert sum("，成交 " in t for t in r.done()) == len(fills)


def test_ac1_fills_are_never_throttled_or_merged() -> None:
    plan = {i: (Side.BUY if i % 4 == 0 else Side.SELL) for i in range(0, 80, 2)}
    r = rig(plan, cfg({"burst": 1, "throttle_s": 3600.0}))
    r.run([tick(i) for i in range(82)])
    assert len([e for e in r.engine.events if e.kind == "fill"]) == 40
    texts = r.done()
    assert sum("，成交 " in t for t in texts) == 40
    assert not any("合併" in t for t in texts)


def test_ac1_book_follows_the_engine_ledger_through_days_and_corrections() -> None:
    rng = random.Random(20261115)
    r = rig({i: rng.choice([Side.BUY, Side.SELL]) for i in range(0, 1500, 3)})
    forward = r.engine.sink
    assert forward is not None
    checked = 0

    def checking(event: Event) -> None:
        nonlocal checked
        forward(event)
        if event.kind in ("fill", "correction"):
            book, ledger = r.notifier.book, r.engine.ledger
            assert book.ledger.position == ledger.position
            assert book.ledger.avg_price == ledger.avg_price
            assert book.ledger.realized_twd == ledger.realized_twd
            assert book.day_realized == ledger.realized_twd - r.engine._day_start_realized
            checked += 1

    r.engine.sink = checking
    price = 27000.0
    for i in range(1500):
        price += rng.choice([-3.0, -1.0, 1.0, 2.0])
        if i == 1300:
            r.broker.drift = 1  # 第三天：券商端多出 1 口，下一筆成交後帳會被修正
        r.engine.on_tick(tick(i * 0.4, price, days=i // 500))
    kinds = [e.kind for e in r.engine.events]
    assert checked > 100 and "correction" in kinds
    assert len({e.ts.date() for e in r.engine.events if e.kind == "fill"}) == 3


def test_ac1_corrected_day_marks_fill_pnl_as_estimate() -> None:
    texts = mismatch().done()
    sells = [t for t in texts if "賣出 1 口" in t]
    assert len(sells) == 2 and all("這筆損益是估算" in t for t in sells)
    assert "這筆賠 30 元" in sells[0] and "帳上部位：多 1 口（均價 27,000）" in sells[0]
    assert "帳上部位：空手" in sells[1] and "今天已實現 -80 元" in sells[1]
    assert "估算" not in find(texts, "買進 1 口")


# ---- AC2 異常通知與分級


def test_ac2_daily_loss_halt_is_a_warning_that_needs_no_action() -> None:
    r = daily_loss()
    texts = r.done()
    halt = find(texts, "停手：今天虧損到上限")
    assert halt.startswith(WARN)
    assert "當日損益 -530 元（上限 300 元）" in halt and "不用處理" in halt
    blocked = find(texts, "風控擋下：買進 1 口")
    assert blocked.startswith(PLAIN)
    assert "原因：停手中（當日虧損到上限），只接受平倉" in blocked
    assert r.silent(halt) is False and r.silent(blocked) is True


def test_ac2_kill_needs_a_human_and_release_is_routine() -> None:
    r = killed_then_released()
    texts = r.done()
    kill = find(texts, "緊急停止")
    assert kill.startswith(ACT + "緊急停止\n")
    assert "帳上部位：多 1 口（均價 27,001）" in kill and "要在電腦上解除" in kill
    release = find(texts, "已解除停手")
    assert release.startswith(PLAIN) and "帳上部位：空手" in release
    assert r.silent(kill) is False and r.silent(release) is True


def test_ac2_stale_exit_order_becomes_an_urgent_kill() -> None:
    halt = find(stale_exit().done(), "緊急停止：平倉單一直沒成交")
    assert halt.startswith(ACT)
    assert "超過 10 秒沒成交" in halt and "帳上部位：多 1 口（均價 27,001）" in halt
    assert "元大 App" in halt


def test_ac2_mismatch_is_corrected_and_needs_a_human() -> None:
    texts = mismatch().done()
    fix = find(texts, "部位對不上：帳上多 1 口、券商多 2 口")
    halt = find(texts, "停手：部位跟券商對不上")
    assert fix.startswith(ACT) and halt.startswith(ACT)
    assert "已改用券商的數字" in fix and "元大 App" in fix
    assert "帳上部位：多 2 口（均價 27,000）" in halt and "要在電腦上解除" in halt


def test_ac2_suspension_says_whether_a_position_is_left() -> None:
    rate = find(order_rate().done(), "暫停送單：送單太頻繁")
    assert rate.startswith(ACT)
    assert "達到上限（3 筆）" in rate and "連平倉單都不送" in rate
    assert "帳上還有部位：多 1 口（均價 27,001）" in rate and "元大 App" in rate
    rejects = find(broker_rejects().done(), "暫停送單：連續被券商拒絕")
    assert rejects.startswith(ACT)
    assert "連續 2 筆委託被券商拒絕" in rejects and "帳上空手" in rejects


def test_ac2_broker_reject_of_entry_warns_and_of_exit_needs_action() -> None:
    entry = [t for t in broker_rejects().done() if "委託被券商拒絕：買進 1 口" in t]
    assert len(entry) == 2 and all(t.startswith(WARN) for t in entry)
    assert "券商的說明：測試：券商拒絕" in entry[0] and "部位不變" in entry[0]
    assert "連續被拒絕 2 筆，程式就會暫停送單" in entry[0] and "委託編號 X000001" in entry[0]
    leaving = find(exit_rejected().done(), "委託被券商拒絕：賣出 1 口")
    assert leaving.startswith(ACT)
    assert "這是平倉單，部位還在：多 1 口（均價 27,001）" in leaving and "元大 App" in leaving
    assert "就會暫停送單" not in leaving  # 這個情境沒設連續被拒的上限


def test_ac2_kill_while_suspended_says_it_did_nothing() -> None:
    text = find(kill_while_suspended().done(), "緊急停止沒有作用")
    assert text.startswith(ACT + "緊急停止沒有作用：程式在暫停送單中\n")
    assert "暫停的原因：送單太頻繁" in text and "帳上還有部位：多 1 口" in text


def test_ac2_release_that_did_not_clear_the_halt_says_so() -> None:
    text = find(release_while_daily_loss().done(), "解除沒有生效")
    assert text.startswith(WARN)
    assert "原因：當日虧損到上限" in text and "明天自動恢復" in text


def test_ac2_stop_loss_is_a_warning() -> None:
    text = find(stopped_out().done(), "停損：程式正在平倉")
    assert text.startswith(WARN)
    assert "未實現損益 -610 元，達到停損金額 500 元" in text and "不用處理" in text


def test_ac2_exit_order_that_cannot_go_out_needs_a_human() -> None:
    # 暫停送單擋下的是平倉單，而且沒有別張平倉單在途：部位還在
    suspended = find(order_rate().done(), "風控擋下：賣出 1 口")
    assert suspended.startswith(ACT)
    assert (
        "這是平倉方向的單，部位還在：多 1 口（均價 27,001）" in suspended
        and "元大 App" in suspended
    )
    # 有部位時斷線，策略想賣卻送不出去
    r = rig({0: Side.BUY, 2: Side.SELL})
    r.run([tick(0), tick(1)])
    r.engine.on_disconnect(at(1.5))
    r.run([tick(2)])
    cut_off = find(r.done(), "連線中斷，沒有送出：賣出 1 口")
    assert cut_off.startswith(ACT) and "這是平倉方向的單，部位還在：多 1 口" in cut_off
    assert r.silent(cut_off) is False
    # 空手時被擋下的買單只是一般通知（daily_loss 情境已經檢查過），不會跟上面的合併在一起
    blocked = find(daily_loss().done(), "風控擋下：買進 1 口")
    assert blocked.startswith(PLAIN) and "部位還在" not in blocked


def test_ac2_link_down_is_urgent_only_when_something_is_at_risk() -> None:
    flat = find(foreign().done(), "券商連線中斷")
    assert flat.startswith(WARN) and "帳上空手" in flat
    r = rig({0: Side.BUY})
    r.run([tick(0), tick(1)])
    r.engine.on_disconnect(at(2))
    r.engine.on_reconnect(at(3))
    texts = r.done()
    holding = find(texts, "券商連線中斷")
    assert holding.startswith(ACT) and "元大 App" in holding
    assert "帳上還有部位：多 1 口（均價 27,001）" in holding
    assert find(texts, "券商連線恢復").startswith(PLAIN)
    pending = find(lost_on_reconnect().done(), "券商連線中斷")
    assert pending.startswith(ACT) and "還有 1 張委託沒有結果" in pending


def test_ac2_foreign_order_needs_a_human() -> None:
    texts = foreign().done()
    alien = find(texts, "｜券商那邊有不是程式送的委託")
    assert alien.startswith(ACT) and "委託編號 Z999" in alien and "已送出刪單" in alien
    assert find(texts, "停手：券商那邊有不是程式送的委託").startswith(ACT)


def test_ac2_cancels_are_notified_and_a_timeout_is_a_warning() -> None:
    texts = stale_entry().done()
    assert len(texts) == 2  # 預設不通知「送出委託」
    late = find(texts, "送出刪單：買進 1 口")
    assert late.startswith(WARN) and "原因：超過 10 秒沒成交" in late
    assert "這是新倉單，沒成交的部分不會進場" in late and "委託編號 S000001" in late
    assert find(texts, "刪單完成：買進 1 口").startswith(PLAIN)
    leaving = find(stale_exit().done(), "送出刪單：賣出 1 口")
    assert leaving.startswith(WARN) and "這是平倉單，部位還在" in leaving
    lost = find(lost_on_reconnect().done(), "斷線前送出的委託沒有送到券商：買進 1 口")
    assert lost.startswith(WARN) and "不會自動重送" in lost


def test_ac2_sent_orders_have_a_plain_template_but_are_off_by_default() -> None:
    assert not any("送出委託" in t for t in round_trip().done())
    market = find(round_trip(ALL).done(), "送出委託：買進 1 口（市價）")
    assert market.startswith(PLAIN) and "新倉單" in market and "委託編號 S000001" in market
    limit = find(stale_entry(ALL).done(), "送出委託：買進 1 口（限價 26,000）")
    assert limit.startswith(PLAIN)


def test_ac2_order_reason_is_shown_only_when_it_is_plain() -> None:
    def one(text: str) -> str:
        sender = MemorySender()
        notifier = Notifier(cfg(ALL), outbox_for(sender))
        data = {
            "order_id": "S000009",
            "side": "SELL",
            "qty": 1,
            "limit_price": None,
            "entry": False,
        }
        notifier.on_event(ev(1, "order", text, **data))
        notifier.close()
        return sender.sent[0][0]

    system = one("S000009 SELL 1 口 停手（kill）")
    assert "平倉單" in system and "原因：停手（緊急停止）" in system
    strategy = one("S000009 SELL 1 口 pullback_a 結構停損 26950")
    assert "原因" not in strategy and "pullback" not in strategy


def test_ac2_only_configured_kinds_are_sent() -> None:
    assert not any("送出委託" in t for t in daily_loss().done())
    only_fills = daily_loss({"kinds": ("fill",)}).done()
    assert len(only_fills) == 2 and all("，成交 " in t for t in only_fills)


def test_ac2_routine_messages_ring_when_quiet_is_off() -> None:
    r = round_trip({"quiet_routine": False})
    r.done()
    assert [silent for _, silent in r.sender.sent] == [False, False]


# ---- AC3 白話


def test_ac3_halt_table_covers_every_risk_code() -> None:
    codes = {v for k, v in vars(risk).items() if k.startswith("HALT_")}
    assert codes == set(HALT_SHORT) and len(codes) == 5
    assert all(re.search(r"[A-Za-z_]", text) is None for text in HALT_SHORT.values())


def test_ac3_notify_knows_every_kind_the_engine_can_log() -> None:
    source = engine_module.__file__
    assert source is not None
    logged: set[str] = set()
    for node in ast.walk(ast.parse(Path(source).read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_log"
        ):
            kind = node.args[1]
            assert isinstance(kind, ast.Constant) and isinstance(kind.value, str), ast.dump(node)
            logged.add(kind.value)
    assert logged == set(NOTIFY_KINDS)
    assert set(notify.KIND_LABEL) == set(NOTIFY_KINDS)


def test_ac3_no_internal_codes_in_any_message() -> None:
    kinds: set[str] = set()
    count = 0
    for scenario in SCENARIOS:
        r = scenario(ALL)
        kinds |= {e.kind for e in r.engine.events}
        for text in r.done():
            visible = re.sub(r"委託編號 \S+", "", text).replace("元大 App", "元大")
            assert re.search(r"[A-Za-z_]", visible) is None, text
            count += 1
    assert kinds == set(NOTIFY_KINDS)  # 這些情境走過引擎會寫出的每一種事件
    assert count > 60


def test_ac3_codes_become_plain_words_and_unknown_ones_are_flagged() -> None:
    assert plain("停手（mismatch）") == "停手（跟券商對不上）"
    assert (
        plain("風控擋下 BUY 1 口：停手中（daily_loss），只接受平倉")
        == "風控擋下 買進 1 口：停手中（當日虧損到上限），只接受平倉"
    )
    assert plain("超過 10.0 秒沒成交") == "超過 10 秒沒成交"
    assert plain("停手（brand_new）") == "停手（代碼 brand_new，通知還不認得）"
    sender = MemorySender()
    notifier = Notifier(cfg(), outbox_for(sender))
    notifier.on_event(ev(1, "halt", "停手（brand_new）", halt="brand_new"))
    notifier.close()
    (sent,) = sender.sent
    assert sent[0].startswith(ACT) and "代碼 brand_new，通知還不認得" in sent[0]


# ---- AC4 同一件事不洗版


def test_ac4_same_blocked_reason_notifies_once_then_merges() -> None:
    r = disconnected_storm(100)
    texts = r.done()
    assert len([e for e in r.engine.events if e.kind == "reject"]) == 99
    blocked = [t for t in texts if "連線中斷，沒有送出：買進 1 口" in t]
    assert len(blocked) == 2
    assert "時間 11/13 09:00:01" in blocked[0]
    assert "同樣的原因 5 分鐘內不重複通知" in blocked[0]
    assert "｜連線中斷，沒有送出：買進 1 口（共 98 次）\n" in blocked[1]
    assert "（09:00:02～09:01:39 同樣的事共 98 次，合併成這一則。）" in blocked[1]


def test_ac4_merged_summary_goes_out_when_the_window_frees() -> None:
    r = disconnected_storm(400)
    r.outbox.drain()
    so_far = [t for t, _ in r.sender.sent if "連線中斷，沒有送出" in t]
    assert len(so_far) == 2  # 第一則即時送；第 301 秒視窗空出來，送出前 5 分鐘的合併
    assert "09:00:02～09:05:00 同樣的事共 299 次，合併成這一則" in so_far[1]
    last = [t for t in r.done() if "連線中斷，沒有送出" in t][-1]
    assert "09:05:01～09:06:39 同樣的事共 99 次，合併成這一則" in last


def test_ac4_burst_then_merge_keeps_the_latest_state() -> None:
    texts = [t for t in flapping({"burst": 2}).done() if "券商連線" in t]
    assert len(texts) == 3
    assert "｜券商連線中斷" in texts[0] and "合併" not in texts[0]
    assert "｜券商連線恢復" in texts[1]
    assert "這類通知 5 分鐘內已經 2 則，接下來的會合併成一則" in texts[1]
    # 最後的狀態是中斷，而且用合併進來的通知裡最嚴重的等級
    assert texts[2].startswith(WARN + "券商連線中斷（合併 3 則）\n")
    assert "09:00:03～09:00:05 共 3 則同類通知合併成這一則：" in texts[2]
    assert "券商連線中斷 2 次、券商連線恢復 1 次。上面是最後一則的內容。" in texts[2]


def test_ac4_single_late_message_says_when_it_happened() -> None:
    throttle = Throttle(window_s=300.0, burst=1)
    first = Message(at(0), "link", Level.WARN, "link", False, WHERE, "券商連線中斷", ())
    second = Message(at(7), "link", Level.INFO, "link", False, WHERE, "券商連線恢復", ())
    assert throttle.offer(first)[0].merged == 1 and throttle.offer(second) == []
    assert throttle.due(at(299)) == []
    (late,) = throttle.due(at(300))
    assert late.merged == 1 and late.headline == "券商連線恢復"
    assert "這則是 09:00:07 發生的；跟前面的通知同一類，所以晚了一點才送" in late.text
    assert throttle.flush() == []


def test_ac4_different_reasons_are_not_merged() -> None:
    r = daily_loss()
    r.engine.on_disconnect(at(6))
    scripted(r.engine).plan[scripted(r.engine).seen] = Side.BUY  # 再想買一次：這次是斷線擋的
    r.run([tick(7)])
    texts = r.done()
    assert len([t for t in texts if "風控擋下：買進 1 口" in t]) == 1
    assert len([t for t in texts if "連線中斷，沒有送出：買進 1 口" in t]) == 1


def test_ac4_urgent_news_is_never_held_back() -> None:
    # 有部位時連線斷斷續續：第 7 則（又斷了）超過即時名額，但它是要人處理的事，不能壓著
    r = rig({0: Side.BUY})
    r.run([tick(0), tick(1)])
    for s in range(2, 9):
        if s % 2 == 0:
            r.engine.on_disconnect(at(s))
        else:
            r.engine.on_reconnect(at(s))
    r.outbox.drain()  # 沒有呼叫 poll，也還沒結束
    links = [t for t, _ in r.sender.sent if "券商連線" in t]
    assert len(links) == 7 and links[-1].startswith(ACT + "券商連線中斷\n")
    assert "時間 11/13 09:00:08" in links[-1]
    # 壓著的「恢復」之後又斷線：先把壓著的送出，再送這一則，順序不會顛倒
    r.engine.on_reconnect(at(9))  # 第 8 則，一般通知：壓著
    r.engine.on_disconnect(at(10))
    r.outbox.drain()
    links = [t for t, _ in r.sender.sent if "券商連線" in t]
    assert "｜券商連線恢復\n" in links[-2] and "09:00:09 發生的" in links[-2]
    assert links[-1].startswith(ACT + "券商連線中斷\n") and "時間 11/13 09:00:10" in links[-1]
    r.done()


def test_ac4_invariant_rate_is_bounded_and_nothing_is_lost() -> None:
    rng = random.Random(20261113)
    window, burst = 60.0, 3
    throttle = Throttle(window_s=window, burst=burst)
    calm = [Level.INFO, Level.WARN]
    # （類別、是不是「只即時通知一次」、會出現的等級）；「urgent」那一類混著要人處理的事
    keys: list[tuple[str | None, bool, list[Level]]] = [
        ("link", False, calm),
        ("halt", False, calm),
        ("reject:甲", True, list(Level)),
        ("reject:乙", True, list(Level)),
        ("urgent", False, list(Level)),
        (None, False, list(Level)),
    ]
    now, offered, urgent = T0, 0, 0
    out: list[tuple[datetime, Message]] = []
    for _ in range(4000):
        now += timedelta(seconds=rng.choice([0.0, 0.2, 1.0, 3.0, 20.0, 70.0]))
        if rng.random() < 0.8:
            key, once, levels = rng.choice(keys)
            level = rng.choice(levels)
            message = Message(now, "x", level, key, once, WHERE, f"事{rng.randrange(3)}", ())
            offered += 1
            ready = throttle.offer(message)
            if (
                level is Level.ACT and not once
            ):  # 要人處理的事：當下就送，而且排在最後（前面是先清掉的合併）
                assert ready[-1] == message
                urgent += 1
            out += [(now, m) for m in ready]
        else:
            out += [(now, m) for m in throttle.due(now)]
    on_time = list(out)
    out += [(now, m) for m in throttle.flush()]  # 結束時不管視窗，全部吐出來
    assert sum(m.merged for _, m in out) == offered and urgent > 100
    assert all(m.merged == 1 for _, m in out if m.key is None)
    for key, once, levels in keys:
        if key is None or (Level.ACT in levels and not once):
            continue  # 成交與要人處理的事不受則數上限限制
        limit = 1 if once else burst
        times = [t for t, m in on_time if m.key == key]
        assert len(times) > limit
        for i, start in enumerate(times):
            in_window = sum(1 for t in times[i:] if t - start < timedelta(seconds=window))
            assert in_window <= limit


# ---- AC5 送失敗不影響交易


def busy_day(sender: Sender | None, close_wait_s: float = 30.0) -> tuple[Engine, Notifier | None]:
    """有成交、停手、被擋的一天；sender 不是 None 就開背景執行緒送通知。"""
    config = cfg({"close_wait_s": close_wait_s}, daily_loss_limit_twd=300.0)
    notifier = None
    if sender is not None:
        assert config.notify is not None
        outbox = Outbox.from_config(sender, config.notify)
        outbox.start()
        notifier = Notifier(config, outbox)
    engine = Engine(
        config,
        Scripted({0: Side.BUY, 4: Side.BUY, 6: Side.BUY}),
        FlakyBroker(SimBroker(config.sim)),
        sink=notifier.on_event if notifier is not None else None,
    )
    ticks = [tick(0), tick(1), tick(2, 26950.0), tick(3, 26950.0), *(tick(s) for s in range(4, 8))]
    for t in ticks:
        engine.on_tick(t)
    return engine, notifier


def test_ac5_engine_is_identical_when_notifications_fail_or_hang() -> None:
    baseline, _ = busy_day(None)
    assert [e.kind for e in baseline.events].count("fill") == 2
    stuck = Stuck()
    senders: list[Sender] = [
        Failing(SendError("連不上", retryable=True)),
        Failing(SendError("金鑰不對", retryable=False)),
        Failing(RuntimeError("boom")),
        stuck,
    ]
    for sender in senders:
        # 一直送不出去的：結束時只等一下就放棄（不然每則都會試到時間用完）
        engine, notifier = busy_day(sender, close_wait_s=30.0 if sender is stuck else 0.2)
        assert notifier is not None
        assert engine.events == baseline.events
        assert engine.ledger.realized_twd == baseline.ledger.realized_twd
        assert engine.ledger.position == baseline.ledger.position == 0
        if sender is stuck:  # 引擎跑完整天的時候，背景那邊還卡在送第一則
            assert stuck.entered.wait(timeout=60) and stuck.sent == []
            stuck.release.set()
        report = notifier.close()
        # 2 筆成交、停手、被擋 1 則即時＋1 則結束時補送
        assert report.sent + report.lost == 5
        assert report.sent == (5 if sender is stuck else 0)
        assert len([f for f in report.failures if f.final]) == report.lost


def test_ac5_engine_thread_never_waits_for_a_stuck_sender() -> None:
    config = cfg()
    assert config.notify is not None
    stuck = Stuck()
    outbox = Outbox.from_config(stuck, config.notify)
    outbox.start()
    notifier = Notifier(config, outbox)
    for i in range(50):  # 送出端卡住時，這個迴圈照樣跑完（會卡的話測試就停在這裡）
        side = "BUY" if i % 2 == 0 else "SELL"
        fill = {"order_id": f"S{i:06d}", "symbol": "TMF", "side": side, "qty": 1, "price": 27000.0}
        notifier.on_event(ev(i, "fill", "成交", **fill))
    assert stuck.entered.wait(timeout=60)  # 背景那邊確實卡在送第一則
    assert outbox.sent == 0 and outbox.pending == 50
    stuck.release.set()
    report = notifier.close()
    assert (report.sent, report.lost) == (50, 0)
    assert ["買進" in t for t in stuck.sent] == [i % 2 == 0 for i in range(50)]


def test_ac5_broken_message_building_does_not_reach_the_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = round_trip()
    baseline.done()

    def broken(*args: object) -> Message:
        raise RuntimeError("範本壞了")

    monkeypatch.setattr(notify, "compose", broken)
    r = round_trip()
    assert r.engine.events == baseline.engine.events
    report = r.notifier.close()
    texts = [t for t, _ in r.sender.sent]
    assert len(texts) == 2 and all(t.startswith(ACT + "有一件事沒辦法轉成通知") for t in texts)
    assert all("種類：成交" in t and "到電腦上看" in t for t in texts)
    assert [(f.final, f.attempts) for f in report.failures] == [(True, 0), (True, 0)]
    assert all("內容產生失敗" in f.reason and "範本壞了" in f.reason for f in report.failures)
    assert report.failures[0].ts == at(1) and "成交" in report.failures[0].title


# ---- AC6 失敗留紀錄


def test_ac6_retries_until_it_goes_out() -> None:
    waits: list[float] = []
    sender = Script(down(), down())
    box = outbox_for(sender, waits=waits)
    assert box.put(item(1)) is True
    box.drain()
    assert (box.sent, box.lost, box.pending) == (1, 0, 0)
    assert waits == [2.0, 2.0] and box.failures == ()
    assert sender.sent == [(item(1).text, True)]


def test_ac6_keeps_trying_in_order_and_records_once_that_it_is_struggling() -> None:
    waits: list[float] = []
    sender = Script(*[down() for _ in range(7)])
    box = outbox_for(sender, waits=waits)
    box.put(item(1))
    box.put(item(2))
    box.drain()  # 第 1 則失敗 7 次、第 8 次才送出；第 2 則要等它有結果才送
    assert [t for t, _ in sender.sent] == [item(1).text, item(2).text]  # 順序不會亂
    assert (box.sent, box.lost, box.pending) == (2, 0, 0)
    (record,) = box.failures  # 連續失敗 3 次時記一筆「還在重試」，之後不重複記
    assert (record.title, record.attempts, record.final) == ("第 1 則", 3, False)
    assert "連不上" in record.reason and "還會再試" in record.reason
    assert waits == [2.0] * 7


def test_ac6_hopeless_error_is_not_retried_and_the_next_message_says_so() -> None:
    waits: list[float] = []
    sender = Script(SendError("Telegram 回 401：金鑰不對", retryable=False))
    box = outbox_for(sender, waits=waits)
    box.put(item(1))
    box.put(item(2))
    box.drain()
    (record,) = box.failures
    assert (record.title, record.attempts, record.final) == ("第 1 則", 1, True)
    assert record.ts == at(1) and "401" in record.reason and waits == []
    assert (box.sent, box.lost) == (1, 1)
    text, silent = sender.sent[0]
    assert text == item(2).text + "\n（前面有 1 則通知沒送出去，請到電腦上看紀錄。）"
    assert silent is False  # 有通知漏掉時，這一則不靜音
    box.put(item(3))
    box.drain()
    assert sender.sent[1] == (item(3).text, True)  # 講過一次就不再重複


def test_ac6_waits_as_long_as_the_server_asks() -> None:
    waits: list[float] = []
    slow = [SendError("慢一點", retryable=True, retry_after_s=s) for s in (7.0, 0.5)]
    box = outbox_for(Script(*slow), waits=waits)
    box.put(item(1))
    box.drain()
    assert waits == [7.0, 2.0] and box.sent == 1


def test_ac6_full_queue_drops_new_messages_and_records_them() -> None:
    sender = Script()
    box = outbox_for(sender, queue_size=2)
    assert [box.put(item(n)) for n in range(1, 6)] == [True, True, False, False, False]
    assert (box.offered, box.lost, box.pending) == (5, 3, 2)
    assert [f.title for f in box.failures] == ["第 3 則", "第 4 則", "第 5 則"]
    assert all(f.final and f.attempts == 0 and "排滿" in f.reason for f in box.failures)
    box.drain()
    assert [t.splitlines()[0] for t, _ in sender.sent] == ["第 1 則", "第 2 則"]
    assert "前面有 3 則通知沒送出去" in sender.sent[0][0]
    assert "沒送出去" not in sender.sent[1][0]


def test_ac6_closing_records_what_was_never_sent() -> None:
    told: list[Failure] = []
    sender = Script(*[down() for _ in range(100)])
    box = Outbox(
        sender, warn_after_attempts=3, retry_wait_s=2.0, queue_size=10, on_failure=told.append
    )
    box.put(item(1))
    box.put(item(2))
    box.close(0.0)
    assert (box.sent, box.lost, box.pending) == (0, 2, 0)
    assert [(f.title, f.final) for f in box.failures] == [("第 1 則", True), ("第 2 則", True)]
    assert all("程式結束" in f.reason and "連不上" in f.reason for f in box.failures)
    assert told == list(box.failures)
    assert box.put(item(3)) is False
    assert "已經關閉" in box.failures[-1].reason and box.lost == 3


def test_ac6_unexpected_sender_error_is_recorded_and_delivery_goes_on() -> None:
    sender = Script(RuntimeError("boom"))
    box = outbox_for(sender)
    box.put(item(1))
    box.put(item(2))
    box.drain()
    (record,) = box.failures
    assert record.final and "沒預期的錯誤" in record.reason and "RuntimeError" in record.reason
    assert (box.sent, box.lost) == (1, 1) and sender.sent[0][0].startswith("第 2 則")


def test_ac6_every_message_is_either_sent_or_recorded() -> None:
    rng = random.Random(20261113)
    outcomes: list[Callable[[], BaseException | None]] = [
        lambda: None,
        lambda: None,
        down,
        lambda: SendError("壞了", retryable=False),
        lambda: RuntimeError("x"),
    ]
    for _ in range(30):
        sender = Script(*[rng.choice(outcomes)() for _ in range(rng.randrange(0, 80))])
        box = outbox_for(sender, queue_size=rng.choice([1, 3, 50]))
        for n in range(rng.randrange(1, 40)):
            box.put(item(n))
            if rng.random() < 0.4:
                box.drain()
        box.close(0.0)
        assert box.sent + box.lost == box.offered and box.pending == 0
        assert box.sent == len(sender.sent)
        assert len([f for f in box.failures if f.final]) == box.lost


def test_ac6_background_thread_delivers_in_order() -> None:
    sender = MemorySender()
    box = Outbox(sender, warn_after_attempts=3, retry_wait_s=0.0, queue_size=500)
    box.start()
    for n in range(300):
        assert box.put(item(n)) is True
    box.close(60.0)
    assert [t for t, _ in sender.sent] == [item(n).text for n in range(300)]
    assert (box.sent, box.lost, box.pending) == (300, 0, 0)


# ---- AC7 送出介面與 Telegram


class FakePost:
    """假的連線：記下每個請求，照劇本回應；劇本用完就回成功。"""

    def __init__(self, *replies: tuple[int, bytes] | BaseException) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, dict[str, Any], float]] = []

    def __call__(self, url: str, body: bytes, timeout_s: float) -> tuple[int, bytes]:
        self.calls.append((url, json.loads(body), timeout_s))
        answer = self.replies.pop(0) if self.replies else reply(200, ok=True, result={})
        if isinstance(answer, BaseException):
            raise answer
        return answer


def reply(status: int, **payload: object) -> tuple[int, bytes]:
    return status, json.dumps(payload).encode("utf-8")


def telegram(*replies: tuple[int, bytes] | BaseException) -> tuple[TelegramSender, FakePost]:
    post = FakePost(*replies)
    return TelegramSender(TOKEN, CHAT, timeout_s=5.0, post=post), post


def test_ac7_telegram_request_shape() -> None:
    sender, post = telegram()
    sender.send("哈囉\n第二行", silent=True)
    sender.send("要處理", silent=False)
    url, body, timeout = post.calls[0]
    assert url == f"https://api.telegram.org/bot{TOKEN}/sendMessage" and timeout == 5.0
    assert body == {
        "chat_id": 987654321,
        "text": "哈囉\n第二行",
        "disable_notification": True,
        "link_preview_options": {"is_disabled": True},
    }
    assert post.calls[1][1]["disable_notification"] is False


def test_ac7_long_text_is_clipped_to_what_telegram_accepts() -> None:
    sender, post = telegram()
    sender.send("字" * 5000, silent=False)
    text = post.calls[0][1]["text"]
    assert len(text) == 4096 and text.endswith("…（太長，後面省略）")


def test_ac7_telegram_errors_are_sorted_into_retry_or_give_up() -> None:
    def fails(answer: tuple[int, bytes] | BaseException) -> SendError:
        sender, _ = telegram(answer)
        with pytest.raises(SendError) as info:
            sender.send("x", silent=False)
        return info.value

    transient: list[tuple[int, bytes] | BaseException] = [
        TimeoutError("timed out"),
        urllib.error.URLError("getaddrinfo failed"),
        reply(502, ok=False, description="Bad Gateway"),
        (200, b"<html>login</html>"),
        reply(200, ok=False),
    ]
    for answer in transient:
        assert fails(answer).retryable is True
    slow = fails(
        reply(429, ok=False, description="Too Many Requests", parameters={"retry_after": 7})
    )
    assert slow.retryable is True and slow.retry_after_s == 7.0
    blocked = fails(reply(403, ok=False, description="Forbidden: bot was blocked by the user"))
    assert blocked.retryable is False and "403" in str(blocked) and "開始" in str(blocked)
    unauthorized = fails(reply(401, ok=False, description="Unauthorized"))
    assert unauthorized.retryable is False and ENV_TOKEN in str(unauthorized)
    no_chat = fails(reply(400, ok=False, description="Bad Request: chat not found"))
    assert no_chat.retryable is False and ENV_CHAT in str(no_chat)


class FakeResponse:
    """假的 HTTP 回應。"""

    def __init__(self, status: int, raw: bytes) -> None:
        self.status = status
        self._raw = raw

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class FakeHttp:
    """假的 urllib.request.urlopen：記下請求、照劇本回應；劇本用完就回成功。"""

    def __init__(self, *replies: tuple[int, dict[str, Any]] | BaseException) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, dict[str, Any], float]] = []
        self.methods: list[str] = []
        self.content_types: list[str | None] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> FakeResponse:
        assert isinstance(request.data, bytes)
        self.calls.append((request.full_url, json.loads(request.data), timeout))
        self.methods.append(request.get_method())
        self.content_types.append(request.get_header("Content-type"))
        answer = self.replies.pop(0) if self.replies else (200, {"ok": True, "result": {}})
        if isinstance(answer, BaseException):
            raise answer
        status, payload = answer
        raw = json.dumps(payload).encode("utf-8")
        if status != 200:
            headers = email.message.Message()
            raise urllib.error.HTTPError(
                request.full_url, status, "error", headers, io.BytesIO(raw)
            )
        return FakeResponse(status, raw)


def test_ac7_http_post_uses_urllib_and_returns_error_bodies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http = FakeHttp((200, {"ok": True}), (403, {"ok": False}))
    monkeypatch.setattr(urllib.request, "urlopen", http)
    assert http_post("https://example.invalid/a", b'{"x":1}', 3.0) == (200, b'{"ok": true}')
    assert http_post("https://example.invalid/b", b"{}", 3.0) == (403, b'{"ok": false}')
    assert http.calls[0] == ("https://example.invalid/a", {"x": 1}, 3.0)
    assert http.methods == ["POST", "POST"]
    assert http.content_types == ["application/json", "application/json"]


def test_ac7_tests_cannot_reach_the_network() -> None:
    with pytest.raises(AssertionError, match="測試不得連網"):
        socket.create_connection(("192.0.2.1", 443), timeout=0.1)
    with pytest.raises(AssertionError, match="測試不得連網"):
        http_post("https://192.0.2.1/x", b"{}", 0.1)


def test_ac7_memory_and_print_senders(capsys: pytest.CaptureFixture[str]) -> None:
    memory = MemorySender()
    memory.send("甲", silent=True)
    assert memory.sent == [("甲", True)]
    PrintSender().send("🚨 要處理【模擬】微台｜測試\n第二行", silent=False)
    assert "要處理【模擬】微台｜測試\n第二行\n" in capsys.readouterr().out

    class Narrow(io.StringIO):
        """像 Windows 主控台那樣顯示不了表情符號。"""

        encoding = "cp950"

    narrow = Narrow()
    PrintSender(narrow).send("🚨 要處理｜測試", silent=False)
    assert "? 要處理｜測試" in narrow.getvalue()


# ---- AC8 設定


def raw_config() -> dict[str, Any]:
    with CONFIG_PATH.open("rb") as fh:
        return tomllib.load(fh)


def test_ac8_shipped_config_notifies_fills_and_anomalies_by_default() -> None:
    n = load_config(CONFIG_PATH).notify
    assert n is not None and n.channel == "telegram" and n.name == "微台"
    assert set(n.kinds) == set(NOTIFY_KINDS) - {"order"}  # 只有「每一筆送出的委託」預設不通知
    assert set(NOTIFY_LIVE_REQUIRED) <= set(n.kinds) <= set(NOTIFY_KINDS)
    assert n.quiet_routine is True and n.throttle_s == 300.0 and n.burst >= 1


def test_ac8_notify_config_is_validated() -> None:
    bad: list[tuple[str, object, str]] = [
        ("channel", "line", "channel"),
        ("name", "", "name"),
        ("kinds", [], "kinds"),
        ("kinds", ["fill", "fill"], "重複"),
        ("kinds", ["fill", "mystery"], "mystery"),
        ("kinds", "fill", "kinds"),
        ("quiet_routine", "yes", "quiet_routine"),
        ("throttle_s", 0, "throttle_s"),
        ("burst", 0, "burst"),
        ("timeout_s", 0, "timeout_s"),
        ("warn_after_attempts", 0, "warn_after_attempts"),
        ("retry_wait_s", 0, "retry_wait_s"),  # 0 會讓送不出去時不停重試
        ("retry_wait_s", float("nan"), "retry_wait_s"),
        ("throttle_s", float("inf"), "throttle_s"),
        ("timeout_s", float("inf"), "timeout_s"),
        ("close_wait_s", float("inf"), "close_wait_s"),
        ("burst", 2.5, "burst"),
        ("burst", True, "burst"),
        ("queue_size", "many", "queue_size"),
        ("queue_size", 0, "queue_size"),
        ("close_wait_s", -1, "close_wait_s"),
    ]
    for key, value, message in bad:
        raw = raw_config()
        raw["notify"][key] = value
        with pytest.raises(ConfigError, match=message):
            parse_config(raw)
    for key in raw_config()["notify"]:
        raw = raw_config()
        del raw["notify"][key]
        with pytest.raises(ConfigError, match=key):
            parse_config(raw)
    raw = raw_config()
    del raw["notify"], raw["notify_push"]  # 第二管道依附在 [notify] 上，一起拿掉
    assert parse_config(raw).notify is None  # 模擬可以不開通知


def test_ac8_live_mode_requires_notify_with_the_serious_kinds() -> None:
    def live() -> dict[str, Any]:
        raw = raw_config()
        raw["mode"] = "live"
        raw["risk"] |= {"daily_loss_limit_twd": 3000.0, "position_stop_loss_twd": 1500.0}
        return raw

    assert parse_config(live()).mode == "live"
    raw = live()
    del raw["notify"], raw["notify_push"]  # 第二管道依附在 [notify] 上，一起拿掉
    with pytest.raises(ConfigError, match=r"真單.*\[notify\]"):
        parse_config(raw)
    for kind in NOTIFY_LIVE_REQUIRED:
        raw = live()
        raw["notify"]["kinds"] = [k for k in raw["notify"]["kinds"] if k != kind]
        with pytest.raises(ConfigError, match=kind):
            parse_config(raw)


def test_ac8_notifier_refuses_to_start_without_notify_section() -> None:
    with pytest.raises(ConfigError, match=r"\[notify\]"):
        Notifier(replace(cfg(), notify=None), outbox_for(Script()))


# ---- AC9 金鑰不外洩


def test_ac9_missing_or_malformed_secrets_raise_naming_only_the_variable() -> None:
    n = cfg().notify
    assert n is not None
    with pytest.raises(ConfigError, match=ENV_TOKEN):
        telegram_from_env({}, n)
    with pytest.raises(ConfigError, match=ENV_CHAT) as info:
        telegram_from_env({ENV_TOKEN: TOKEN}, n)
    assert TOKEN not in str(info.value)
    with pytest.raises(ConfigError, match=ENV_TOKEN) as info:
        telegram_from_env({ENV_TOKEN: "不像金鑰 abc", ENV_CHAT: CHAT}, n)
    assert "abc" not in str(info.value)
    with pytest.raises(ConfigError, match=ENV_CHAT) as info:
        telegram_from_env({ENV_TOKEN: TOKEN, ENV_CHAT: "我的帳號 xyz"}, n)
    assert "xyz" not in str(info.value) and TOKEN not in str(info.value)
    assert isinstance(telegram_from_env({ENV_TOKEN: TOKEN, ENV_CHAT: CHAT}, n), TelegramSender)


def test_ac9_secrets_never_appear_in_errors_or_records() -> None:
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    leaky: list[tuple[int, bytes] | BaseException] = [
        urllib.error.URLError(f"cannot reach {url} for chat {CHAT}"),
        reply(400, ok=False, description=f"Bad Request: chat {CHAT} not found for bot{TOKEN}"),
        (200, f"<html>{url}</html>".encode()),
    ]
    for answer in leaky:
        sender, _ = telegram(answer)
        with pytest.raises(SendError) as info:
            sender.send("x", silent=False)
        shown = str(info.value) + repr(info.value)
        assert TOKEN not in shown and CHAT not in shown and "***" in shown
        if isinstance(answer, BaseException):  # 原本的錯誤帶著網址，不接在後面一起丟出去
            assert info.value.__cause__ is None and info.value.__suppress_context__
    sender, _ = telegram(leaky[1])
    box = outbox_for(sender)
    box.put(item(1))
    box.drain()
    (record,) = box.failures
    assert TOKEN not in record.reason and CHAT not in record.reason and "400" in record.reason
    assert TOKEN not in repr(sender) and CHAT not in repr(sender)


# ---- AC10 指令列


@pytest.fixture
def telegram_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TOKEN, TOKEN)
    monkeypatch.setenv(ENV_CHAT, CHAT)


def test_ac10_cli_notify_test_sends_one_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], telegram_env: None
) -> None:
    http = FakeHttp()
    monkeypatch.setattr(urllib.request, "urlopen", http)
    assert main(["notify-test", "--config", str(CONFIG_PATH)]) == 0
    ((url, body, timeout),) = http.calls
    assert url.endswith("/sendMessage") and timeout == 5.0
    assert body["chat_id"] == int(CHAT) and body["disable_notification"] is True
    assert body["text"].startswith("【模擬】微台｜測試通知\n")
    out = capsys.readouterr()
    assert "已送出" in out.out
    assert TOKEN not in out.out + out.err and CHAT not in out.out + out.err
    assert main(["notify-test", "--config", str(CONFIG_PATH), "--level", "act"]) == 0
    urgent = http.calls[1][1]
    assert urgent["text"].startswith("🚨 要處理【模擬】微台｜測試通知")
    assert urgent["disable_notification"] is False


def test_ac10_cli_reads_secrets_from_env_file_when_not_in_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    monkeypatch.delenv(ENV_CHAT, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(f"OTHER=1\n{ENV_TOKEN}={TOKEN}\n", encoding="utf-8")
    http = FakeHttp()
    monkeypatch.setattr(urllib.request, "urlopen", http)
    args = ["notify-test", "--config", str(CONFIG_PATH)]
    assert main(args) == 2  # 金鑰還沒設：講清楚缺哪一個，不丟一長串錯誤，也不連網
    assert f"設定還沒好：環境變數 {ENV_TOKEN} 沒有設定" in capsys.readouterr().out
    assert main([*args, "--env-file", str(env_file)]) == 2  # 有金鑰、還沒有收件代號
    out = capsys.readouterr().out
    assert ENV_CHAT in out and "notify-setup" in out and TOKEN not in out
    assert http.calls == []
    env_file.write_text(f"OTHER=1\n{ENV_TOKEN}={TOKEN}\n{ENV_CHAT}='{CHAT}'\n", encoding="utf-8")
    assert main([*args, "--env-file", str(env_file)]) == 0
    assert len(http.calls) == 1 and http.calls[0][1]["chat_id"] == int(CHAT)


def test_ac10_cli_notify_test_fails_loudly_without_leaking_secrets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], telegram_env: None
) -> None:
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    monkeypatch.setattr(
        urllib.request, "urlopen", FakeHttp(urllib.error.URLError(f"no route {url}"))
    )
    assert main(["notify-test", "--config", str(CONFIG_PATH)]) == 1
    out = capsys.readouterr()
    shown = out.out + out.err
    assert "沒送出去" in shown and "連不上" in shown
    assert TOKEN not in shown and CHAT not in shown


def test_ac10_cli_notify_setup_replies_inside_telegram_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(ENV_TOKEN, TOKEN)
    monkeypatch.delenv(ENV_CHAT, raising=False)  # 這一步就是要找出收件代號，所以還沒有
    private = {"id": 555000111, "type": "private"}
    updates = [
        {"update_id": 1, "message": {"chat": private, "text": "/start"}},
        {"update_id": 2, "message": {"chat": private, "text": "hi"}},
        {"update_id": 3, "message": {"chat": {"id": -100222, "type": "group"}, "text": "hi"}},
        {"update_id": 4, "my_chat_member": {"chat": {"id": 555000333, "type": "private"}}},
    ]
    http = FakeHttp((200, {"ok": True, "result": updates}))
    monkeypatch.setattr(urllib.request, "urlopen", http)
    assert main(["notify-setup", "--config", str(CONFIG_PATH)]) == 0
    assert http.calls[0][0] == f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    (told,) = http.calls[1:]
    assert told[0].endswith("/sendMessage") and told[1]["chat_id"] == 555000111
    assert "555000111" in told[1]["text"] and ENV_CHAT in told[1]["text"]
    out = capsys.readouterr().out
    assert "回覆了 1 個人" in out and "555000111" not in out and TOKEN not in out

    monkeypatch.setattr(urllib.request, "urlopen", FakeHttp((200, {"ok": True, "result": []})))
    assert main(["notify-setup", "--config", str(CONFIG_PATH)]) == 1
    assert "按「開始」" in capsys.readouterr().out
    monkeypatch.setattr(urllib.request, "urlopen", FakeHttp((401, {"ok": False})))
    assert main(["notify-setup", "--config", str(CONFIG_PATH)]) == 1
    shown = capsys.readouterr()
    assert ENV_TOKEN in shown.out + shown.err and TOKEN not in shown.out + shown.err


TRADE_ROWS = [
    "20261113,TMF   ,202611     ,090000,27000,2,-,-, ",
    "20261113,TMF   ,202611     ,090001,27000,2,-,-, ",
    "20261113,TMF   ,202611     ,090002,27050,2,-,-, ",
    "20261113,TMF   ,202611     ,090003,27050,2,-,-, ",
    "20261113,TMF   ,202611     ,090004,27050,2,-,-, ",
]


def trading_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """一天買賣各一筆的重播指令（策略換成照劇本下單的假策略）。"""
    ticks = write_csv(tmp_path / "Daily_2026_11_13.csv", TRADE_ROWS, encoding="utf-8-sig")
    monkeypatch.setattr(
        cli, "build_strategy", lambda name, cfg, ms_repo: Scripted({0: Side.BUY, 2: Side.SELL})
    )
    day = ["--ticks", str(ticks), "--day", "2026-11-13", "--strategy", "none"]
    return ["replay", "--config", str(CONFIG_PATH), *day]


def test_ac10_cli_replay_print_shows_the_days_notifications(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    args = trading_day(tmp_path, monkeypatch)
    assert main([*args, "--notify", "print"]) == 0
    out = capsys.readouterr().out
    assert "【模擬重播】微台｜買進 1 口，成交 27,001\n時間 11/13 09:00:01\n" in out
    assert "【模擬重播】微台｜賣出 1 口，成交 27,049｜這一趟賺 440 元" in out
    assert "notify_sent: 2" in out and "notify_failed: 0" in out
    assert main(args) == 0
    assert "notify_sent" not in capsys.readouterr().out  # 沒開通知就沒有這兩行


def test_ac10_cli_replay_send_reports_and_logs_what_did_not_go_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    telegram_env: None,
) -> None:
    args = trading_day(tmp_path, monkeypatch)
    blocked = {"ok": False, "description": f"Forbidden: bot{TOKEN} was blocked by {CHAT}"}
    http = FakeHttp((403, blocked))
    monkeypatch.setattr(urllib.request, "urlopen", http)
    log = tmp_path / "notify_failures.jsonl"
    assert main([*args, "--notify", "send", "--notify-log", str(log)]) == 0
    out = capsys.readouterr()
    assert "notify_sent: 1" in out.out and "notify_failed: 1" in out.out
    assert "通知沒送出去" in out.err and "買進 1 口" in out.err
    assert len(http.calls) == 2
    assert "前面有 1 則通知沒送出去" in http.calls[1][1]["text"]
    (line,) = log.read_text(encoding="utf-8").splitlines()
    record = json.loads(line)
    assert record["final"] is True and record["attempts"] == 1 and "403" in record["reason"]
    assert record["ts"] == "2026-11-13T09:00:01+08:00" and "買進 1 口" in record["title"]
    everything = out.out + out.err + line
    assert TOKEN not in everything and CHAT not in everything


def test_ac10_cli_replay_send_refuses_to_run_without_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    monkeypatch.delenv(ENV_CHAT, raising=False)
    with pytest.raises(ConfigError, match=ENV_TOKEN):
        main([*trading_day(tmp_path, monkeypatch), "--notify", "send"])


# ---- AC11 決定性


def test_ac11_same_events_give_identical_messages() -> None:
    def everything() -> list[list[tuple[str, bool]]]:
        runs = [scenario(ALL) for scenario in SCENARIOS] + [disconnected_storm(700, ALL)]
        for r in runs:
            r.notifier.close()
        return [r.sender.sent for r in runs]

    first, second = everything(), everything()
    assert first == second
    assert sum(len(sent) for sent in first) > 70
    assert any("合併成這一則" in text for sent in first for text, _ in sent)


# ---- AC12 重啟後接手


def test_ac12_notifier_takes_over_the_restored_position(tmp_path: Path) -> None:
    config = cfg()
    path = tmp_path / "journal.jsonl"
    log = Journal(path)
    first = Engine(config, Scripted({0: Side.BUY}), SimBroker(config.sim), sink=log.write)
    first.on_tick(tick(0))
    first.on_tick(tick(1))
    log.close()
    state = recover(read_journal(path), config, T0.date())
    assert state.position == 1 and state.avg_price == 27001.0

    broker = SimBroker(config.sim)
    broker._position = 1  # 券商端確實有 1 口
    sender = MemorySender()
    notifier = Notifier(config, outbox_for(sender), restored=state)
    second = Engine(config, Scripted({1: Side.SELL}), broker, sink=notifier.on_event)
    second.restore(state)
    for t in [tick(2), tick(3, 27050.0), tick(4, 27050.0)]:
        second.on_tick(t)
    notifier.close()
    ((text, _),) = sender.sent
    # 重啟前的進場不在這次的事件裡，所以只講這一筆、不講「這一趟」；當日損益照樣是對的
    assert "賣出 1 口，成交 27,049｜這筆賺 460 元" in text and "這一趟" not in text
    assert "帳上部位：空手" in text and "今天已實現 +440 元（已扣成本）" in text
    assert notifier.book.ledger.realized_twd == second.ledger.realized_twd


# ---- AC13 時區


def test_ac13_times_are_shown_in_taipei_and_naive_times_are_not_trusted() -> None:
    sender = MemorySender()
    notifier = Notifier(cfg(), outbox_for(sender))
    fill = {"order_id": "S000001", "symbol": "TMF", "qty": 1, "price": 27000.0}
    utc = datetime(2026, 11, 13, 1, 0, 1, tzinfo=UTC)  # 台北時間 09:00:01
    notifier.on_event(Event(utc, "fill", "成交", {**fill, "side": "BUY"}))
    naive = datetime(2026, 11, 13, 9, 0, 2)
    notifier.on_event(Event(naive, "fill", "成交", {**fill, "side": "SELL"}))
    notifier.poll(T0 + timedelta(seconds=3))
    notifier.poll(naive)  # 沒有時區的時間拿來問也一樣：不當成台北時間，也不會往外丟錯
    report = notifier.close()
    first, second = [t for t, _ in sender.sent]
    assert "買進 1 口" in first and "時間 11/13 09:00:01" in first
    assert second.startswith(ACT + "有一件事沒辦法轉成通知") and "種類：成交" in second
    (failure,) = report.failures
    assert "沒有時區" in failure.reason and failure.final
    assert notifier.book.ledger.position == 1  # 那筆沒有時區的成交沒有被記進小帳

    lonely = MemorySender()
    alone = Notifier(cfg(), outbox_for(lonely))
    alone.on_event(Event(naive, "fill", "成交", {**fill, "side": "BUY"}))
    alone.close()
    assert len(lonely.sent) == 1 and "有一件事沒辦法轉成通知" in lonely.sent[0][0]


# ---- 獨立審查找到的情境（2026-11-13）


def test_ac1_reversal_in_one_fill_does_not_claim_a_round_trip() -> None:
    book = Book(cfg().contract)
    fill = {"order_id": "S1", "symbol": "TMF", "price": 22000.0}
    book.apply(ev(0, "fill", "", **fill, side="BUY", qty=1))
    flipped = book.apply(ev(1, "fill", "", **{**fill, "price": 22010.0}, side="SELL", qty=2))
    assert flipped.position == -1 and flipped.closing and flipped.trip_twd is None
    flat = book.apply(ev(2, "fill", "", **{**fill, "price": 22005.0}, side="BUY", qty=1))
    assert flat.position == 0 and flat.trip_twd is None  # 成本分不乾淨，就只講這一筆


def test_ac2_stale_exit_while_suspended_is_not_called_a_kill_switch() -> None:
    sender = MemorySender()
    notifier = Notifier(cfg(), outbox_for(sender))
    notifier.on_event(ev(1, "halt", "平倉單 S000002 逾時，緊急停止", halt="broker_reject"))
    notifier.close()
    text = sender.sent[0][0]
    assert text.startswith(ACT + "平倉單一直沒成交，而且程式在暫停送單中\n")
    assert "暫停的原因：連續被券商拒絕" in text and "緊急停止沒有作用" not in text


def test_ac10_notify_setup_keeps_going_when_one_chat_cannot_be_reached() -> None:
    def updates(*ids: int) -> tuple[int, bytes]:
        rows = [{"message": {"chat": {"id": i, "type": "private"}}} for i in ids]
        return reply(200, ok=True, result=rows)

    blocked = reply(403, ok=False, description="Forbidden: bot was blocked by the user")
    post = FakePost(updates(111000111, 222000222), blocked)
    assert telegram_tell_chat_ids(TOKEN, 5.0, post) == 1  # 第一個人封鎖了機器人，第二個照樣收到
    assert [c[1]["chat_id"] for c in post.calls[1:]] == [111000111, 222000222]
    with pytest.raises(SendError, match="403") as info:
        telegram_tell_chat_ids(TOKEN, 5.0, FakePost(updates(111000111), blocked))
    assert "111000111" not in str(info.value) and TOKEN not in str(info.value)
