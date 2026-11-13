"""A-011：遠端緊急停止（D-224）——中繼程式。交易程式跟雲端之間唯一會連資料庫的東西，另一支程序。"""

from __future__ import annotations

import importlib
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from autotrade import relay as relay_module
from autotrade.broker.sim import SimBroker
from autotrade.cli import main
from autotrade.control import (
    HEARTBEAT_NAME,
    INBOX_NAME,
    ControlError,
    ControlLoop,
    FileControl,
    outbox_name,
)
from autotrade.engine import Engine
from autotrade.export import STATUS_UPSERT, dumps, snapshot
from autotrade.model import (
    RESULT_FLAT,
    RESULT_FLATTENING,
    RESULT_STUCK,
    STAGE_ACK,
    STAGE_DONE,
    Command,
    CommandResult,
    Side,
)
from autotrade.relay import (
    ACK_SQL,
    BEAT_SQL,
    COMMANDS_DDL,
    DONE_SQL,
    PENDING_SQL,
    TIMEOUT_SQL,
    PgRelayDb,
    Relay,
    RelayReport,
    open_pg,
)
from autotrade.risk import HALT_KILL
from test_a010_broker_requirements import FlakyBroker, Scripted
from test_a011_control import CONFIG_PATH, CONTROL, TODAY, at, brief, cfg, stop, tick

# 網站 frontend/functions/_lib/autotrade.ts 的 COMMANDS_DDL（frontend/tests/cloud-autotrade-stop.test.ts 守著同樣八句）
WEBSITE_DDL = (
    "CREATE SCHEMA IF NOT EXISTS web",
    "CREATE TABLE IF NOT EXISTS web.autotrade_commands (id bigserial PRIMARY KEY, day date NOT NULL, "
    "action text NOT NULL CHECK (action = 'stop'), created_by text NOT NULL, "
    "created_at timestamptz NOT NULL DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS web.autotrade_command_acks (command_id bigint NOT NULL "
    "REFERENCES web.autotrade_commands (id) ON DELETE CASCADE, consumer text NOT NULL "
    "CHECK (consumer IN ('sim', 'live', 'watchdog')), acked_at timestamptz NOT NULL DEFAULT now(), "
    "code text NOT NULL, note text NOT NULL, done_at timestamptz, done_code text, done_note text, "
    "PRIMARY KEY (command_id, consumer))",
    "CREATE TABLE IF NOT EXISTS web.autotrade_listeners (consumer text PRIMARY KEY "
    "CHECK (consumer IN ('sim', 'live', 'watchdog')), relay_seen_at timestamptz NOT NULL, "
    "engine_seen_at timestamptz, poll_s real NOT NULL)",
    "ALTER TABLE web.autotrade_commands ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE web.autotrade_command_acks ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE web.autotrade_listeners ENABLE ROW LEVEL SECURITY",
    "CREATE INDEX IF NOT EXISTS autotrade_commands_day ON web.autotrade_commands (day, id)",
)


@dataclass
class FakeDb:
    """記憶體裡的資料庫，規則跟真的 SQL 一樣：回報一支程式一列、只留第一次；收尾結果後寫的蓋掉先寫的。"""

    commands: list[Command] = field(default_factory=list)
    acks: dict[tuple[int, str], CommandResult] = field(default_factory=dict)
    dones: dict[tuple[int, str], CommandResult] = field(default_factory=dict)
    beats: list[tuple[str, float | None, float]] = field(default_factory=list)
    statuses: list[tuple[str, str, str, str, str, str]] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    fail: bool = False

    def _check(self, name: str) -> None:
        self.calls.append(name)
        if self.fail:
            raise ControlError("測試：資料庫連不上")

    def pending(self, today: date, consumer: str) -> list[Command]:
        self._check("pending")
        return [c for c in self.commands if c.day == today and (c.id, consumer) not in self.acks]

    def ack(self, result: CommandResult, consumer: str) -> None:
        self._check("ack")
        self.acks.setdefault((result.command_id, consumer), result)

    def done(self, result: CommandResult, consumer: str) -> None:
        self._check("done")
        if (result.command_id, consumer) in self.acks:
            self.dones[(result.command_id, consumer)] = result

    def beat(self, consumer: str, engine_age_s: float | None, poll_s: float) -> None:
        self._check("beat")
        self.beats.append((consumer, engine_age_s, poll_s))

    def push_status(self, row: tuple[str, str, str, str, str, str]) -> None:
        self._check("status")
        self.statuses.append(row)


def inbox(folder: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((folder / INBOX_NAME).read_text(encoding="utf-8"))
    return data


def append_result(folder: Path, result: CommandResult, *, newline: bool = True) -> None:
    line = json.dumps(
        {
            "command_id": result.command_id,
            "stage": result.stage,
            "code": result.code,
            "note": result.note,
            "at": at(0).isoformat(),
        },
        ensure_ascii=False,
    )
    with (folder / outbox_name(TODAY)).open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(line + ("\n" if newline else ""))


ACK7 = CommandResult(7, STAGE_ACK, RESULT_FLATTENING, "已停手，已送出平倉；帳上多 1 口")
DONE7 = CommandResult(7, STAGE_DONE, RESULT_FLAT, "已確認沒有部位、沒有委託；帳上沒有部位")


# ---- AC11 指令表與查詢


def test_ac11_ddl_matches_website_and_only_stop_can_exist() -> None:
    assert COMMANDS_DDL == WEBSITE_DDL
    assert "CHECK (action = 'stop')" in COMMANDS_DDL[1]
    assert sum("ENABLE ROW LEVEL SECURITY" in s for s in COMMANDS_DDL) == 3


def test_ac11_queries_are_per_program_and_never_create_or_delete_commands() -> None:
    # 不用「編號比上次大」：指令表重建過、編號重來也不會漏；「還沒回報過」是看這支程式自己的
    assert "id >" not in PENDING_SQL and "NOT EXISTS" in PENDING_SQL
    assert "a.consumer = %s" in PENDING_SQL and "c.day = %s" in PENDING_SQL
    assert "ON CONFLICT (command_id, consumer) DO NOTHING" in ACK_SQL  # 一支程式只留第一次的回報
    assert "WHERE command_id = %s AND consumer = %s" in DONE_SQL
    # 時間一律用資料庫的時鐘（收到的時間是欄位預設的 now()）
    assert "acked_at" not in ACK_SQL and "done_at = now()" in DONE_SQL
    assert "ON CONFLICT (consumer) DO UPDATE" in BEAT_SQL and "now()" in BEAT_SQL
    source = Path(str(relay_module.__file__)).read_text(encoding="utf-8")
    assert "INSERT INTO web.autotrade_commands" not in source  # 交易程式這邊不會自己生出停止指令
    for word in ("DELETE FROM", "TRUNCATE", "DROP ", "UPDATE web.autotrade_commands"):
        assert word not in source, word


# ---- AC12 中繼程式跑一輪


def test_ac12_commands_go_to_the_inbox_and_results_go_back(tmp_path: Path) -> None:
    db = FakeDb(commands=[stop(7, -3), stop(8, -1, day=TODAY - timedelta(days=1))])
    relay = Relay(db, "live", tmp_path, poll_s=2.0)
    report = relay.step(at(0))
    assert report == RelayReport(ok=True, commands=1, shipped=0, status_pushed=False, error=None)
    assert inbox(tmp_path) == {
        "written_at": "2026-11-13T09:00:00+08:00",
        "ok": True,
        "error": None,
        "commands": [
            {
                "id": 7,
                "action": "stop",
                "day": "2026-11-13",
                "created_at": "2026-11-13T08:59:57+08:00",
            }
        ],
    }
    assert not list(tmp_path.glob("*.tmp"))
    # 交易程式處理了、寫了結果：下一輪送回資料庫，之後這筆就不再出現在指令檔
    append_result(tmp_path, ACK7)
    assert relay.step(at(2)).shipped == 1 and db.acks == {(7, "live"): ACK7}
    assert inbox(tmp_path)["commands"] == []
    append_result(tmp_path, DONE7)
    assert relay.step(at(4)).shipped == 1 and db.dones == {(7, "live"): DONE7}
    assert relay.step(at(6)).shipped == 0 and db.calls.count("ack") == 1  # 送過的不重送


def test_ac12_one_programs_report_never_hides_the_command_from_another(tmp_path: Path) -> None:
    db = FakeDb(commands=[stop(7)])
    sim, live = tmp_path / "sim", tmp_path / "live"
    sim.mkdir()
    live.mkdir()
    sim_relay, live_relay = Relay(db, "sim", sim, 2.0), Relay(db, "live", live, 2.0)
    sim_relay.step(at(0))
    live_relay.step(at(0))
    append_result(sim, CommandResult(7, STAGE_ACK, RESULT_FLAT, "已停手；帳上沒有部位"))
    sim_relay.step(at(2))
    live_relay.step(at(2))
    # 模擬回報了「沒有部位」，真單那邊還是看得到這筆指令，資料庫裡也沒有真單的回報
    assert inbox(sim)["commands"] == [] and len(inbox(live)["commands"]) == 1
    assert set(db.acks) == {(7, "sim")}
    append_result(live, ACK7)
    live_relay.step(at(4))
    assert db.acks[(7, "live")] == ACK7 and db.acks[(7, "sim")].code == RESULT_FLAT


def test_ac12_half_written_line_waits_and_a_new_day_starts_a_new_file(tmp_path: Path) -> None:
    db = FakeDb(commands=[stop(7)])
    relay = Relay(db, "sim", tmp_path, 2.0)
    append_result(tmp_path, ACK7, newline=False)  # 交易程式還在寫這一行
    assert relay.step(at(0)).shipped == 0 and db.acks == {}
    with (tmp_path / outbox_name(TODAY)).open("a", encoding="utf-8", newline="\n") as fh:
        fh.write("\n")
    assert relay.step(at(2)).shipped == 1
    tomorrow = at(86400)
    line = {"command_id": 9, "stage": STAGE_ACK, "code": RESULT_FLAT, "note": "x", "at": "t"}
    (tmp_path / outbox_name(tomorrow.date())).write_text(json.dumps(line) + "\n", encoding="utf-8")
    assert relay.step(tomorrow).shipped == 1 and (9, "sim") in db.acks  # 新的一天從頭算


def test_ac12_relay_restart_resends_everything_without_changing_the_outcome(tmp_path: Path) -> None:
    db = FakeDb(commands=[stop(7)])
    append_result(tmp_path, ACK7)
    append_result(tmp_path, CommandResult(7, STAGE_DONE, RESULT_STUCK, "還沒平掉"))
    append_result(tmp_path, DONE7)
    Relay(db, "live", tmp_path, 2.0).step(at(0))
    first = (dict(db.acks), dict(db.dones))
    append_result(tmp_path, CommandResult(7, STAGE_ACK, RESULT_FLAT, "重新啟動後補報"))
    Relay(db, "live", tmp_path, 2.0).step(at(2))  # 中繼程式重開：整個檔重送一次
    assert (db.acks, db.dones) == first
    assert db.acks[(7, "live")] == ACK7 and db.dones[(7, "live")] == DONE7


def test_ac12_database_trouble_is_written_into_the_inbox_not_raised(tmp_path: Path) -> None:
    db = FakeDb(commands=[stop(7)], fail=True)
    relay = Relay(db, "live", tmp_path, 2.0)
    append_result(tmp_path, ACK7)
    report = relay.step(at(0))
    assert report.ok is False and report.error == "測試：資料庫連不上" and report.shipped == 0
    data = inbox(tmp_path)
    assert (data["ok"], data["error"], data["commands"]) == (False, "測試：資料庫連不上", [])
    # 交易程式這一側因此知道這條路斷了
    with pytest.raises(ControlError, match="中繼程式讀不到雲端的指令"):
        FileControl(tmp_path, CONTROL.stale_s).poll(at(1))
    db.fail = False
    report = relay.step(at(2))
    assert report.ok and report.shipped == 1 and db.acks == {(7, "live"): ACK7}  # 好了之後補送


def test_ac12_heartbeat_tells_how_long_ago_the_engine_read_the_inbox(tmp_path: Path) -> None:
    db = FakeDb()
    relay = Relay(db, "sim", tmp_path, 2.0)
    relay.step(at(0))
    assert db.beats == [("sim", None, 2.0)]  # 交易程式還沒讀過
    FileControl(tmp_path, CONTROL.stale_s).poll(at(1))
    seen = at(1).timestamp()
    os.utime(tmp_path / HEARTBEAT_NAME, (seen, seen))
    relay.step(at(4))
    assert db.beats[-1] == ("sim", 3.0, 2.0)


def test_ac12_status_file_is_pushed_only_when_it_changes(tmp_path: Path) -> None:
    config = cfg()
    engine = Engine(config, Scripted({}), SimBroker(config.sim))
    engine.on_tick(tick(0))
    text = dumps(snapshot(engine, at(0), source="live", status="live", strategy="x"))
    status = tmp_path / "status.json"
    db = FakeDb()
    relay = Relay(db, "sim", tmp_path, 2.0, status)
    assert relay.step(at(0)).status_pushed is False  # 還沒有狀態檔
    status.write_text(text, encoding="utf-8")
    os.utime(status, (1000, 1000))
    assert relay.step(at(2)).status_pushed is True and db.statuses[-1][4] == text
    assert relay.step(at(4)).status_pushed is False and len(db.statuses) == 1
    os.utime(status, (1002, 1002))
    assert relay.step(at(6)).status_pushed is True and len(db.statuses) == 2
    status.write_text("{}", encoding="utf-8")  # 壞掉的狀態檔：回報錯誤，不送、不當掉
    os.utime(status, (1004, 1004))
    bad = relay.step(at(8))
    assert bad.ok is False and "狀態檔的內容不對" in str(bad.error) and len(db.statuses) == 2


def test_ac12_rejects_unknown_program_names_and_unknown_stages(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="consumer"):
        Relay(FakeDb(), "paper", tmp_path, 2.0)
    db = FakeDb()
    relay = Relay(db, "sim", tmp_path, 2.0)
    append_result(tmp_path, CommandResult(7, "release", RESULT_FLAT, "x"))
    report = relay.step(at(0))
    assert report.ok is False and "不認得的階段" in str(report.error) and db.acks == {}
    append_result(tmp_path, ACK7)  # 壞的那一行跳過了，後面真正的結果照樣送得出去
    assert relay.step(at(2)).shipped == 1 and db.acks == {(7, "sim"): ACK7}


# ---- AC13 連線


class FakeDbError(Exception):
    pass


@dataclass
class FakeCursor:
    rows: list[tuple[Any, ...]] | None

    @property
    def description(self) -> object | None:
        return None if self.rows is None else object()

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self.rows or [])


@dataclass
class FakeConn:
    rows: list[tuple[Any, ...]] | None = None
    executed: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)
    transactions: int = 0
    broken: bool = False
    closed: bool = False

    @contextmanager
    def transaction(self) -> Any:
        self.transactions += 1
        yield

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> FakeCursor:
        if self.broken:
            raise FakeDbError("連線斷了")
        self.executed.append((sql, params))
        return FakeCursor(self.rows if sql == PENDING_SQL else None)

    def close(self) -> None:
        self.closed = True


def last_two(conn: FakeConn) -> list[tuple[str, tuple[Any, ...]]]:
    return conn.executed[-2:]


def test_ac13_every_action_is_one_short_transaction_with_a_timeout() -> None:
    pressed = datetime(2026, 11, 13, 1, 0, 3, tzinfo=UTC)  # 資料庫回 UTC
    conns = [FakeConn(rows=[(12, "stop", TODAY, pressed)]), FakeConn(rows=[])]
    opened: list[FakeConn] = []

    def connect() -> FakeConn:
        opened.append(conns[len(opened)])
        return opened[-1]

    db = PgRelayDb(connect, errors=(FakeDbError,), timeout_s=2.0)
    got = db.pending(TODAY, "live")
    assert got == [Command(id=12, action="stop", day=TODAY, created_at=at(3))]
    assert got[0].created_at.utcoffset() == timedelta(hours=8)
    assert last_two(opened[0]) == [(TIMEOUT_SQL, ("2000", "2000")), (PENDING_SQL, (TODAY, "live"))]
    db.ack(ACK7, "live")
    assert last_two(opened[0])[1] == (ACK_SQL, (7, "live", ACK7.code, ACK7.note))
    db.done(DONE7, "live")
    assert last_two(opened[0])[1] == (DONE_SQL, (DONE7.code, DONE7.note, 7, "live"))
    db.beat("live", None, 2.0)
    assert last_two(opened[0])[1] == (BEAT_SQL, ("live", None, 2.0))
    row = ("2026-11-13", "live", "live", "live", "{}", "2026-11-13T09:00:00+08:00")
    db.push_status(row)
    assert last_two(opened[0])[1] == (STATUS_UPSERT, row)
    assert opened[0].transactions == 5 and len(opened) == 1  # 同一條連線重複用
    assert "set_config('statement_timeout'" in TIMEOUT_SQL and "true" in TIMEOUT_SQL

    opened[0].broken = True
    with pytest.raises(ControlError, match="連線斷了"):
        db.pending(TODAY, "live")
    assert opened[0].closed
    assert db.pending(TODAY, "live") == [] and len(opened) == 2  # 下一次自己重連


def test_ac13_connection_is_pooler_safe_and_tables_are_checked_once_at_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    conn = FakeConn(rows=[])

    class FakePsycopg:
        Error = FakeDbError

        @staticmethod
        def connect(dsn: str, **kwargs: Any) -> FakeConn:
            calls.append((dsn, kwargs))
            return conn

    real_import = importlib.import_module

    def fake_import(name: str, package: str | None = None) -> Any:
        return FakePsycopg if name == "psycopg" else real_import(name, package)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    db = open_pg("postgresql://u:p@h:6543/db", timeout_s=2.0)
    assert calls == []  # 還沒用到之前不連線
    assert db.pending(TODAY, "sim") == []
    dsn, kwargs = calls[0]
    assert dsn == "postgresql://u:p@h:6543/db"
    # 雲端的連線池不支援預備語句；每一句自己成一筆交易；線路默默斷掉要發現得了
    assert kwargs["prepare_threshold"] is None and kwargs["autocommit"] is True
    assert 0 < kwargs["connect_timeout"] <= 3 and kwargs["keepalives"] == 1
    assert not any(sql.startswith(("CREATE", "ALTER")) for sql, _ in conn.executed)  # 連線時不建表
    db.ensure()  # 只有啟動時做一次
    ddl = [sql for sql, _ in conn.executed if sql.startswith(("CREATE", "ALTER"))]
    assert ddl == list(COMMANDS_DDL)
    tool = (Path(__file__).parents[1] / "tools" / "push_status.py").read_text(encoding="utf-8")
    assert "prepare_threshold=None" in tool


# ---- AC14 整條路串起來


def test_ac14_stop_travels_database_to_engine_and_back(tmp_path: Path) -> None:
    config = cfg()
    broker = FlakyBroker(SimBroker(config.sim))
    engine = Engine(config, Scripted({0: Side.BUY}), broker)
    for i in range(3):
        engine.on_tick(tick(i))
    db = FakeDb()
    relay = Relay(db, "live", tmp_path, CONTROL.poll_s)
    loop = ControlLoop(engine, FileControl(tmp_path, CONTROL.stale_s), CONTROL.poll_s)
    relay.step(at(3))
    loop.step(at(3))
    assert loop.state.ok is True and engine.risk.halt is None

    db.commands.append(stop(7, 4))  # 使用者在網站上按了停止
    relay.step(at(5))
    loop.step(at(5))
    assert engine.risk.halt == HALT_KILL and broker.submitted[-1].side is Side.SELL
    relay.step(at(7))
    assert brief([db.acks[(7, "live")]]) == [(7, STAGE_ACK, RESULT_FLATTENING)]
    assert (7, "live") not in db.dones  # 還沒確認平掉
    for s in (8, 9, 10):
        engine.on_tick(tick(s))
    loop.step(at(10))
    relay.step(at(11))
    assert brief([db.dones[(7, "live")]]) == [(7, STAGE_DONE, RESULT_FLAT)]
    assert engine.ledger.position == 0
    assert db.beats[-1][0] == "live" and db.beats[-1][1] is not None

    # 中繼程式停了：交易程式照跑，但看得出遠端停止這條路斷了
    events = len(engine.events)
    loop.step(at(11 + CONTROL.stale_s + 2))
    assert loop.state.ok is False and engine.events[events].kind == "control_link"
    assert "沒更新" in engine.events[events].text


def test_ac14_cli_runs_one_relay_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = FakeConn(rows=[])

    class FakePsycopg:
        Error = FakeDbError

        @staticmethod
        def connect(dsn: str, **kwargs: Any) -> FakeConn:
            return conn

    real_import = importlib.import_module

    def fake_import(name: str, package: str | None = None) -> Any:
        return FakePsycopg if name == "psycopg" else real_import(name, package)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    monkeypatch.setenv("AUTOTRADE_TEST_DSN", "postgresql://u:p@h:6543/db")
    args = ["relay", "--config", str(CONFIG_PATH), "--dir", str(tmp_path), "--once"]
    assert main([*args, "--dsn-env", "AUTOTRADE_TEST_DSN"]) == 0
    assert inbox(tmp_path)["ok"] is True and inbox(tmp_path)["commands"] == []
    assert any(sql == PENDING_SQL and params[1] == "sim" for sql, params in conn.executed)
    out = capsys.readouterr().out
    assert "sim 正常" in out and "postgresql" not in out  # 不把連線字串印出來
    monkeypatch.delenv("AUTOTRADE_TEST_DSN")
    with pytest.raises(Exception, match="AUTOTRADE_TEST_DSN 沒有設定"):
        main([*args, "--dsn-env", "AUTOTRADE_TEST_DSN"])
    with pytest.raises(FileNotFoundError):
        main([*args[:4], str(tmp_path / "nope"), "--once"])
