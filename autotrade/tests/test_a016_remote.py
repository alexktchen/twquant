"""A-016：網站的遙控按鈕——平倉、暫停／恢復自動交易（D-229）。

使用者 2026-11-14：「雲端也要有各式按鈕 賣出」，選了平倉、暫停／恢復、啟動／結束，模擬與真單都要。
這裡測交易程式與中繼程式這一側；啟動／結束（由後台執行）在 test_a017_remote_admin.py。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from autotrade import relay as relay_module
from autotrade.broker.sim import SimBroker
from autotrade.control import (
    ControlLoop,
    FileControl,
    outbox_name,
    result_from_json,
    result_to_json,
)
from autotrade.engine import Engine
from autotrade.export import snapshot
from autotrade.model import (
    ACTION_FLATTEN,
    ACTION_PAUSE,
    ACTION_QUIT,
    ACTION_RESUME,
    ACTION_START,
    ACTION_STOP,
    ADMIN_ACTIONS,
    ENGINE_ACTIONS,
    RESULT_FLAT,
    RESULT_FLATTENING,
    RESULT_IGNORED,
    RESULT_LINK_DOWN,
    RESULT_PAUSED,
    RESULT_RELEASED,
    RESULT_RESUMED,
    STAGE_ACK,
    STAGE_DONE,
    TAIPEI,
    Command,
    CommandResult,
    Side,
)
from autotrade.relay import (
    BELL_DDL,
    COMMANDS_DDL,
    REMOTE_BEAT_SQL,
    REQUEST_ACK_SQL,
    REQUEST_DONE_SQL,
    REQUESTS_ADMIN_SQL,
    REQUESTS_DDL,
    REQUESTS_SQL,
    PgRelayDb,
    Relay,
)
from autotrade.risk import HALT_KILL, HALT_MISMATCH
from autotrade.runner import QUIT_NAME, earlier_actions, earlier_stop
from test_a010_broker_requirements import FlakyBroker, Scripted
from test_a011_control import CONTROL, TODAY, at, brief, cfg, long_one, stop, tick
from test_a011_drill import FakeClock
from test_a011_relay import FakeConn, FakeCursor, FakeDb, FakeDbError, append_result, inbox
from test_a014_runner import DAY, Rig, journal_fills
from test_a014_runner import at as clock_at


def cmd(
    command_id: int, seconds: float, action: str, target: str = "sim", day: date = TODAY
) -> Command:
    return Command(id=command_id, action=action, day=day, created_at=at(seconds), target=target)


def rig(plan: dict[int, Side]) -> tuple[Engine, FlakyBroker, Scripted]:
    config = cfg()
    broker = FlakyBroker(SimBroker(config.sim))
    strategy = Scripted(plan)
    return Engine(config, strategy, broker), broker, strategy


@dataclass
class FakeRequests:
    """記憶體裡的遙控指令表，規則跟真的 SQL 一樣：交易程式只拿給自己的，後台只拿啟動／結束；收到只留第一次。"""

    rows: list[Command] = field(default_factory=list)
    acks: dict[int, CommandResult] = field(default_factory=dict)
    dones: dict[int, CommandResult] = field(default_factory=dict)
    beats: list[tuple[float | None, float]] = field(default_factory=list)

    def pending_requests(self, today: date, consumer: str) -> list[Command]:
        mine = ADMIN_ACTIONS if consumer == "admin" else ENGINE_ACTIONS
        return [
            c
            for c in self.rows
            if c.day == today
            and c.id not in self.acks
            and c.action in mine
            and (consumer == "admin" or c.target == consumer)
        ]

    def _same(self, result: CommandResult) -> bool:
        return any(
            c.id == result.command_id and c.created_at == result.created_at for c in self.rows
        )

    def ack_request(self, result: CommandResult) -> None:
        if self._same(result):
            self.acks.setdefault(result.command_id, result)

    def done_request(self, result: CommandResult) -> None:
        if self._same(result):
            self.acks.setdefault(result.command_id, result)
            self.dones[result.command_id] = result

    def beat_remote(self, reader_age_s: float | None, poll_s: float) -> None:
        self.beats.append((reader_age_s, poll_s))


# ---- AC1 指令表


def test_ac1_requests_live_in_their_own_table_and_the_stop_table_is_untouched() -> None:
    table = REQUESTS_DDL[0]
    assert "CREATE TABLE IF NOT EXISTS web.autotrade_requests" in table
    assert "CHECK (action IN ('flatten', 'pause', 'resume', 'start', 'quit'))" in table
    assert "'stop'" not in table  # 停止不走這張表
    assert "target text NOT NULL CHECK (target IN ('sim', 'live'))" in table  # 一筆只對一種模式
    # 編號跟停止共用同一個序號：兩張表不會撞號，交易程式用編號認指令才不會認錯
    assert "DEFAULT nextval('web.autotrade_commands_id_seq')" in table
    assert sum("ENABLE ROW LEVEL SECURITY" in s for s in REQUESTS_DDL) == 2
    assert "CHECK (action = 'stop')" in COMMANDS_DDL[1] and len(COMMANDS_DDL) == 8  # 停止那張表不動
    assert any("ON web.autotrade_requests" in s for s in BELL_DDL)  # 門鈴也會響
    for statement in REQUESTS_DDL:  # 可以重複執行，而且不刪、不改既有的東西
        assert "IF NOT EXISTS" in statement or "ENABLE ROW LEVEL SECURITY" in statement
    conn = FakeConn(rows=[])
    PgRelayDb(lambda: conn, errors=(FakeDbError,), timeout_s=2.0).ensure_requests()
    assert [sql for sql, _ in conn.executed if sql.startswith(("CREATE", "ALTER"))] == list(
        REQUESTS_DDL
    )
    source = Path(str(relay_module.__file__)).read_text(encoding="utf-8")
    assert "INSERT INTO web.autotrade_requests" not in source  # 交易程式這邊不會自己生出遙控指令


# ---- AC2 誰拿到什麼


@dataclass
class RequestConn(FakeConn):
    """查遙控指令時回 rows 的假連線。"""

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> FakeCursor:
        super().execute(sql, params)
        return FakeCursor(self.rows if sql in (REQUESTS_SQL, REQUESTS_ADMIN_SQL) else None)


def last(conn: FakeConn) -> tuple[str, tuple[Any, ...]]:
    return conn.executed[-1]


def test_ac2_each_program_is_only_handed_its_own_fresh_requests() -> None:
    for sql in (REQUESTS_SQL, REQUESTS_ADMIN_SQL):
        assert "r.day = %s" in sql and "r.acked_at IS NULL" in sql  # 今天的、還沒有人接手
        assert "r.created_at > now() - make_interval(secs => %s" in sql  # 太久沒人接手就作廢
    assert "r.action IN ('flatten', 'pause', 'resume') AND r.target = %s" in REQUESTS_SQL
    assert (
        "r.action IN ('start', 'quit')" in REQUESTS_ADMIN_SQL
        and "target" not in (REQUESTS_ADMIN_SQL.split("WHERE")[1])
    )
    # 只記到編號與按下時間都對得上的那一列；收到只記第一次；最後的結果自己就寫得進去
    assert "WHERE id = %s AND created_at = %s AND acked_at IS NULL" in REQUEST_ACK_SQL
    assert "acked_at = COALESCE(acked_at, now())" in REQUEST_DONE_SQL
    assert "WHERE id = %s AND created_at = %s" in REQUEST_DONE_SQL
    assert "ON CONFLICT (id) DO UPDATE" in REMOTE_BEAT_SQL

    pressed = datetime(2026, 11, 13, 1, 0, 3, tzinfo=TAIPEI)
    conn = RequestConn(rows=[(21, "flatten", TODAY, pressed, "sim")])
    db = PgRelayDb(lambda: conn, errors=(FakeDbError,), timeout_s=2.0, request_ttl_s=30.0)
    assert db.pending_requests(TODAY, "sim") == [
        Command(id=21, action="flatten", day=TODAY, created_at=pressed, target="sim")
    ]
    assert last(conn) == (REQUESTS_SQL, (TODAY, 30.0, "sim"))
    db.pending_requests(TODAY, "admin")
    assert last(conn) == (REQUESTS_ADMIN_SQL, (TODAY, 30.0))
    ack = CommandResult(21, STAGE_ACK, RESULT_FLATTENING, "已送出平倉", pressed, ACTION_FLATTEN)
    db.ack_request(ack)
    assert last(conn) == (REQUEST_ACK_SQL, (RESULT_FLATTENING, "已送出平倉", 21, pressed))
    done = CommandResult(21, STAGE_DONE, RESULT_FLAT, "平掉了", pressed, ACTION_FLATTEN)
    db.done_request(done)
    assert last(conn) == (
        REQUEST_DONE_SQL,
        (RESULT_FLAT, "平掉了", RESULT_FLAT, "平掉了", 21, pressed),
    )
    db.beat_remote(1.5, 2.0)
    assert last(conn) == (REMOTE_BEAT_SQL, (1.5, 2.0))


def result_line(folder: Path, result: CommandResult, pressed: float) -> None:
    line = json.dumps(result_to_json(replace_created(result, pressed), at(0)), ensure_ascii=False)
    with (folder / outbox_name(TODAY)).open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(line + "\n")


def replace_created(result: CommandResult, pressed: float) -> CommandResult:
    return CommandResult(
        result.command_id, result.stage, result.code, result.note, at(pressed), result.action
    )


def test_ac2_the_relay_merges_both_lanes_and_sends_each_result_to_its_own_table(
    tmp_path: Path,
) -> None:
    db = FakeDb(commands=[stop(7, 1)])
    requests = FakeRequests(
        rows=[
            cmd(8, 2, ACTION_FLATTEN, "sim"),
            cmd(9, 2, ACTION_PAUSE, "live"),  # 給真單的：模擬的中繼程式不拿
            cmd(10, 2, ACTION_START, "sim"),  # 啟動是後台的事
        ]
    )
    relay = Relay(db, "sim", tmp_path, CONTROL.poll_s, requests=requests)
    assert relay.step(at(3)).commands == 2
    got = inbox(tmp_path)["commands"]
    assert [(c["id"], c["action"], c.get("target")) for c in got] == [
        (7, "stop", None),
        (8, "flatten", "sim"),
    ]
    assert "target" not in got[0]  # 停止的格式跟 A-011 一樣

    result_line(tmp_path, CommandResult(7, STAGE_ACK, RESULT_FLAT, "已停手"), 1)
    flat_ack = CommandResult(8, STAGE_ACK, RESULT_FLATTENING, "已送出平倉", action=ACTION_FLATTEN)
    flat_done = CommandResult(8, STAGE_DONE, RESULT_FLAT, "平掉了", action=ACTION_FLATTEN)
    result_line(tmp_path, flat_ack, 2)
    result_line(tmp_path, flat_done, 2)
    assert relay.step(at(5)).shipped == 3
    assert list(db.acks) == [(7, "sim")] and db.dones == {}  # 停止記在停止那張表
    assert requests.acks[8].code == RESULT_FLATTENING and requests.dones[8].code == RESULT_FLAT
    assert inbox(tmp_path)["commands"] == []  # 都回報過了

    # 沒有接遙控那一路的中繼程式（A-011 的樣子）：遙控指令的結果跳過並講明，不卡住後面的
    plain = tmp_path / "plain"
    plain.mkdir()
    old = Relay(FakeDb(commands=[stop(7, 1)]), "sim", plain, CONTROL.poll_s)
    result_line(plain, flat_ack, 2)
    result_line(plain, CommandResult(7, STAGE_ACK, RESULT_FLAT, "已停手"), 1)
    report = old.step(at(5))
    assert report.shipped == 1 and "沒有接那一路" in str(report.error)


def test_ac2_the_admin_relay_takes_start_and_quit_and_never_the_stop(tmp_path: Path) -> None:
    db = FakeDb(commands=[stop(7, 1)])
    requests = FakeRequests(rows=[cmd(8, 2, ACTION_FLATTEN), cmd(10, 2, ACTION_QUIT, "live")])
    relay = Relay(db, "admin", tmp_path, CONTROL.poll_s, requests=requests)
    assert relay.step(at(3)).ok
    assert [(c["id"], c["action"]) for c in inbox(tmp_path)["commands"]] == [(10, "quit")]
    assert db.calls == []  # 停止的表、交易程式的心跳都不碰
    assert requests.beats == [(None, CONTROL.poll_s)]  # 後台還沒讀過指令檔
    with pytest.raises(ValueError, match="一定要接遙控指令"):
        Relay(db, "admin", tmp_path, CONTROL.poll_s)
    with pytest.raises(ValueError, match="consumer"):
        Relay(db, "boss", tmp_path, CONTROL.poll_s, requests=requests)


# ---- AC3 平倉


def test_ac3_flatten_closes_the_position_but_trading_goes_on() -> None:
    engine, broker, strategy = rig({0: Side.BUY, 3: Side.BUY, 5: Side.BUY})
    for i in range(3):
        engine.on_tick(tick(i))
    assert engine.ledger.position == 1
    results = engine.on_control([cmd(8, 2.5, ACTION_FLATTEN)], at(3))
    assert brief(results) == [(8, STAGE_ACK, RESULT_FLATTENING)]
    assert results[0].action == ACTION_FLATTEN
    assert "已送出平倉" in results[0].note and "已停手" not in results[0].note
    assert engine.risk.halt is None and broker.submitted[-1].side is Side.SELL
    sent = len(broker.submitted)
    engine.on_tick(tick(3.2))  # 還沒平掉：策略想再進場（劇本第 3 筆）——不送，也不重複送平倉
    assert len(broker.submitted) == sent and strategy.rejects == ["正在平倉"]
    engine.on_tick(tick(4))  # 平倉成交
    assert engine.ledger.position == 0
    engine.on_tick(tick(5))  # 平掉之後策略可以再進場（劇本第 5 筆）
    engine.on_tick(tick(6))
    assert engine.ledger.position == 1 and engine.risk.halt is None
    # 回報的時候策略已經又進場了：平掉的那一刻有記下來，不會誤報「沒平掉」
    assert brief(engine.on_control([], at(7))) == [(8, STAGE_DONE, RESULT_FLAT)]
    kinds = [(e.kind, e.data.get("action")) for e in engine.events if e.kind == "control"]
    assert kinds == [("control", ACTION_FLATTEN)]


def test_ac3_flatten_with_nothing_to_close_says_so_at_once() -> None:
    engine, broker, _ = rig({})
    engine.on_tick(tick(0))
    results = engine.on_control([cmd(8, 0.5, ACTION_FLATTEN)], at(1))
    assert brief(results) == [(8, STAGE_ACK, RESULT_FLAT), (8, STAGE_DONE, RESULT_FLAT)]
    assert "本來就沒有部位" in results[0].note and broker.submitted == []
    engine.on_tick(tick(2))
    assert engine.risk.halt is None


def test_ac3_flatten_asks_the_broker_first() -> None:
    # 人已經用券商的 App 平掉了：照帳上的數字再賣會變成放空
    engine, broker = long_one()
    sent = len(broker.submitted)
    broker.drift = -1
    results = engine.on_control([cmd(8, 2.5, ACTION_FLATTEN)], at(3))
    assert brief(results)[0] == (8, STAGE_ACK, RESULT_FLAT)
    assert "券商的部位跟帳上不一樣" in results[0].note
    assert len(broker.submitted) == sent and engine.ledger.position == 0
    assert engine.risk.halt == HALT_MISMATCH  # 對不上照原本的規矩停手


def test_ac3_flatten_finishes_on_the_timer_when_quotes_stop() -> None:
    engine, broker = long_one()
    engine.on_disconnect(at(2.5))  # 按下的時候券商連線是斷的：先記著
    results = engine.on_control([cmd(8, 2.6, ACTION_FLATTEN)], at(3))
    assert results[0].code == RESULT_LINK_DOWN and "已停手" not in results[0].note
    sent = len(broker.submitted)
    engine.on_reconnect(at(3.5))
    engine.on_timer(at(4))  # 行情停住了：連線恢復後靠時鐘把平倉送出去
    assert len(broker.submitted) == sent + 1 and broker.submitted[-1].side is Side.SELL
    assert engine.risk.halt is None


# ---- AC4 暫停／恢復


def test_ac4_pause_blocks_entries_and_resume_lets_them_through_again() -> None:
    engine, broker, strategy = rig({1: Side.BUY, 2: Side.BUY, 4: Side.BUY})
    results = engine.on_control([cmd(9, 0, ACTION_PAUSE)], at(0.5))
    assert brief(results) == [(9, STAGE_ACK, RESULT_PAUSED), (9, STAGE_DONE, RESULT_PAUSED)]
    assert "不再進新單" in results[0].note and results[0].action == ACTION_PAUSE
    assert engine.paused is True
    for i in range(3):
        engine.on_tick(tick(i))
    assert broker.submitted == [] and strategy.rejects == ["自動交易暫停中"] * 2
    noted = [e.text for e in engine.events if e.kind == "reject"]
    assert len(noted) == 1 and "之後的不再一一記錄" in noted[0]  # 只記第一次，不洗版
    live = snapshot(engine, at(3), source="live", status="live", strategy="x")
    assert live["paused"] is True and live["halt"] is None

    again = engine.on_control([cmd(10, 3, ACTION_PAUSE)], at(3.2))
    assert again[0].code == RESULT_PAUSED and "本來就在暫停中" in again[0].note
    results = engine.on_control([cmd(11, 3, ACTION_RESUME)], at(3.5))
    assert brief(results) == [(11, STAGE_ACK, RESULT_RESUMED), (11, STAGE_DONE, RESULT_RESUMED)]
    assert "已恢復自動交易" in results[0].note and engine.paused is False
    engine.on_tick(tick(3))
    engine.on_tick(tick(4))
    assert [r.side for r in broker.submitted] == [Side.BUY]
    assert snapshot(engine, at(5), source="live", status="live", strategy="x")["paused"] is False


def test_ac4_exits_still_go_out_while_paused() -> None:
    engine, broker, _ = rig({0: Side.BUY, 4: Side.SELL})
    for i in range(3):
        engine.on_tick(tick(i))
    engine.on_control([cmd(9, 2.5, ACTION_PAUSE)], at(3))
    engine.on_tick(tick(3))
    engine.on_tick(tick(4))  # 策略自己的出場單
    engine.on_tick(tick(5))
    assert broker.submitted[-1].side is Side.SELL and engine.ledger.position == 0


def test_ac4_resume_never_releases_a_stop() -> None:
    engine, _ = long_one()
    engine.on_control([stop(7, 2.5)], at(3))
    assert engine.risk.halt == HALT_KILL
    results = engine.on_control([cmd(11, 3, ACTION_RESUME)], at(3.5))
    assert results[0].code == RESULT_RESUMED
    assert "本來就沒有暫停" in results[0].note and "不會解除停手" in results[0].note
    assert engine.risk.halt == HALT_KILL


# ---- AC5 回報


def test_ac5_results_carry_the_action_and_release_only_touches_stops() -> None:
    engine, _ = long_one()
    results = engine.on_control([cmd(8, 2.5, ACTION_FLATTEN)], at(3))
    row = result_to_json(results[0], at(3))
    assert row["action"] == ACTION_FLATTEN and result_from_json(row).action == ACTION_FLATTEN
    old = {k: v for k, v in row.items() if k != "action"}
    assert result_from_json(old).action == ACTION_STOP  # 舊的回報檔沒有這一欄：那時只有停止
    engine.on_control([stop(9, 3.5)], at(4))
    engine.release_kill(at(10))
    after = engine.on_control([], at(11))
    assert (9, STAGE_DONE, RESULT_RELEASED) in brief(after)
    assert not any(r.command_id == 8 and r.code == RESULT_RELEASED for r in after)


# ---- AC6 只算當天


def test_ac6_only_todays_requests_run_and_a_new_day_starts_unpaused() -> None:
    engine, _, _ = rig({})
    yesterday = TODAY - timedelta(days=1)
    results = engine.on_control([cmd(9, 1, ACTION_PAUSE, day=yesterday)], at(2))
    assert brief(results) == [(9, STAGE_ACK, RESULT_IGNORED)] and engine.paused is False
    engine.on_control([cmd(10, 3, ACTION_PAUSE)], at(4))
    assert engine.paused is True
    engine.on_control([], at(4) + timedelta(days=1))
    assert engine.paused is False


# ---- AC7 模擬重開


def test_ac7_earlier_actions_reads_the_results_file(tmp_path: Path) -> None:
    def row(action: str | None, stage: str, code: str, when: datetime) -> str:
        data: dict[str, Any] = {"command_id": 1, "stage": stage, "code": code, "note": "", "at": ""}
        data["at"] = when.isoformat()
        if action is not None:
            data["action"] = action
        if action == ACTION_PAUSE:  # 執行當下處理到哪一筆行情（電腦的時鐘比行情快一點）
            data["after_tick"] = (when - timedelta(seconds=1)).isoformat()
        return json.dumps(data) + "\n"

    path = tmp_path / outbox_name(DAY)
    assert earlier_actions(tmp_path, DAY) == []
    path.write_text(
        row(ACTION_RESUME, STAGE_ACK, RESULT_RESUMED, clock_at(8, 47))
        + row(ACTION_PAUSE, STAGE_ACK, RESULT_PAUSED, clock_at(8, 46))
        + row(ACTION_PAUSE, STAGE_DONE, RESULT_PAUSED, clock_at(8, 46))  # 收尾那一行不算第二次
        + row(ACTION_FLATTEN, STAGE_ACK, RESULT_IGNORED, clock_at(8, 46, 30))  # 沒執行的不算
        + row(None, STAGE_ACK, RESULT_FLATTENING, clock_at(8, 48))  # 舊格式＝停止
        + row(ACTION_FLATTEN, STAGE_ACK, RESULT_FLATTENING, clock_at(8, 49))[:-1],  # 沒寫完
        encoding="utf-8",
    )
    assert earlier_actions(tmp_path, DAY) == [
        (clock_at(8, 46), ACTION_PAUSE, clock_at(8, 45, 59)),
        (clock_at(8, 47), ACTION_RESUME, None),
    ]
    assert earlier_stop(tmp_path, DAY) == clock_at(8, 48)  # 停止只看停止
    path.write_text("不是 JSON\n", encoding="utf-8")
    with pytest.raises(ValueError, match="第 1 行讀不懂"):
        earlier_actions(tmp_path, DAY)


def quit_at(folder: Path, when: datetime) -> Any:
    def hook(now: datetime) -> None:
        if now >= when:
            (folder / QUIT_NAME).write_text("x", encoding="utf-8")

    return hook


def test_ac7_a_restart_replays_todays_flatten(tmp_path: Path) -> None:
    pressed = clock_at(8, 46, 0)  # InOut：08:45:30 買、08:47:00 賣

    def commands(now: datetime) -> list[Command]:
        if now < pressed:
            return []
        return [Command(id=8, action=ACTION_FLATTEN, day=DAY, created_at=pressed, target="sim")]

    first = Rig(
        tmp_path,
        FakeClock(clock_at(8, 44, 50)),
        commands=commands,
        hook=quit_at(tmp_path, clock_at(8, 46, 30)),
    )
    one = first.runner().run()
    assert (one.reason, one.halt, one.position, one.fills) == ("quit", None, 0, 2)
    flattened = journal_fills(tmp_path)
    assert flattened[1][1] == "SELL" and flattened[1][0] < clock_at(8, 46, 10).isoformat()

    second = Rig(tmp_path, FakeClock(clock_at(8, 47, 30)))  # 重開：指令已經回報過，不會再來
    two = second.runner().run()
    assert (two.reason, two.halt, two.position) == ("closed", None, 0)
    assert journal_fills(tmp_path) == flattened  # 沒有變成「08:47 才賣」
    assert "套用今天稍早的遠端平倉" in "\n".join(second.said)


def test_ac7_a_restart_replays_todays_pause_and_resume(tmp_path: Path) -> None:
    paused, resumed = clock_at(8, 45, 10), clock_at(8, 46, 10)

    def commands(now: datetime) -> list[Command]:
        rows = []
        if now >= paused:
            rows.append(Command(9, ACTION_PAUSE, DAY, paused, "sim"))
        if now >= resumed:
            rows.append(Command(10, ACTION_RESUME, DAY, resumed, "sim"))
        return rows

    first = Rig(
        tmp_path,
        FakeClock(clock_at(8, 44, 50)),
        commands=commands,
        hook=quit_at(tmp_path, clock_at(8, 46, 40)),
    )
    one = first.runner().run()
    bought = journal_fills(tmp_path)
    assert one.fills == 1 and bought[0][1] == "BUY"
    assert bought[0][0] >= resumed.isoformat()  # 暫停的時候沒有進場（原本 08:45:30 就會買）

    second = Rig(tmp_path, FakeClock(clock_at(8, 47, 30)))
    second.runner().run()
    again = journal_fills(tmp_path)
    # 一模一樣：靠的是回報檔裡記的「當時處理到哪一筆行情」，不是電腦的時鐘（它比行情快一秒）
    assert again[0] == bought[0] and [f[1] for f in again] == ["BUY", "SELL"]
    said = "\n".join(second.said)
    assert "套用今天稍早的遠端暫停自動交易" in said and "套用今天稍早的遠端恢復自動交易" in said


# ---- 整條路：檔案 → 交易程式 → 檔案


def test_ac2_ac5_request_travels_through_the_files(tmp_path: Path) -> None:
    engine, _ = long_one()
    requests = FakeRequests(rows=[cmd(8, 2.5, ACTION_FLATTEN)])
    relay = Relay(FakeDb(), "sim", tmp_path, CONTROL.poll_s, requests=requests)
    source = FileControl(tmp_path, CONTROL.stale_s)
    loop = ControlLoop(engine, source, CONTROL.poll_s, urgent=source.changed)
    relay.step(at(3))
    loop.step(at(3.1))
    engine.on_tick(tick(4))
    loop.step(at(5.2))
    relay.step(at(6))
    assert requests.acks[8].code == RESULT_FLATTENING and requests.dones[8].code == RESULT_FLAT
    assert requests.acks[8].action == ACTION_FLATTEN
    assert engine.ledger.position == 0 and engine.risk.halt is None
    append_result(
        tmp_path, CommandResult(99, STAGE_ACK, RESULT_FLAT, "舊格式的一行")
    )  # 沒有 action＝停止
    assert relay.step(at(8)).ok
