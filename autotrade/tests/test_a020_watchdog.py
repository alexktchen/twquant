"""A-020：獨立看門程式（D-231 補充）。使用者 2026-11-14 選「看門程式跟 agent 本體一起做」。

盯交易程式的心跳、盤中行情、部位，以及「遠端緊急停止按了、主程式卻沒回報」。出事就要交易程式停手並通知。
"""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

from autotrade.cli import main
from autotrade.config import Config, ConfigError, WatchdogConfig, load_config, parse_config
from autotrade.control import (
    HEARTBEAT_NAME,
    FileControl,
    outbox_name,
    read_local_stop,
    release_name,
    result_to_json,
    write_local_stop,
)
from autotrade.model import (
    ACTION_FLATTEN,
    ACTION_STOP,
    RESULT_FLAT,
    RESULT_FLATTENING,
    RESULT_IGNORED,
    RESULT_SUSPENDED,
    STAGE_ACK,
    STAGE_DONE,
    Command,
    CommandResult,
)
from autotrade.risk import HALT_KILL
from autotrade.runner import earlier_stop
from autotrade.watchdog import SimWatchBroker, Watchdog
from test_a011_drill import FakeClock, write_inbox
from test_a014_runner import CONFIG_PATH, DAY, Rig, journal_fills
from test_a014_runner import at as clock_at

WATCH = WatchdogConfig(check_s=2.0, heartbeat_stale_s=60.0, tick_stale_s=90.0, stop_grace_s=10.0)


def config() -> Config:
    return replace(load_config(CONFIG_PATH), watchdog=WATCH)


@dataclass
class FakeBroker:
    at_broker: int | None = None
    flattened: list[str] = field(default_factory=list)

    def position(self) -> int | None:
        return self.at_broker

    def flatten(self, why: str) -> tuple[str, str]:
        self.flattened.append(why)
        return RESULT_FLATTENING, "已向券商送出平倉"


@dataclass
class World:
    """交易程式的資料夾，加上看門程式自己的那一個。"""

    folder: Path
    broker: FakeBroker = field(default_factory=FakeBroker)
    alerts: list[tuple[datetime, str, list[str]]] = field(default_factory=list)
    said: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        (self.folder / "watchdog").mkdir()
        self.ticks = self.folder / "ticks.csv"
        self.status = self.folder / "status.json"

    def dog(self) -> Watchdog:
        def alert(ts: datetime, head: str, lines: Sequence[str]) -> None:
            self.alerts.append((ts, head, list(lines)))

        return Watchdog(
            config(),
            self.folder,
            self.ticks,
            self.status,
            self.broker,
            FileControl(self.folder / "watchdog", 10.0),
            alert,
            say=self.said.append,
        )

    def touch(self, path: Path, when: datetime, text: str = "x") -> None:
        path.write_text(text, encoding="utf-8")
        os.utime(path, (when.timestamp(), when.timestamp()))

    def healthy(self, now: datetime, position: int = 0) -> None:
        self.touch(self.folder / HEARTBEAT_NAME, now)
        self.touch(self.ticks, now)
        self.touch(self.status, now, json.dumps({"position": position}))
        write_inbox(self.folder / "watchdog", now, [])


def stop_file(world: World) -> tuple[datetime, str] | None:
    return read_local_stop(world.folder, DAY)


# ---- AC1 心跳


def test_ac1_a_silent_trader_in_session_is_stopped_once_and_someone_is_told(tmp_path: Path) -> None:
    world = World(tmp_path)
    dog = world.dog()
    world.healthy(clock_at(9, 0))
    dog.step(clock_at(9, 0, 30))
    assert world.alerts == [] and stop_file(world) is None
    write_inbox(tmp_path / "watchdog", clock_at(9, 1, 30), [])
    world.touch(world.ticks, clock_at(9, 1, 30))
    dog.step(clock_at(9, 1, 30))  # 心跳 90 秒沒動（上限 60）
    assert len(world.alerts) == 1
    _, head, lines = world.alerts[0]
    assert head == "交易程式沒有心跳" and "90 秒沒有動靜" in lines[0]
    assert "要在電腦上解除" in lines[-1]
    assert world.broker.flattened == ["交易程式沒有心跳"]
    asked = stop_file(world)
    assert asked is not None and asked == (clock_at(9, 1, 30), "交易程式沒有心跳")
    dog.step(clock_at(9, 1, 32))  # 同一個狀況不重複通知、不重寫停手檔
    assert len(world.alerts) == 1 and stop_file(world) == asked
    world.touch(tmp_path / HEARTBEAT_NAME, clock_at(9, 2, 0))
    world.touch(world.ticks, clock_at(9, 2, 0))
    write_inbox(tmp_path / "watchdog", clock_at(9, 2, 0), [])
    dog.step(clock_at(9, 2, 1))
    assert any("這個狀況解除了（停手還在，要人解除）" in line for line in world.said)
    assert stop_file(world) == asked  # 心跳回來不等於解除


def test_ac1_nothing_is_watched_outside_the_session(tmp_path: Path) -> None:
    world = World(tmp_path)
    dog = world.dog()
    write_inbox(tmp_path / "watchdog", clock_at(8, 40), [])
    dog.step(clock_at(8, 40))  # 還沒開盤：沒有心跳檔也不算出事
    write_inbox(tmp_path / "watchdog", clock_at(13, 50), [])
    dog.step(clock_at(13, 50))
    assert world.alerts == [] and stop_file(world) is None
    write_inbox(tmp_path / "watchdog", clock_at(8, 45, 5), [])
    dog.step(clock_at(8, 45, 5))  # 開盤了、連心跳檔都沒有
    assert [a[1] for a in world.alerts] == ["交易程式沒有心跳"]
    assert "找不到交易程式的心跳" in world.alerts[0][2][0]


# ---- AC2 行情


def test_ac2_stalled_ticks_stop_the_trader_but_a_day_without_any_is_not_an_alarm(
    tmp_path: Path,
) -> None:
    world = World(tmp_path)
    dog = world.dog()
    now = clock_at(9, 0)
    world.touch(tmp_path / HEARTBEAT_NAME, now)
    write_inbox(tmp_path / "watchdog", now, [])
    dog.step(now)  # 假日：開盤了、整天沒有行情（沒有狀態檔）——不是看門程式的事
    assert world.alerts == []
    world.healthy(now)
    later = clock_at(9, 1, 40)  # 行情 100 秒沒有新的（上限 90）
    world.touch(tmp_path / HEARTBEAT_NAME, later)
    write_inbox(tmp_path / "watchdog", later, [])
    dog.step(later)
    assert [a[1] for a in world.alerts] == ["盤中行情停住了"]
    assert "100 秒沒有新的" in world.alerts[0][2][0] and stop_file(world) is not None


def test_ac2_the_first_seconds_after_the_open_wait_for_the_first_tick(tmp_path: Path) -> None:
    world = World(tmp_path)
    dog = world.dog()
    now = clock_at(8, 45, 30)
    world.touch(tmp_path / HEARTBEAT_NAME, now)
    world.touch(world.ticks, clock_at(8, 30))  # 行情中繼開機時建的空檔
    world.touch(world.status, now, json.dumps({"position": 0}))
    write_inbox(tmp_path / "watchdog", now, [])
    dog.step(now)
    assert world.alerts == []


# ---- AC3 部位


def test_ac3_a_position_the_broker_does_not_agree_with_stops_everything(tmp_path: Path) -> None:
    world = World(tmp_path, broker=FakeBroker(at_broker=1))
    dog = world.dog()
    now = clock_at(9, 0)
    world.healthy(now, position=1)
    dog.step(now)
    assert world.alerts == []
    world.healthy(now, position=0)
    dog.step(now)
    assert [a[1] for a in world.alerts] == ["部位跟券商對不上"]
    assert "交易程式帳上 0 口，券商 1 口" in world.alerts[0][2][0]
    assert world.broker.flattened == ["部位跟券商對不上"]


def test_ac3_simulation_says_plainly_what_it_cannot_do(tmp_path: Path) -> None:
    sim = SimWatchBroker()
    assert sim.position() is None  # 沒有另一個帳：模擬不比對部位
    code, did = sim.flatten("x")
    assert (
        code == RESULT_SUSPENDED and "看門程式沒有東西可以平" in did and "已要求交易程式停手" in did
    )


# ---- AC4 遠端緊急停止的備援


def stop_cmd(command_id: int, when: datetime, action: str = ACTION_STOP) -> Command:
    return Command(id=command_id, action=action, day=DAY, created_at=when)


def main_reports(folder: Path, result: CommandResult, when: datetime) -> None:
    with (folder / outbox_name(DAY)).open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(result_to_json(result, when), ensure_ascii=False) + "\n")


def own_results(folder: Path) -> list[tuple[int, str, str, str]]:
    path = folder / "watchdog" / outbox_name(DAY)
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return [(r["command_id"], r["stage"], r["code"], r["note"]) for r in rows]


def test_ac4_when_the_main_program_answers_the_watchdog_only_echoes_it(tmp_path: Path) -> None:
    world = World(tmp_path)
    dog = world.dog()
    pressed = clock_at(9, 0)
    cmd = stop_cmd(7, pressed)
    for seconds in (1, 3):
        now = pressed + timedelta(seconds=seconds)
        world.healthy(now)
        write_inbox(tmp_path / "watchdog", now, [cmd])
        dog.step(now)
    assert own_results(tmp_path) == []  # 還在等主程式
    main_reports(
        tmp_path,
        CommandResult(7, STAGE_ACK, RESULT_FLATTENING, "已停手，已送出平倉", pressed),
        pressed,
    )
    now = pressed + timedelta(seconds=5)
    world.healthy(now)
    write_inbox(tmp_path / "watchdog", now, [cmd])
    dog.step(now)
    main_reports(
        tmp_path, CommandResult(7, STAGE_DONE, RESULT_FLAT, "已確認沒有部位", pressed), now
    )
    dog.step(now + timedelta(seconds=1))
    rows = own_results(tmp_path)
    assert [(r[0], r[1], r[2]) for r in rows] == [
        (7, STAGE_ACK, RESULT_FLATTENING),
        (7, STAGE_DONE, RESULT_FLAT),
    ]
    assert "主程式已經處理，看門程式沒有動作" in rows[0][3]
    assert world.alerts == [] and world.broker.flattened == [] and stop_file(world) is None
    dog.step(now + timedelta(seconds=2))
    assert len(own_results(tmp_path)) == 2  # 報過就不再報


def test_ac4_when_the_main_program_stays_silent_the_watchdog_acts(tmp_path: Path) -> None:
    world = World(tmp_path)
    dog = world.dog()
    pressed = clock_at(9, 0)
    cmd = stop_cmd(7, pressed)
    for seconds in (1, 9, 12):
        now = pressed + timedelta(seconds=seconds)
        world.healthy(now)
        write_inbox(tmp_path / "watchdog", now, [cmd])
        dog.step(now)
    rows = own_results(tmp_path)
    assert [(r[0], r[1], r[2]) for r in rows] == [
        (7, STAGE_ACK, RESULT_FLATTENING),
        (7, STAGE_DONE, RESULT_FLATTENING),
    ]
    assert "主程式沒有回報，由看門程式處理" in rows[0][3]
    assert [a[1] for a in world.alerts] == ["遠端緊急停止：主程式沒有回報，由看門程式處理"]
    assert "11 秒了，交易程式沒有回報收到" in world.alerts[0][2][0]
    assert len(world.broker.flattened) == 1 and stop_file(world) is not None


def test_ac4_the_watchdog_ignores_anything_that_is_not_todays_stop(tmp_path: Path) -> None:
    world = World(tmp_path)
    dog = world.dog()
    now = clock_at(9, 0)
    world.healthy(now)
    yesterday = Command(5, ACTION_STOP, DAY - timedelta(days=1), now - timedelta(days=1))
    write_inbox(tmp_path / "watchdog", now, [yesterday, stop_cmd(6, now, ACTION_FLATTEN)])
    dog.step(now)
    dog.step(now + timedelta(seconds=1))
    assert [(r[0], r[1], r[2]) for r in own_results(tmp_path)] == [
        (5, STAGE_ACK, RESULT_IGNORED),
        (6, STAGE_ACK, RESULT_IGNORED),
    ]
    assert world.alerts == []


def test_ac4_an_unreadable_inbox_is_said_once_and_does_not_end_the_watchdog(tmp_path: Path) -> None:
    world = World(tmp_path)
    dog = world.dog()
    now = clock_at(9, 0)
    world.touch(tmp_path / HEARTBEAT_NAME, now)
    dog.step(now)  # 看門程式自己的中繼還沒寫出指令檔
    dog.step(now + timedelta(seconds=2))
    assert sum("讀不到自己的指令檔" in line for line in world.said) == 1
    world.healthy(now + timedelta(seconds=4))
    dog.step(now + timedelta(seconds=4))
    assert any("指令檔恢復可以讀" in line for line in world.said)


# ---- AC5 停手檔：交易程式活著就馬上停；重開也一樣停；解除只能在電腦前


def test_ac5_a_running_trader_obeys_the_stop_file_and_a_restart_replays_it(tmp_path: Path) -> None:
    asked = clock_at(8, 46, 0)  # InOut：08:45:30 買

    def watchdog_fires(now: datetime) -> None:
        if now >= asked and read_local_stop(tmp_path, DAY) is None:
            write_local_stop(tmp_path, now, "盤中行情停住了")

    first = Rig(tmp_path, FakeClock(clock_at(8, 44, 50)), hook=watchdog_fires)
    one = first.runner().run()
    assert (one.reason, one.halt, one.position) == ("closed", HALT_KILL, 0)
    stopped = journal_fills(tmp_path)
    assert [f[1] for f in stopped] == ["BUY", "SELL"] and stopped[1][0] < clock_at(
        8, 46, 10
    ).isoformat()
    assert any("看門程式要求停手：停手、平倉" in line for line in first.said)
    at_file = read_local_stop(tmp_path, DAY)
    assert at_file is not None and earlier_stop(tmp_path, DAY) == at_file[0]

    second = Rig(tmp_path, FakeClock(clock_at(8, 47, 30)))  # 重開：算到那個時間一樣停手
    two = second.runner().run()
    assert (two.halt, two.position) == (HALT_KILL, 0) and journal_fills(tmp_path) == stopped
    assert any("看門程式要求過停手：盤中行情停住了" in line for line in second.said)

    third = Rig(tmp_path, FakeClock(clock_at(8, 48, 0)))  # 在電腦前解除：照常
    three = third.runner(release_stop=True).run()
    assert three.halt is None and earlier_stop(tmp_path, DAY) is None
    assert (tmp_path / release_name(DAY)).exists()


def test_ac5_a_released_stop_can_be_raised_again_but_an_active_one_is_not_overwritten(
    tmp_path: Path,
) -> None:
    world = World(tmp_path)
    first = clock_at(9, 0)
    write_local_stop(tmp_path, first, "先前的原因")
    dog = world.dog()
    write_inbox(tmp_path / "watchdog", clock_at(9, 5), [])
    dog.step(clock_at(9, 5))  # 沒心跳 → 出事，但今天已經有一份還沒解除的停手檔
    assert len(world.alerts) == 1 and stop_file(world) == (first, "先前的原因")
    (tmp_path / release_name(DAY)).write_text(
        json.dumps({"at": clock_at(9, 6).isoformat()}), encoding="utf-8"
    )
    again = world.dog()  # 看門程式重開（或換了一種狀況）
    write_inbox(tmp_path / "watchdog", clock_at(9, 7), [])
    again.step(clock_at(9, 7))
    assert stop_file(world) == (clock_at(9, 7), "交易程式沒有心跳")


def test_ac5_an_unreadable_stop_file_means_stop(tmp_path: Path) -> None:
    def garbage(now: datetime) -> None:
        if now >= clock_at(8, 46, 0):
            (tmp_path / f"local_stop_{DAY.isoformat()}.json").write_text("{", encoding="utf-8")

    rig = Rig(tmp_path, FakeClock(clock_at(8, 44, 50)), hook=garbage)
    report = rig.runner().run()
    assert report.halt == HALT_KILL and report.position == 0
    assert any("停手檔讀不懂，當成要停" in line for line in rig.said)


# ---- AC6 設定與指令列


def test_ac6_config_is_validated_and_the_program_refuses_what_it_cannot_watch(
    tmp_path: Path,
) -> None:
    raw: dict[str, Any] = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    parsed = parse_config(raw)
    assert parsed.watchdog is not None and parsed.control is not None
    assert parsed.watchdog.heartbeat_stale_s >= 2 * parsed.control.poll_s
    for key, bad in (
        ("check_s", 0),
        ("tick_stale_s", -1),
        ("stop_grace_s", 0),
        ("heartbeat_stale_s", 1),
    ):
        with pytest.raises(ConfigError):
            parse_config({**raw, "watchdog": {**raw["watchdog"], key: bad}})
    # 沒有 [control] 的設定照樣讀得進來（重播用不到看門程式）
    assert parse_config({k: v for k, v in raw.items() if k != "control"}).control is None
    args = ["watchdog", "--config", str(CONFIG_PATH), "--dir", str(tmp_path)]
    args += ["--ticks-file", str(tmp_path / "t.csv"), "--status", str(tmp_path / "s.json")]
    with pytest.raises(FileNotFoundError):  # 沒有看門程式自己的資料夾
        main(args)
    with pytest.raises(ConfigError, match="不能跑看門程式"):
        Watchdog(
            replace(config(), watchdog=None),
            tmp_path,
            tmp_path / "t",
            tmp_path / "s",
            SimWatchBroker(),
            FileControl(tmp_path, 10.0),
            lambda ts, head, lines: None,
        )
    assert time(8, 45) == parsed.session.start  # 看門程式的盯梢時段跟著設定檔的開盤、收盤
