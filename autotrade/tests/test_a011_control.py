"""A-011：遠端緊急停止（D-225）——交易程式這一側。網站只能叫停；交易程式讀本機的指令檔、執行、追蹤到平掉、回報。"""

from __future__ import annotations

import json
import os
import random
import time as time_module
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from autotrade import control as control_module
from autotrade import relay as relay_module
from autotrade.broker.sim import SimBroker
from autotrade.config import (
    Config,
    ConfigError,
    ControlConfig,
    ExportConfig,
    load_config,
    parse_config,
)
from autotrade.control import (
    HEARTBEAT_NAME,
    INBOX_NAME,
    ControlError,
    ControlLoop,
    ControlState,
    FileControl,
    command_to_json,
    outbox_name,
    result_from_json,
    write_json_atomic,
)
from autotrade.engine import Engine
from autotrade.export import snapshot
from autotrade.journal import Journal, read_journal, recover
from autotrade.model import (
    ACTION_STOP,
    RESULT_CANCELLING,
    RESULT_FLAT,
    RESULT_FLATTENING,
    RESULT_IGNORED,
    RESULT_LINK_DOWN,
    RESULT_RELEASED,
    RESULT_STUCK,
    RESULT_SUSPENDED,
    STAGE_ACK,
    STAGE_DONE,
    TAIPEI,
    Command,
    CommandResult,
    Side,
    Tick,
)
from autotrade.risk import (
    HALT_BROKER_REJECT,
    HALT_DAILY_LOSS,
    HALT_KILL,
    HALT_MISMATCH,
    HALT_ORDER_RATE,
)
from test_a010_broker_requirements import FlakyBroker, Scripted

CONFIG_PATH = Path(__file__).parents[1] / "configs" / "tmf_sim.toml"
T0 = datetime(2026, 11, 13, 9, 0, 0, tzinfo=TAIPEI)
TODAY = T0.date()
MANY = 10_000
CONTROL = ControlConfig(poll_s=2.0, flatten_timeout_s=20.0, stale_s=10.0)


def cfg(max_events: int = 30, **risk: object) -> Config:
    base = load_config(CONFIG_PATH)
    defaults: dict[str, object] = {
        "max_entries_per_day": MANY,
        "max_orders_per_minute": None,
        "max_consecutive_broker_rejects": None,
        "position_stop_loss_twd": None,
    }
    return replace(
        base,
        risk=replace(base.risk, **{**defaults, **risk}),  # type: ignore[arg-type]
        export=ExportConfig(max_events=max_events),
        control=CONTROL,
    )


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def tick(seconds: float, price: float = 27000.0) -> Tick:
    return Tick(ts=at(seconds), price=price, qty=1)


def stop(
    command_id: int, seconds: float = 0.0, action: str = ACTION_STOP, day: date = TODAY
) -> Command:
    return Command(id=command_id, action=action, day=day, created_at=at(seconds))


def engine_with(plan: dict[int, Side], config: Config | None = None) -> tuple[Engine, FlakyBroker]:
    config = config or cfg()
    broker = FlakyBroker(SimBroker(config.sim))
    return Engine(config, Scripted(plan), broker), broker


def long_one(config: Config | None = None) -> tuple[Engine, FlakyBroker]:
    """買 1 口並成交。"""
    engine, broker = engine_with({0: Side.BUY}, config)
    for i in range(3):
        engine.on_tick(tick(i))
    assert engine.ledger.position == 1
    return engine, broker


def kinds(engine: Engine) -> list[str]:
    return [e.kind for e in engine.events]


def brief(results: list[CommandResult]) -> list[tuple[int, str, str]]:
    return [(r.command_id, r.stage, r.code) for r in results]


# ---- AC1 只認得停止、只管今天；同一筆只處理一次


def test_ac1_only_todays_stop_commands_apply_once_in_id_order() -> None:
    engine, _ = engine_with({})
    yesterday = TODAY - timedelta(days=1)
    # D-229 之後 resume 是認得的動作（只解除「暫停」，test_a016 守著它不會解除停手）；這裡換一個真的不認得的
    batch = [stop(3, 5), stop(2, 4), stop(5, 6, day=yesterday), stop(6, 7, action="release")]
    results = engine.on_control(batch, at(10))
    assert brief(results) == [
        (2, STAGE_ACK, RESULT_FLAT),
        (3, STAGE_ACK, RESULT_FLAT),
        (5, STAGE_ACK, RESULT_IGNORED),
        (6, STAGE_ACK, RESULT_IGNORED),
        (2, STAGE_DONE, RESULT_FLAT),
        (3, STAGE_DONE, RESULT_FLAT),
    ]
    assert engine.risk.halt == HALT_KILL and engine.last_command_id == 6
    assert "不是今天的" in results[2].note and "沒有執行" in results[2].note
    assert "不認得" in results[3].note and "沒有執行" in results[3].note
    control = [e for e in engine.events if e.kind == "control"]
    assert [e.data["command_id"] for e in control] == [2, 3, 5, 6]
    assert [e.data["applied"] for e in control] == [True, True, False, False]
    # 同一批再送來（回報還沒送到雲端之前，中繼程式每一輪都會再給一次）：不重做、不重記、不重報
    before = len(engine.events)
    assert engine.on_control(batch, at(11)) == [] and len(engine.events) == before


def test_ac1_same_id_with_another_press_time_is_a_new_command() -> None:
    # 指令表被重建過、編號從頭來：用「編號＋按下的時間」認，不用「編號比上次大」
    engine, _ = engine_with({})
    engine.on_control([stop(2, 4)], at(10))
    engine.release_kill(at(11))
    engine.on_control([], at(12))
    assert engine.risk.halt is None
    results = engine.on_control([stop(2, 30), stop(1, 31)], at(40))
    assert brief(results)[:2] == [(1, STAGE_ACK, RESULT_FLAT), (2, STAGE_ACK, RESULT_FLAT)]
    assert engine.risk.halt == HALT_KILL


def test_ac1_command_validates_time_and_id() -> None:
    with pytest.raises(ValueError, match="時區"):
        Command(id=1, action=ACTION_STOP, day=TODAY, created_at=datetime(2026, 11, 13, 9, 0))
    with pytest.raises(ValueError, match="編號"):
        Command(id=0, action=ACTION_STOP, day=TODAY, created_at=T0)
    engine, _ = engine_with({})
    with pytest.raises(ValueError, match="時區"):
        engine.on_control([stop(1)], datetime(2026, 11, 13, 9, 0))


# ---- AC2 停止怎麼做：先問券商、先停手再記指令、平倉


def test_ac2_stop_halts_then_flattens_and_journals_halt_before_command() -> None:
    engine, broker = long_one()
    results = engine.on_control([stop(7, 1)], at(3))
    assert engine.risk.halt == HALT_KILL
    assert results == [
        CommandResult(
            7,
            STAGE_ACK,
            RESULT_FLATTENING,
            "已停手，已送出平倉；帳上多 1 口（09:00:01 按下，09:00:03 執行）",
        )
    ]
    assert broker.submitted[-1].side is Side.SELL and broker.submitted[-1].qty == 1
    # 日誌的順序：先有「停手」，才有「這筆指令處理過了」——中間當機的話，重啟後是「還沒處理」，不會是「處理了卻沒停」
    i = kinds(engine).index("halt")
    assert kinds(engine)[i:] == ["halt", "order", "control"]
    event = engine.events[-1]
    assert "第 7 號" in event.text and "09:00:01 按下" in event.text
    assert event.data == {
        "command_id": 7,
        "created_at": "2026-11-13T09:00:01+08:00",
        "action": "stop",
        "applied": True,
    }
    for s in range(4, 8):
        engine.on_tick(tick(s))
    assert engine.ledger.position == 0 and engine.risk.halt == HALT_KILL
    # 停手之後策略再想買也送不出去
    engine.strategy.plan[engine.strategy.seen] = Side.BUY  # type: ignore[attr-defined]
    engine.on_tick(tick(9))
    assert engine.ledger.position == 0 and kinds(engine)[-1] == "reject"


def test_ac2_broker_already_flat_sends_no_order() -> None:
    # 停止晚到，人已經照頁面的提醒用券商的 App 平掉了：帳上還是 +1，券商是 0。不可以再賣一口變成放空
    engine, broker = long_one()
    broker.drift = -1
    sent = len(broker.submitted)
    results = engine.on_control([stop(8, 3)], at(900))
    assert len(broker.submitted) == sent
    assert engine.ledger.position == 0 and engine.risk.halt == HALT_KILL
    assert brief(results) == [(8, STAGE_ACK, RESULT_FLAT), (8, STAGE_DONE, RESULT_FLAT)]
    assert results[0].note.startswith(
        "券商的部位跟帳上不一樣，已改用券商的數字；已停手；帳上沒有部位"
    )
    assert "09:00:03 按下，09:15:00 執行" in results[0].note  # 晚了多久看得出來
    assert "correction" in kinds(engine)


def test_ac2_stop_cancels_working_entry_order() -> None:
    engine, broker = engine_with({0: Side.BUY})
    engine.on_tick(tick(0))  # 買單送出、還沒成交
    results = engine.on_control([stop(1)], at(0.1))
    assert brief(results) == [(1, STAGE_ACK, RESULT_CANCELLING)]
    assert results[0].note.startswith("已停手，已送出刪單；帳上沒有部位")
    assert len(broker.cancelled) == 1
    engine.on_tick(tick(1))
    assert brief(engine.on_control([], at(2))) == [(1, STAGE_DONE, RESULT_FLAT)]


def test_ac2_result_says_what_really_happened() -> None:
    engine, broker = long_one()
    engine.on_disconnect(at(3))
    sent = len(broker.submitted)
    results = engine.on_control([stop(2)], at(4))
    assert brief(results) == [(2, STAGE_ACK, RESULT_LINK_DOWN)]
    assert results[0].note.startswith("已停手；券商連線中斷，恢復後才會平倉；帳上多 1 口")
    assert len(broker.submitted) == sent  # 斷線中不送單

    engine, broker = long_one()
    engine.risk.halt = HALT_BROKER_REJECT
    sent = len(broker.submitted)
    results = engine.on_control([stop(3)], at(4))
    assert brief(results) == [(3, STAGE_ACK, RESULT_SUSPENDED), (3, STAGE_DONE, RESULT_STUCK)]
    assert results[0].note.startswith("程式在暫停送單，不會自動平倉，請手動處理；帳上多 1 口")
    assert "請手動處理" in results[1].note and "帳上多 1 口" in results[1].note
    assert engine.risk.halt == HALT_BROKER_REJECT and len(broker.submitted) == sent


# ---- AC3 重啟：只還原今天處理過的；補報；解除過的不會再被停


def test_ac3_restart_keeps_todays_commands_and_reports_again(tmp_path: Path) -> None:
    config = cfg()
    path = tmp_path / "journal.jsonl"
    log = Journal(path)
    engine = Engine(config, Scripted({}), SimBroker(config.sim), sink=log.write)
    engine.on_tick(tick(0))
    assert brief(engine.on_control([stop(7)], at(1))) == [
        (7, STAGE_ACK, RESULT_FLAT),
        (7, STAGE_DONE, RESULT_FLAT),
    ]
    log.close()

    # 還停著的時候重啟：停手還在，指令不重做，但結果再報一次（當機前可能來不及報）
    restored = recover(read_journal(path), config, TODAY)
    assert restored.stops_today == ((7, "2026-11-13T09:00:00+08:00", True),)
    again = Engine(config, Scripted({}), SimBroker(config.sim))
    again.restore(restored)
    results = again.on_control([stop(7)], at(10))
    assert again.risk.halt == HALT_KILL
    assert brief(results) == [(7, STAGE_ACK, RESULT_FLAT), (7, STAGE_DONE, RESULT_FLAT)]
    assert "重新啟動後補報" in results[0].note
    assert [e.kind for e in again.events] == []  # 沒有重做，所以沒有新事件


def test_ac3_released_stop_does_not_stop_again_after_restart(tmp_path: Path) -> None:
    config = cfg()
    path = tmp_path / "journal.jsonl"
    log = Journal(path)
    engine = Engine(config, Scripted({}), SimBroker(config.sim), sink=log.write)
    engine.on_tick(tick(0))
    engine.on_control([stop(7)], at(1))
    engine.release_kill(at(3))  # 人在電腦前解除
    assert brief(engine.on_control([stop(7)], at(4))) == [(7, STAGE_DONE, RESULT_RELEASED)]
    log.close()

    restored = recover(read_journal(path), config, TODAY)
    assert restored.halt is None
    again = Engine(config, Scripted({}), SimBroker(config.sim))
    again.restore(restored)
    results = again.on_control([stop(7)], at(10))
    assert again.risk.halt is None  # 同一天的舊指令不會再把它停一次
    assert (7, STAGE_DONE, RESULT_RELEASED) in brief(results)
    assert brief(again.on_control([stop(8, 20)], at(21)))[0] == (8, STAGE_ACK, RESULT_FLAT)
    assert again.risk.halt == HALT_KILL  # 新的停止照樣有效


def test_ac3_yesterdays_commands_are_not_carried_over(tmp_path: Path) -> None:
    config = cfg()
    path = tmp_path / "journal.jsonl"
    log = Journal(path)
    engine = Engine(config, Scripted({}), SimBroker(config.sim), sink=log.write)
    engine.on_tick(tick(0))
    engine.on_control([stop(500)], at(1))
    engine.release_kill(at(2))
    log.close()
    tomorrow = TODAY + timedelta(days=1)
    restored = recover(read_journal(path), config, tomorrow)
    assert restored.stops_today == ()
    again = Engine(config, Scripted({}), SimBroker(config.sim))
    again.restore(restored)
    # 隔天編號比較小的新指令照樣執行（不會因為昨天處理到 500 號就被當成舊的）
    pressed = Command(id=3, action=ACTION_STOP, day=tomorrow, created_at=at(86400))
    results = again.on_control([pressed], at(86400 + 5))
    assert brief(results)[0] == (3, STAGE_ACK, RESULT_FLAT) and again.risk.halt == HALT_KILL


def test_ac3_new_day_forgets_handled_commands_without_restart() -> None:
    engine, _ = engine_with({})
    engine.on_control([stop(4)], at(1))
    assert engine.last_command_id == 4
    engine.on_control([], at(86400))
    assert engine.last_command_id == 0


# ---- AC4 只能叫停


def test_ac4_control_can_only_stop_never_unhalt() -> None:
    rng = random.Random(11)
    halts = [None, HALT_KILL, HALT_MISMATCH, HALT_DAILY_LOSS, HALT_ORDER_RATE, HALT_BROKER_REJECT]
    actions = [ACTION_STOP, "resume", "release", "start", "", "STOP"]
    for _ in range(300):
        engine, _ = engine_with({})
        before = rng.choice(halts)
        engine.risk.halt = before
        commands = [
            stop(
                rng.randint(1, 50),
                rng.randint(0, 5),
                action=rng.choice(actions),
                day=TODAY - timedelta(days=rng.choice([0, 0, 0, 1])),
            )
            for _ in range(rng.randint(0, 6))
        ]
        engine.on_control(commands, at(10))
        engine.on_control(commands, at(40))
        if before is not None:
            assert engine.risk.halt is not None, (before, commands)
        if before in (HALT_ORDER_RATE, HALT_BROKER_REJECT):
            assert engine.risk.halt == before  # 暫停送單不會被降級成一般停手
        if before is None and engine.risk.halt is not None:
            assert engine.risk.halt == HALT_KILL
            assert any(c.action == ACTION_STOP and c.day == TODAY for c in commands)
    # 這條路上沒有任何解除的入口：兩個模組的原始碼都不碰 release
    for module in (control_module, relay_module):
        source = Path(str(module.__file__)).read_text(encoding="utf-8")
        assert "release_kill(" not in source and ".halt = None" not in source


# ---- AC5 追蹤到確認平掉；平不掉要講


def test_ac5_follows_a_stop_until_confirmed_flat() -> None:
    engine, _ = long_one()
    assert brief(engine.on_control([stop(7)], at(3))) == [(7, STAGE_ACK, RESULT_FLATTENING)]
    assert engine.on_control([], at(3.5)) == []  # 還在平，不重複回報
    for s in range(4, 8):
        engine.on_tick(tick(s))
    done = engine.on_control([], at(8))
    assert done == [
        CommandResult(7, STAGE_DONE, RESULT_FLAT, "已確認沒有部位、沒有委託；帳上沒有部位")
    ]
    assert engine.on_control([], at(10)) == []  # 確認過就不再報


def test_ac5_reports_stuck_after_timeout_then_flat_when_it_recovers() -> None:
    engine, broker = long_one()
    engine.on_disconnect(at(3))
    assert brief(engine.on_control([stop(9)], at(4))) == [(9, STAGE_ACK, RESULT_LINK_DOWN)]
    assert engine.on_control([], at(4 + CONTROL.flatten_timeout_s)) == []
    stuck = engine.on_control([], at(4 + CONTROL.flatten_timeout_s + 1))
    assert brief(stuck) == [(9, STAGE_DONE, RESULT_STUCK)]
    assert "過了 21 秒還沒確認平掉，請手動處理；帳上多 1 口" in stuck[0].note
    assert engine.on_control([], at(40)) == []  # 講過一次就不再重複
    # AC6：連線一恢復就平倉，不用等行情
    sent = len(broker.submitted)
    engine.on_reconnect(at(41))
    assert len(broker.submitted) == sent + 1 and broker.submitted[-1].side is Side.SELL
    for s in range(42, 46):
        engine.on_tick(tick(s))
    assert brief(engine.on_control([], at(46))) == [(9, STAGE_DONE, RESULT_FLAT)]


def test_ac5_link_down_with_empty_book_is_not_called_flat_until_broker_confirms() -> None:
    engine, _ = engine_with({})
    engine.on_tick(tick(0))
    engine.on_disconnect(at(1))
    assert brief(engine.on_control([stop(5)], at(2))) == [(5, STAGE_ACK, RESULT_LINK_DOWN)]
    engine.on_reconnect(at(3))
    assert brief(engine.on_control([], at(4))) == [(5, STAGE_DONE, RESULT_FLAT)]


def test_ac5_release_at_the_computer_is_reported() -> None:
    engine, _ = engine_with({})
    engine.on_control([stop(6)], at(1))
    engine.release_kill(at(5))
    assert engine.on_control([stop(6)], at(6)) == [
        CommandResult(6, STAGE_DONE, RESULT_RELEASED, "已於 09:00:05 在電腦上解除，程式可以再交易")
    ]
    assert engine.risk.halt is None and engine.on_control([stop(6)], at(8)) == []


# ---- AC6 不靠行情也做得完


def test_ac6_timer_flattens_without_ticks() -> None:
    engine, broker = long_one()
    sent = len(broker.submitted)
    engine.on_timer(at(10))
    assert len(broker.submitted) == sent  # 沒事的時候不動
    engine.risk.halt = HALT_MISMATCH
    engine.on_timer(at(11))
    assert len(broker.submitted) == sent + 1 and broker.submitted[-1].side is Side.SELL
    engine.on_timer(at(12))
    assert len(broker.submitted) == sent + 1  # 平倉單還在途，不重複送

    engine, broker = long_one()
    flat_time = engine.cfg.risk.force_flat_time
    before = datetime.combine(TODAY, flat_time, TAIPEI) - timedelta(seconds=1)
    sent = len(broker.submitted)
    engine.on_timer(before)
    assert len(broker.submitted) == sent
    engine.on_timer(before + timedelta(seconds=1))  # 行情停住，強制平倉時間到了照樣平
    assert [o.side for o in broker.submitted[sent:]] == [Side.SELL]
    with pytest.raises(ValueError, match="時區"):
        engine.on_timer(datetime.combine(TODAY, time(10, 0)))


# ---- AC7 讀不到指令不影響交易


@dataclass
class FakeSource:
    """測試用的指令來源：可以放指令、可以壞掉。"""

    commands: list[Command] = field(default_factory=list)
    reports: list[CommandResult] = field(default_factory=list)
    polls: list[datetime] = field(default_factory=list)
    fail_poll: bool = False
    fail_report: bool = False

    def poll(self, now: datetime) -> list[Command]:
        self.polls.append(now)
        if self.fail_poll:
            raise ControlError("測試：指令檔讀不到")
        return list(self.commands)

    def report(self, result: CommandResult, now: datetime) -> None:
        if self.fail_report:
            raise ControlError("測試：回報檔寫不了")
        self.reports.append(result)


def test_ac7_source_failure_leaves_engine_untouched_and_logs_once() -> None:
    engine, broker = long_one()
    source = FakeSource(fail_poll=True)
    loop = ControlLoop(engine, source, poll_s=2.0)
    events_before, sent_before = len(engine.events), len(broker.submitted)
    for s in range(3, 13):
        loop.step(at(s))
    assert engine.ledger.position == 1 and engine.risk.halt is None
    assert len(broker.submitted) == sent_before
    new = engine.events[events_before:]
    assert [(e.kind, e.data["up"]) for e in new] == [("control_link", False)]
    assert loop.state == ControlState(listening=True, ok=False, checked_at=None, poll_s=2.0)

    source.fail_poll = False
    loop.step(at(14))
    loop.step(at(16))
    new = engine.events[events_before:]
    assert [(e.kind, e.data["up"]) for e in new] == [
        ("control_link", False),
        ("control_link", True),
    ]
    assert loop.state == ControlState(listening=True, ok=True, checked_at=at(16), poll_s=2.0)


def test_ac7_still_follows_earlier_stop_while_commands_cannot_be_read() -> None:
    engine, _ = long_one()
    engine.on_disconnect(at(3))
    source = FakeSource(commands=[stop(4)])
    loop = ControlLoop(engine, source, poll_s=2.0)
    loop.step(at(4))
    assert brief(source.reports) == [(4, STAGE_ACK, RESULT_LINK_DOWN)]
    source.fail_poll = True
    loop.step(at(30))
    assert brief(source.reports) == [
        (4, STAGE_ACK, RESULT_LINK_DOWN),
        (4, STAGE_DONE, RESULT_STUCK),
    ]


def test_ac7_result_that_cannot_be_written_is_retried_not_lost() -> None:
    engine, _ = long_one()
    source = FakeSource(commands=[stop(4)], fail_report=True)
    loop = ControlLoop(engine, source, poll_s=2.0)
    loop.step(at(3))
    assert engine.risk.halt == HALT_KILL and engine.last_command_id == 4
    links = [e for e in engine.events if e.kind == "control_link"]
    assert len(links) == 1 and "記不下來" in links[0].text
    loop.step(at(5))
    loop.step(at(7))  # 還是寫不了：不重複記事件
    assert len([e for e in engine.events if e.kind == "control_link"]) == 1
    source.fail_report = False
    loop.step(at(9))  # 好了之後補上，而且只記一次
    assert brief(source.reports) == [(4, STAGE_ACK, RESULT_FLATTENING)]
    loop.step(at(11))
    assert len(source.reports) == 1
    assert [e.data["up"] for e in engine.events if e.kind == "control_link"] == [False, True]


def test_ac7_polls_on_the_clock_without_any_tick() -> None:
    engine, _ = engine_with({})
    source = FakeSource()
    loop = ControlLoop(engine, source, poll_s=2.0)
    for i in range(21):  # 10 秒、每 0.5 秒叫一次，完全沒有行情
        loop.step(at(i * 0.5))
    assert source.polls == [at(s) for s in (0, 2, 4, 6, 8, 10)]
    source.commands.append(stop(9, 10))
    loop.step(at(12))
    assert engine.risk.halt == HALT_KILL
    assert brief(source.reports) == [(9, STAGE_ACK, RESULT_FLAT), (9, STAGE_DONE, RESULT_FLAT)]
    with pytest.raises(ValueError, match="時區"):
        loop.step(datetime(2026, 11, 13, 9, 1))


# ---- AC8 交易程式這一側只碰本機檔案


def write_inbox(folder: Path, written: datetime, commands: list[Command], **extra: object) -> None:
    payload: dict[str, object] = {
        "written_at": written.isoformat(),
        "ok": True,
        "error": None,
        "commands": [command_to_json(c) for c in commands],
    }
    write_json_atomic(folder / INBOX_NAME, {**payload, **extra})


def test_ac8_file_source_reads_commands_and_leaves_a_heartbeat(tmp_path: Path) -> None:
    source = FileControl(tmp_path, stale_s=CONTROL.stale_s)
    with pytest.raises(ControlError, match="還沒有指令檔"):
        source.poll(at(0))
    write_inbox(tmp_path, at(0), [stop(1, -5), stop(2, -1)])
    assert source.poll(at(1)) == [stop(1, -5), stop(2, -1)]
    assert (tmp_path / HEARTBEAT_NAME).read_text(encoding="utf-8") == at(1).isoformat()
    assert not list(tmp_path.glob("*.tmp"))


def test_ac8_stale_broken_or_failing_inbox_counts_as_unreadable(tmp_path: Path) -> None:
    source = FileControl(tmp_path, stale_s=CONTROL.stale_s)
    write_inbox(tmp_path, at(0), [])
    assert source.poll(at(CONTROL.stale_s)) == []
    with pytest.raises(ControlError, match="11 秒沒更新"):
        source.poll(at(CONTROL.stale_s + 1))  # 中繼程式沒在跑
    write_inbox(tmp_path, at(20), [], ok=False, error="connection timeout")
    with pytest.raises(ControlError, match="中繼程式讀不到雲端的指令：connection timeout"):
        source.poll(at(21))
    for broken in ("", "[]", '{"ok": true}', '{"written_at": "2026-11-13T09:00:00", "ok": true}'):
        (tmp_path / INBOX_NAME).write_text(broken, encoding="utf-8")
        with pytest.raises(ControlError, match="指令檔的內容不對"):
            source.poll(at(21))


def test_ac8_results_are_appended_one_line_each(tmp_path: Path) -> None:
    source = FileControl(tmp_path, stale_s=CONTROL.stale_s)
    first = CommandResult(7, STAGE_ACK, RESULT_FLATTENING, "已停手，已送出平倉；帳上多 1 口")
    second = CommandResult(7, STAGE_DONE, RESULT_FLAT, "已確認沒有部位、沒有委託；帳上沒有部位")
    source.report(first, at(3))
    source.report(second, at(8))
    text = (tmp_path / outbox_name(TODAY)).read_text(encoding="utf-8")
    assert text.endswith("\n") and outbox_name(TODAY) == "results_2026-11-13.jsonl"
    rows = [json.loads(line) for line in text.splitlines()]
    assert [result_from_json(r) for r in rows] == [first, second]
    assert rows[0]["at"] == "2026-11-13T09:00:03+08:00" and "已送出平倉" in text  # 中文不轉義
    with pytest.raises(ControlError, match="寫不了回報檔"):
        FileControl(tmp_path / "nope", stale_s=1.0).report(first, at(3))


def test_ac8_engine_side_never_touches_the_network() -> None:
    source = Path(str(control_module.__file__)).read_text(encoding="utf-8")
    for word in ("psycopg", "socket", "importlib", "urllib", "http"):
        assert word not in source, word


# ---- AC9 狀態看得到


def test_ac9_snapshot_shows_control_state() -> None:
    engine, _ = engine_with({})
    engine.on_tick(tick(0))
    off = snapshot(engine, at(0), source="replay", status="closed", strategy="x")
    assert off["control"] == {
        "listening": False,
        "ok": None,
        "checked_at": None,
        "poll_s": None,
        "last_command_id": 0,
    }

    loop = ControlLoop(engine, FakeSource(commands=[stop(3)]), poll_s=2.0)
    loop.step(at(1))
    live = snapshot(engine, at(1), source="live", status="live", strategy="x", control=loop.state)
    assert live["control"] == {
        "listening": True,
        "ok": True,
        "checked_at": "2026-11-13T09:00:01+08:00",
        "poll_s": 2.0,
        "last_command_id": 3,
    }
    assert live["state"] == "halted" and live["halt"] == HALT_KILL
    assert {e["kind"] for e in live["events"]} >= {"control", "halt"}


def test_ac9_flapping_link_does_not_push_real_events_out() -> None:
    engine, _ = engine_with({}, cfg(max_events=3))
    engine.on_tick(tick(0))
    engine.kill(at(1))
    for i in range(6):
        engine.note_control_link(at(2 + i), up=i % 2 == 1, detail=f"第 {i} 次")
    events = snapshot(engine, at(9), source="live", status="live", strategy="x")["events"]
    assert [e["kind"] for e in events] == ["halt", "control_link", "control_link"]
    assert [e["text"] for e in events[1:]] == ["第 4 次", "第 5 次"]


# ---- AC10 設定


def test_ac10_config_validates_and_live_requires_control() -> None:
    import tomllib

    with CONFIG_PATH.open("rb") as fh:
        raw = tomllib.load(fh)
    assert parse_config(raw).control == ControlConfig(**raw["control"])
    good = raw["control"]
    for key, bad in (("poll_s", 0), ("poll_s", -1), ("flatten_timeout_s", 0)):
        with pytest.raises(ConfigError, match="要大於 0"):
            parse_config({**raw, "control": {**good, key: bad}})
    with pytest.raises(ConfigError, match="stale_s"):
        parse_config({**raw, "control": {**good, "stale_s": good["poll_s"] * 2 - 0.1}})
    for missing in good:
        with pytest.raises(ConfigError, match=missing):
            parse_config({**raw, "control": {k: v for k, v in good.items() if k != missing}})
    without = {k: v for k, v in raw.items() if k != "control"}
    assert parse_config(without).control is None
    live_risk = {
        **raw["risk"],
        "daily_loss_limit_twd": 3000,
        "max_orders_per_minute": 10,
        "max_consecutive_broker_rejects": 3,
        "position_stop_loss_twd": 1500,
    }
    assert parse_config({**raw, "mode": "live", "risk": live_risk}).mode == "live"
    with pytest.raises(ConfigError, match=r"\[control\]"):
        parse_config({**without, "mode": "live", "risk": live_risk})


# ---- 第二輪（程式）審查之後補的


def test_review_results_carry_the_press_time_through_the_file(tmp_path: Path) -> None:
    engine, _ = engine_with({})
    results = engine.on_control([stop(7, 3)], at(5))
    assert {r.created_at for r in results} == {at(3)}
    source = FileControl(tmp_path, stale_s=CONTROL.stale_s)
    for r in results:
        source.report(r, at(5))
    rows = [
        json.loads(line)
        for line in (tmp_path / outbox_name(TODAY)).read_text(encoding="utf-8").splitlines()
    ]
    assert {row["created_at"] for row in rows} == {"2026-11-13T09:00:03+08:00"}
    assert [result_from_json(row).created_at for row in rows] == [at(3), at(3)]


def test_review_torn_last_line_does_not_swallow_the_next_result(tmp_path: Path) -> None:
    (tmp_path / outbox_name(TODAY)).write_text('{"command_id": 7, "sta', encoding="utf-8")
    source = FileControl(tmp_path, stale_s=CONTROL.stale_s)
    result = CommandResult(7, STAGE_ACK, RESULT_FLAT, "已停手；帳上沒有部位", at(0))
    source.report(result, at(1))
    lines = (tmp_path / outbox_name(TODAY)).read_text(encoding="utf-8").splitlines()
    assert lines[0] == '{"command_id": 7, "sta' and result_from_json(json.loads(lines[1])) == result


def test_review_atomic_write_retries_when_the_reader_holds_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = os.replace
    calls: list[int] = []

    def flaky(src: str, dst: str) -> None:
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(5, "存取被拒")
        real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    monkeypatch.setattr(time_module, "sleep", lambda s: None)
    write_json_atomic(tmp_path / "x.json", {"a": 1})
    assert len(calls) == 3 and json.loads((tmp_path / "x.json").read_text(encoding="utf-8")) == {
        "a": 1
    }
    calls.clear()

    def always_blocked(src: str, dst: str) -> None:
        raise PermissionError(5, "存取被拒")

    monkeypatch.setattr(os, "replace", always_blocked)
    with pytest.raises(PermissionError):
        write_json_atomic(tmp_path / "y.json", {"a": 1})


@dataclass
class PositionFails(FlakyBroker):
    """查部位會出錯的測試券商（之後接元大時，查詢逾時或元件出錯）。"""

    def position(self) -> int:
        raise RuntimeError("測試：查部位逾時")


def test_review_stop_halts_even_when_the_position_query_fails() -> None:
    config = cfg()
    engine = Engine(config, Scripted({}), PositionFails(SimBroker(config.sim)))
    engine.on_tick(tick(0))
    with pytest.raises(RuntimeError, match="查部位逾時"):
        engine.kill(at(1))
    assert engine.risk.halt == HALT_KILL  # 查部位出錯之前就已經停手了
    assert kinds(engine)[-1] == "halt"


def test_review_release_after_a_corrected_book_does_not_claim_trading_resumes() -> None:
    engine, broker = long_one()
    broker.drift = -1  # 人已經用券商的 App 平掉了
    engine.on_control([stop(8, 3)], at(4))
    engine.release_kill(at(10))
    (done,) = engine.on_control([], at(11))
    assert (done.stage, done.code) == (STAGE_DONE, RESULT_RELEASED)
    assert "策略已經關閉，要重新啟動程式才會再交易" in done.note
    plain_engine, _ = engine_with({})
    plain_engine.on_control([stop(9)], at(1))
    plain_engine.release_kill(at(2))
    assert "程式可以再交易" in plain_engine.on_control([], at(3))[-1].note


def test_review_timer_does_not_cancel_orders_on_the_wall_clock() -> None:
    # 電腦的時鐘比行情的時間快 11 秒：用時鐘叫的 on_timer 不可以把剛送出的委託當成逾時
    engine, broker = engine_with({0: Side.BUY})
    engine.on_tick(tick(0))
    engine.on_timer(at(11))
    assert broker.cancelled == [] and engine.risk.halt is None
