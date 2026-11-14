"""A-019：常駐的交易 agent——啟動／停止是切換待命／運作，指令走訊息匯流排（D-231）。

使用者 2026-11-14：「我要把程式當一個agent一直把訊號打到mq」「這個交易agent也要可以被啟動、停止」
「指令也是透過db mq溝通」；停止選的是「平倉後回待命」。看門程式在 test_a020_watchdog.py。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import IO, Any

import pytest

from autotrade import bus as bus_module
from autotrade.agent import AgentSetup, Child, DayPlan, Supervisor, day_plan
from autotrade.agent import run as run_supervisor
from autotrade.bus import (
    BUS_ACTIONS,
    BUS_BELL_DDL,
    BUS_PENDING_SQL,
    BUS_REPORT_SQL,
    COMMAND_TTL_S,
    MESSAGES_DDL,
    TOPICS,
)
from autotrade.cli import main
from autotrade.config import AgentConfig, ConfigError, parse_config
from autotrade.control import ControlLoop, FileControl
from autotrade.engine import Engine
from autotrade.export import snapshot
from autotrade.model import (
    ACTION_ARM,
    ACTION_STANDBY,
    ACTION_STOP,
    RESULT_ARMED,
    RESULT_FLAT,
    RESULT_FLATTENING,
    STAGE_ACK,
    STAGE_DONE,
    TAIPEI,
    Command,
    CommandResult,
    Side,
)
from autotrade.relay import BELL_DDL, PgRelayDb, Relay
from autotrade.risk import HALT_KILL
from autotrade.runner import QUIT_NAME, earlier_actions
from test_a010_broker_requirements import Scripted
from test_a011_control import CONTROL, TODAY, at, brief, cfg, long_one, tick
from test_a011_drill import FakeClock
from test_a011_relay import FakeConn, FakeCursor, FakeDb, FakeDbError, inbox
from test_a014_runner import CONFIG_PATH, DAY, Rig, journal_fills
from test_a014_runner import at as clock_at
from test_a016_remote import FakeRequests, rig

AGENT = AgentConfig(
    arm_at_open=False,
    trader_start=time(8, 30),
    restart_wait_s=2.0,
    restart_wait_max_s=8.0,
    steady_s=60.0,
)


def cmd(command_id: int, seconds: float, action: str, target: str = "sim") -> Command:
    return Command(id=command_id, action=action, day=TODAY, created_at=at(seconds), target=target)


# ---- AC1 訊息表


def test_ac1_messages_table_is_append_only_and_commands_can_only_arm_or_stand_by() -> None:
    table = MESSAGES_DDL[0]
    assert "CREATE TABLE IF NOT EXISTS web.autotrade_messages" in table
    for topic in TOPICS:
        assert f"'{topic}'" in table
    assert TOPICS == ("command", "report", "signal", "fill", "status")
    # 匯流排上的指令只可能是啟動／停止——沒有緊急停止、沒有解除、沒有買賣
    assert "CHECK (topic <> 'command' OR action IN ('arm', 'standby'))" in table
    assert BUS_ACTIONS == (ACTION_ARM, ACTION_STANDBY) and ACTION_STOP not in BUS_ACTIONS
    assert "nextval('web.autotrade_commands_id_seq')" in table  # 編號跟另外兩張表不會撞
    assert "CHECK (target IN ('sim', 'live'))" in table
    assert any("ENABLE ROW LEVEL SECURITY" in s for s in MESSAGES_DDL)
    for statement in MESSAGES_DDL:  # 可以重複執行，不動既有的表
        assert "IF NOT EXISTS" in statement or "ENABLE ROW LEVEL SECURITY" in statement
    source = Path(str(bus_module.__file__)).read_text(encoding="utf-8")
    for word in ("DELETE FROM", "TRUNCATE", "DROP ", "UPDATE web."):
        assert word not in source
    # 門鈴：只有新的指令才響，用的是 A-015 那個函式
    assert "WHEN (NEW.topic = 'command')" in BUS_BELL_DDL[0]
    assert "web.autotrade_ring()" in BUS_BELL_DDL[0] and "web.autotrade_ring()" in BELL_DDL[0]


def test_ac1_ac2_queries_hand_out_only_fresh_unanswered_commands_for_this_agent() -> None:
    assert "m.day = %s AND m.topic = 'command' AND m.target = %s" in BUS_PENDING_SQL
    assert "COALESCE(m.ttl_s, %s)" in BUS_PENDING_SQL and COMMAND_TTL_S == 30.0
    assert "NOT EXISTS" in BUS_PENDING_SQL and "r.reply_to = m.id" in BUS_PENDING_SQL
    # 回報只寫給編號與按下時間都對得上的那一則指令；「收到」只留第一次
    assert "WHERE m.id = %s AND m.created_at = %s AND m.topic = 'command'" in BUS_REPORT_SQL
    assert "'report'" in BUS_REPORT_SQL and "%s::text = 'ack'" in BUS_REPORT_SQL


@dataclass
class BusConn(FakeConn):
    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> FakeCursor:
        super().execute(sql, params)
        return FakeCursor(self.rows if sql == BUS_PENDING_SQL else None)


def test_ac1_database_calls_use_the_registered_statements() -> None:
    pressed = at(3)
    conn = BusConn(rows=[(41, "arm", TODAY, pressed, "sim")])
    db = PgRelayDb(lambda: conn, errors=(FakeDbError,), timeout_s=2.0)
    db.ensure_bus()
    db.ensure_bus_bell()
    made = [sql for sql, _ in conn.executed if sql.startswith(("CREATE", "ALTER"))]
    assert made == [*MESSAGES_DDL, *BUS_BELL_DDL]
    assert db.pending_bus(TODAY, "sim") == [Command(41, "arm", TODAY, pressed, "sim")]
    assert conn.executed[-1] == (BUS_PENDING_SQL, (TODAY, "sim", COMMAND_TTL_S))
    result = CommandResult(41, STAGE_DONE, RESULT_ARMED, "已啟動", created_at=pressed)
    db.report_bus(result, "sim")
    sql, params = conn.executed[-1]
    assert sql == BUS_REPORT_SQL
    assert list(params) == ["done", "armed", "已啟動", "sim", 41, pressed, "done", "done", "armed"]


# ---- AC3 待命與運作


def test_ac3_standby_sends_no_entries_but_exits_still_go_out() -> None:
    engine, broker, strategy = rig({0: Side.BUY, 3: Side.BUY})
    assert engine.armed is True  # 單獨跑（重播、舊的 run）一律是運作
    engine.disarm()
    for i in range(3):
        engine.on_tick(tick(i))
    assert engine.ledger.position == 0 and broker.submitted == []
    assert strategy.rejects == ["待命中（還沒按啟動）"]
    notes = [e.text for e in engine.events if e.kind == "reject"]
    assert len(notes) == 1 and "待命中" in notes[0] and "不再一一記錄" in notes[0]
    assert engine.arm() is False and engine.arm() is True
    for i in range(3, 6):
        engine.on_tick(tick(i))
    assert engine.ledger.position == 1
    # 手上有部位時切到待命（不平倉的那一種）：策略的出場單照送
    engine.disarm()
    strategy.plan[strategy.seen] = Side.SELL
    for i in range(6, 9):
        engine.on_tick(tick(i))
    assert engine.ledger.position == 0


def test_ac3_arm_command_reports_at_once_and_never_releases_a_halt() -> None:
    engine, _, _ = rig({})
    engine.disarm()
    engine.on_tick(tick(0))
    results = engine.on_control([cmd(5, 1, ACTION_ARM)], at(2))
    assert brief(results) == [(5, STAGE_ACK, RESULT_ARMED), (5, STAGE_DONE, RESULT_ARMED)]
    assert "已啟動" in results[0].note and results[0].action == ACTION_ARM and engine.armed
    again = engine.on_control([cmd(6, 3, ACTION_ARM)], at(4))  # 重複按照樣回報成功
    assert brief(again)[0] == (6, STAGE_ACK, RESULT_ARMED) and "本來就在運作中" in again[0].note
    engine.kill(at(5))
    halted = engine.on_control([cmd(7, 6, ACTION_ARM)], at(7))
    assert engine.risk.halt == HALT_KILL  # 啟動不會解除緊急停止
    assert "停手狀態" in halted[0].note and "不會解除停手" in halted[0].note


def test_ac3_stop_flattens_first_then_stands_by() -> None:
    engine, broker = long_one()
    results = engine.on_control([cmd(8, 2.5, ACTION_STANDBY)], at(3))
    assert brief(results) == [(8, STAGE_ACK, RESULT_FLATTENING)]
    assert "已切到待命" in results[0].note and results[0].action == ACTION_STANDBY
    assert engine.armed is False and engine.risk.halt is None  # 待命不是停手
    assert broker.submitted[-1].side is Side.SELL
    engine.on_tick(tick(4))
    engine.on_tick(tick(5))
    done = engine.on_control([], at(6))
    assert brief(done) == [(8, STAGE_DONE, RESULT_FLAT)] and engine.ledger.position == 0
    # 之後策略想再進場：不送
    strategy = engine.strategy
    assert isinstance(strategy, Scripted)
    strategy.plan[strategy.seen] = Side.BUY
    for i in range(6, 9):
        engine.on_tick(tick(i))
    assert engine.ledger.position == 0
    # 空手時再按一次停止：一樣回報成功
    twice = engine.on_control([cmd(9, 9, ACTION_STANDBY)], at(10))
    assert brief(twice) == [(9, STAGE_ACK, RESULT_FLAT), (9, STAGE_DONE, RESULT_FLAT)]
    # 再按啟動就能再交易
    engine.on_control([cmd(10, 11, ACTION_ARM)], at(12))
    strategy.plan[strategy.seen] = Side.BUY
    for i in range(12, 15):
        engine.on_tick(tick(i))
    assert engine.ledger.position == 1


def test_ac3_stop_asks_the_broker_first_like_flatten() -> None:
    engine, broker = long_one()
    broker.drift = -1  # 人已經用券商的 App 平掉了
    results = engine.on_control([cmd(8, 2.5, ACTION_STANDBY)], at(3))
    assert "已改用券商的數字並停手" in results[0].note
    assert [r.side for r in broker.submitted].count(Side.SELL) == 0  # 沒有照帳上的數字再賣一次


def test_ac8_snapshot_tells_the_website_standby_from_running() -> None:
    engine, _ = long_one()
    live = snapshot(engine, at(3), source="live", status="live", strategy="x")
    assert live["armed"] is True
    engine.disarm()
    assert snapshot(engine, at(3), source="live", status="live", strategy="x")["armed"] is False


# ---- AC4 每天的起點


def raw_config() -> dict[str, Any]:
    return tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def test_ac4_config_starts_in_standby_and_live_can_never_arm_itself() -> None:
    raw = raw_config()
    parsed = parse_config(raw)
    assert parsed.agent is not None and parsed.agent.arm_at_open is False
    assert parsed.agent.trader_start < parsed.session.start
    armed = parse_config({**raw, "agent": {**raw["agent"], "arm_at_open": True}})
    assert armed.agent is not None and armed.agent.arm_at_open is True  # 模擬可以
    live = {
        **raw,
        "mode": "live",
        "risk": {
            **raw["risk"],
            "daily_loss_limit_twd": 3000,
            "position_stop_loss_twd": 2000,
        },
        "agent": {**raw["agent"], "arm_at_open": True},
    }
    with pytest.raises(ConfigError, match="真單模式不准設 \\[agent\\] arm_at_open = true"):
        parse_config(live)
    parse_config({**live, "agent": raw["agent"]})  # 待命開始的真單設定是合法的
    for key, bad in (
        ("arm_at_open", "yes"),
        ("trader_start", "09:00:00"),
        ("restart_wait_s", 0),
        ("restart_wait_max_s", 1.0),
        ("steady_s", 0),
    ):
        with pytest.raises(ConfigError):
            parse_config({**raw, "agent": {**raw["agent"], key: bad}})
    without = {k: v for k, v in raw.items() if k not in ("agent", "watchdog")}
    assert parse_config(without).agent is None  # 沒有這一段的舊設定照樣讀得進來


def quit_at(folder: Path, when: datetime) -> Any:
    def hook(now: datetime) -> None:
        if now >= when:
            (folder / QUIT_NAME).write_text("x", encoding="utf-8")

    return hook


def test_ac4_an_agent_day_trades_nothing_until_someone_presses_start(tmp_path: Path) -> None:
    quiet = Rig(tmp_path, FakeClock(clock_at(8, 44, 50)))
    report = quiet.runner(standby_at_start=True).run()
    assert (report.reason, report.fills, report.position) == ("closed", 0, 0)
    assert any("今天從待命開始：要按「啟動」才會交易" in line for line in quiet.said)
    assert any("待命中" in line for line in quiet.said)  # 被略過的進場有講一次

    other = tmp_path / "b"
    other.mkdir()
    pressed = clock_at(8, 46, 0)  # InOut 原本 08:45:30 就會買

    def commands(now: datetime) -> list[Command]:
        return [Command(11, ACTION_ARM, DAY, pressed, "sim")] if now >= pressed else []

    armed = Rig(other, FakeClock(clock_at(8, 44, 50)), commands=commands)
    report = armed.runner(standby_at_start=True).run()
    fills = journal_fills(other)
    assert [f[1] for f in fills] == ["BUY", "SELL"] and fills[0][0] >= pressed.isoformat()
    assert report.reason == "closed"


# ---- AC7 模擬重開


def test_ac7_a_restart_replays_todays_start_and_stop(tmp_path: Path) -> None:
    started, stopped = clock_at(8, 45, 10), clock_at(8, 46, 0)  # 08:45:30 買；08:46 停止＝平倉

    def commands(now: datetime) -> list[Command]:
        rows = []
        if now >= started:
            rows.append(Command(12, ACTION_ARM, DAY, started, "sim"))
        if now >= stopped:
            rows.append(Command(13, ACTION_STANDBY, DAY, stopped, "sim"))
        return rows

    first = Rig(
        tmp_path,
        FakeClock(clock_at(8, 44, 50)),
        commands=commands,
        hook=quit_at(tmp_path, clock_at(8, 46, 30)),
    )
    one = first.runner(standby_at_start=True).run()
    before = journal_fills(tmp_path)
    assert (one.reason, one.halt, one.position) == ("quit", None, 0)
    assert [f[1] for f in before] == ["BUY", "SELL"] and before[1][0] < clock_at(
        8, 46, 10
    ).isoformat()
    assert [a for _, a, _ in earlier_actions(tmp_path, DAY)] == [ACTION_ARM, ACTION_STANDBY]

    second = Rig(tmp_path, FakeClock(clock_at(8, 47, 30)))  # 重開：指令都回報過了，不會再來
    two = second.runner(standby_at_start=True).run()
    assert (two.reason, two.halt, two.position) == ("closed", None, 0)
    assert journal_fills(tmp_path) == before  # 跟沒重開一樣；停止之後沒有再進場
    said = "\n".join(second.said)
    assert "套用今天稍早的遠端啟動" in said and "套用今天稍早的遠端停止（平倉後待命）" in said


# ---- AC2、AC6 整條路：匯流排 → 檔案 → 交易程式 → 檔案 → 匯流排


@dataclass
class FakeBus:
    """記憶體裡的訊息表，規則跟真的 SQL 一樣：只給對象那一支、有過回報就不再給、同一種回報不重複。"""

    rows: list[Command] = field(default_factory=list)
    reports: list[tuple[int, str, str, str]] = field(default_factory=list)

    def pending_bus(self, today: date, consumer: str) -> list[Command]:
        answered = {r[0] for r in self.reports}
        return [
            c for c in self.rows if c.day == today and c.target == consumer and c.id not in answered
        ]

    def report_bus(self, result: CommandResult, consumer: str) -> None:
        same = any(
            c.id == result.command_id and c.created_at == result.created_at for c in self.rows
        )
        seen = any(
            r[0] == result.command_id
            and r[1] == result.stage
            and (result.stage == STAGE_ACK or r[2] == result.code)
            for r in self.reports
        )
        if same and not seen:
            self.reports.append((result.command_id, result.stage, result.code, consumer))


def test_ac2_ac6_a_bus_command_travels_to_the_engine_and_the_report_comes_back(
    tmp_path: Path,
) -> None:
    engine, _ = long_one()
    bus = FakeBus(rows=[cmd(21, 2.5, ACTION_STANDBY), cmd(22, 2.5, ACTION_ARM, target="live")])
    relay = Relay(FakeDb(), "sim", tmp_path, CONTROL.poll_s, requests=FakeRequests(), bus=bus)
    source = FileControl(tmp_path, CONTROL.stale_s)
    loop = ControlLoop(engine, source, CONTROL.poll_s, urgent=source.changed)
    relay.step(at(3))
    assert [c["id"] for c in inbox(tmp_path)["commands"]] == [21]  # 給真單的那一則不給模擬
    loop.step(at(3.1))
    engine.on_tick(tick(4))
    loop.step(at(5.2))
    report = relay.step(at(6))
    assert report.ok and bus.reports == [
        (21, STAGE_ACK, RESULT_FLATTENING, "sim"),
        (21, STAGE_DONE, RESULT_FLAT, "sim"),
    ]
    assert engine.armed is False and engine.ledger.position == 0
    assert inbox(tmp_path)["commands"] == []  # 回報過的不再交出來
    relay2 = Relay(FakeDb(), "sim", tmp_path, CONTROL.poll_s, requests=FakeRequests(), bus=bus)
    relay2.step(at(8))  # 中繼程式重開會把回報檔從頭再送一次：不會多出重複的回報
    assert len(bus.reports) == 2


def test_ac2_only_the_trading_program_is_handed_bus_commands(tmp_path: Path) -> None:
    bus = FakeBus(rows=[cmd(21, 2.5, ACTION_ARM)])
    for name in ("watchdog", "admin"):
        folder = tmp_path / name
        folder.mkdir()
        Relay(FakeDb(), name, folder, CONTROL.poll_s, requests=FakeRequests(), bus=bus).step(at(3))
        assert inbox(folder)["commands"] == []
    # 沒接匯流排的中繼程式遇到匯流排指令的結果：跳過並講出來，不會記到別張表去
    plain = tmp_path / "plain"
    plain.mkdir()
    relay = Relay(FakeDb(), "sim", plain, CONTROL.poll_s, requests=FakeRequests())
    source = FileControl(plain, CONTROL.stale_s)
    relay.step(at(3))
    source.report(CommandResult(21, STAGE_ACK, RESULT_ARMED, "x", at(2.5), ACTION_ARM), at(3))
    report = relay.step(at(4))
    assert not report.ok and "沒有接匯流排" in str(report.error)


def test_ac9_trading_goes_on_when_the_bus_cannot_be_reached(tmp_path: Path) -> None:
    engine, _ = long_one(replace(cfg(), control=CONTROL))
    db = FakeDb(fail=True)
    relay = Relay(db, "sim", tmp_path, CONTROL.poll_s, requests=FakeRequests(), bus=FakeBus())
    source = FileControl(tmp_path, CONTROL.stale_s)
    loop = ControlLoop(engine, source, CONTROL.poll_s)
    assert not relay.step(at(3)).ok
    loop.step(at(3.1))
    assert [e.kind for e in engine.events][-1] == "control_link"  # 狀態裡看得到
    engine.on_tick(tick(4))
    assert engine.ledger.position == 1 and engine.risk.halt is None  # 交易與風控照常


# ---- AC5 常駐：顧著每一支程序


@dataclass
class FakeProc:
    code: int | None = None
    terminated: bool = False

    def poll(self) -> int | None:
        return self.code

    def terminate(self) -> None:
        self.terminated = True
        self.code = self.code if self.code is not None else -15

    def wait(self, timeout: float | None = None) -> int:
        return self.code or 0

    def kill(self) -> None:
        self.code = -9


@dataclass
class Spawned:
    folder: Path
    procs: dict[str, list[FakeProc]] = field(default_factory=dict)
    said: list[str] = field(default_factory=list)
    days: list[date] = field(default_factory=list)

    def plan(self, day: date) -> DayPlan:
        self.days.append(day)
        folder = self.folder / day.isoformat()
        folder.mkdir(parents=True, exist_ok=True)
        return DayPlan(
            folder,
            (
                Child("relay", ("relay",)),
                Child(
                    "run", ("run",), not_before=time(8, 30), done_ok=True, not_after=time(13, 45)
                ),
            ),
        )

    def spawn(self, args: Any, log: IO[str], cwd: Path) -> FakeProc:
        proc = FakeProc()
        self.procs.setdefault(args[0], []).append(proc)
        return proc

    def supervisor(self) -> Supervisor:
        return Supervisor(AGENT, self.plan, self.folder, spawn=self.spawn, say=self.said.append)


def clock(hh: int, mm: int, ss: int = 0, day: int = 16) -> datetime:
    return datetime(2026, 11, day, hh, mm, ss, tzinfo=TAIPEI)


def test_ac5_helpers_start_at_once_and_the_trader_waits_for_its_hour(tmp_path: Path) -> None:
    world = Spawned(tmp_path)
    sup = world.supervisor()
    sup.step(clock(7, 0))
    assert list(world.procs) == ["relay"]
    sup.step(clock(8, 29, 59))
    assert "run" not in world.procs
    sup.step(clock(8, 30))
    assert len(world.procs["run"]) == 1 and sup.starts("run") == 1
    assert (tmp_path / "2026-11-16" / "run.log").exists()
    sup.close()


def test_ac5_a_program_that_dies_is_restarted_with_a_growing_wait(tmp_path: Path) -> None:
    world = Spawned(tmp_path)
    sup = world.supervisor()
    sup.step(clock(9, 0))
    world.procs["relay"][0].code = 3
    sup.step(clock(9, 0, 1))
    assert any("relay 自己結束了（結束碼 3" in line and "2 秒後重開" in line for line in world.said)
    sup.step(clock(9, 0, 2))
    assert len(world.procs["relay"]) == 1  # 還沒到
    sup.step(clock(9, 0, 3))
    assert len(world.procs["relay"]) == 2
    waits = []
    now = clock(9, 0, 3)
    for _ in range(3):  # 一開就死：2 → 4 → 8 → 8（上限）
        world.procs["relay"][-1].code = 1
        sup.step(now)
        waits.append(world.said[-1].split("：")[1].split(" 秒後")[0])
        now += timedelta(seconds=10)
        sup.step(now)
    assert waits == ["4", "8", "8"]
    # 活得夠久（steady_s）之後才死：間隔從頭算
    now += timedelta(seconds=61)
    world.procs["relay"][-1].code = 1
    sup.step(now)
    assert "2 秒後重開" in world.said[-1]
    assert len(world.procs["run"]) == 1  # 別支不受影響
    sup.close()


def test_ac5_the_trader_ends_its_day_and_comes_back_the_next_morning(tmp_path: Path) -> None:
    world = Spawned(tmp_path)
    sup = world.supervisor()
    sup.step(clock(8, 30))
    world.procs["run"][0].code = 0  # 收盤、正常結束
    sup.step(clock(13, 46, 10))
    sup.step(clock(15, 0))
    assert len(world.procs["run"]) == 1 and any("明天再開" in line for line in world.said)
    relay_before = world.procs["relay"][0]
    assert relay_before.code is None  # 中繼程式整天都在
    sup.step(clock(0, 0, 1, day=17))  # 換日：全部收掉，開新的一天的
    assert relay_before.terminated and world.days == [date(2026, 11, 16), date(2026, 11, 17)]
    assert len(world.procs["relay"]) == 2 and len(world.procs["run"]) == 1
    sup.step(clock(8, 30, 0, day=17))
    assert len(world.procs["run"]) == 2
    assert (tmp_path / "2026-11-17" / "run.log").exists()
    sup.close()


def test_ac5_a_failure_after_the_close_is_not_retried_all_night(tmp_path: Path) -> None:
    world = Spawned(tmp_path)
    sup = world.supervisor()
    sup.step(clock(8, 30))
    world.procs["run"][0].code = 1  # 盤中出錯：重開
    sup.step(clock(10, 0))
    sup.step(clock(10, 0, 3))
    assert len(world.procs["run"]) == 2
    world.procs["run"][1].code = 1  # 收盤檢查沒過
    sup.step(clock(13, 46, 10))
    sup.step(clock(14, 0))
    assert len(world.procs["run"]) == 2 and any("今天不再重開" in line for line in world.said)
    sup.close()


def test_ac5_ctrl_c_takes_everything_down(tmp_path: Path) -> None:
    world = Spawned(tmp_path)
    sup = world.supervisor()
    ticks = iter([clock(9, 0), clock(9, 0, 1)])

    def sleep(seconds: float) -> None:
        if len(world.procs.get("run", [])) == 1 and sup.starts("relay") == 1:
            raise KeyboardInterrupt

    run_supervisor(sup, clock=lambda: next(ticks), sleep=sleep)
    assert all(p.terminated for group in world.procs.values() for p in group)
    assert any("收到中斷" in line for line in world.said)


def test_ac5_the_day_plan_wires_five_programs_to_one_folder(tmp_path: Path) -> None:
    setup = AgentSetup(
        python="py",
        config=Path("c.toml"),
        base=tmp_path,
        strategy="momentum",
        mode="sim",
        dsn_env="TWQUANT_SUPABASE_DSN",
        ticks_dsn_env="TWQUANT_PG_DSN",
        env_file=Path("x.env"),
        bell_port=5432,
        notify="send",
    )
    plan = day_plan(setup, AGENT, time(13, 45), date(2026, 11, 16))
    folder = tmp_path / "2026-11-16"
    assert plan.folder == folder and (folder / "watchdog").is_dir()
    by_name = {c.name: c for c in plan.children}
    assert list(by_name) == ["ticks", "relay", "relay_watchdog", "watchdog", "run"]
    run = by_name["run"]
    assert "--agent" in run.args and run.done_ok and run.not_before == time(8, 30)
    assert run.not_after == time(13, 45)
    assert by_name["relay"].args[-3:] == ("--bus", "--consumer", "sim")  # 只有這一支接匯流排
    assert "--bus" not in by_name["relay_watchdog"].args
    watch_relay = by_name["relay_watchdog"].args
    assert str(folder / "watchdog") in watch_relay and watch_relay[-1] == "watchdog"
    assert "--status" not in watch_relay  # 狀態只由主程式那一支中繼送
    for name in ("relay", "relay_watchdog"):
        assert "--bell-port" in by_name[name].args and "5432" in by_name[name].args
    # 交易的那一半拿不到資料庫的連線：指令裡沒有任何連線字串的變數名稱
    assert not any("DSN" in a for a in run.args) and not any(
        "DSN" in a for a in by_name["watchdog"].args
    )
    assert parser_accepts(run.args[3:]) and parser_accepts(by_name["watchdog"].args[3:])


def parser_accepts(args: tuple[str, ...]) -> bool:
    """組出來的指令列，指令列程式認不認得（設定檔不存在，所以會停在讀檔——那就代表參數都認得）。"""
    try:
        main(list(args))
    except FileNotFoundError:
        return True
    except SystemExit:
        return False
    return False


def test_ac5_cli_refuses_what_it_cannot_run(tmp_path: Path) -> None:
    tail = ["--strategy", "none", "--dsn-env", "TWQUANT_PG_DSN"]
    more = ["--ticks-file", str(tmp_path / "t.csv"), "--agent"]
    with pytest.raises(FileNotFoundError):
        main(["agent", "--config", str(CONFIG_PATH), "--dir", str(tmp_path / "missing"), *tail])
    old = tmp_path / "old.toml"
    text = CONFIG_PATH.read_text(encoding="utf-8")
    start, end = text.index("[agent]"), text.index("[momentum]")
    old.write_text(text[:start] + text[end:], encoding="utf-8")
    with pytest.raises(ConfigError, match="\\[agent\\]"):
        main(["agent", "--config", str(old), "--dir", str(tmp_path), *tail])
    with pytest.raises(ConfigError, match="不能用 --agent"):
        main(["run", "--config", str(old), "--dir", str(tmp_path), "--strategy", "none", *more])


def test_ac5_engine_side_modules_still_never_touch_the_database() -> None:
    root = Path(str(bus_module.__file__)).parent
    for name in ("agent.py", "engine.py", "runner.py", "watchdog.py"):
        source = (root / name).read_text(encoding="utf-8")
        assert "psycopg" not in source and "import requests" not in source, name
    assert isinstance(Engine, type)
