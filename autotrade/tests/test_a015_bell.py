"""A-015：遠端指令的門鈴（D-228）。資料庫一有新的指令就推給中繼程式；輪詢原封不動留著當備援。"""

from __future__ import annotations

import importlib
import time as time_module
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from autotrade import cli as cli_module
from autotrade.bell import CHANNEL, NOT_YET, BellError, BellState, PgBell, open_bell
from autotrade.cli import main
from autotrade.control import ControlLoop, FileControl
from autotrade.jobs import DrillRequest, Jobs, RunRequest
from autotrade.relay import BELL_DDL, COMMANDS_DDL, PgRelayDb, Relay, run
from autotrade.risk import HALT_KILL
from test_a011_control import CONFIG_PATH, CONTROL, TODAY, FakeSource, at, long_one, stop
from test_a011_relay import FakeConn, FakeDb, FakeDbError
from test_a012_admin import FakeSpawn

# ---- 假的「聽」連線與假時鐘


@dataclass
class Note:
    channel: str
    payload: str


@dataclass
class Ticker:
    """假時鐘（秒）。"""

    now: float = 0.0

    def __call__(self) -> float:
        return self.now


@dataclass
class ListenConn:
    """假的「聽」連線。等的時候把假時鐘往前推；hears=False＝別人按的鈴傳不到這裡（例如連到交易模式的連線池）。"""

    clock: Ticker
    hears: bool = True
    broken: bool = False
    closed: bool = False
    executed: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)
    queue: list[Note] = field(default_factory=list)
    waits: list[float] = field(default_factory=list)
    on_wait: Callable[[ListenConn], None] | None = None
    sender: PingConn = field(init=False)

    def __post_init__(self) -> None:
        self.sender = PingConn(self)

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if self.broken:
            raise FakeDbError("連線斷了")
        self.executed.append((sql, params))
        if sql.startswith("SELECT pg_notify"):
            # 雲端實測到的陷阱：在聽的那條連線自己按，就算平常聽不到別人，也會收到自己這一聲
            self.queue.append(Note(str(params[0]), str(params[1])))

    def notifies(self, timeout: float, stop_after: int) -> Any:
        assert stop_after == 1
        if self.broken:
            raise FakeDbError("連線斷了")
        self.waits.append(timeout)
        if self.on_wait is not None:
            self.on_wait(self)
        if self.queue:
            self.clock.now += 0.01
            yield self.queue.pop(0)
        else:
            self.clock.now += timeout

    def close(self) -> None:
        self.closed = True

    def pings(self) -> list[tuple[Any, ...]]:
        return self.sender.sent


@dataclass
class PingConn:
    """假的「按測試鈴」連線：另一條連線，按下去的鈴傳不傳得到看聽的那一條。"""

    listener: ListenConn
    sent: list[tuple[Any, ...]] = field(default_factory=list)
    closed: bool = False

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if self.listener.broken:
            raise FakeDbError("連線斷了")
        assert sql.startswith("SELECT pg_notify")
        self.sent.append(params)
        if self.listener.hears:
            self.listener.queue.append(Note(str(params[0]), str(params[1])))

    def close(self) -> None:
        self.closed = True


def tokens() -> Callable[[], str]:
    """可預測的代號：第一個拿去當自己專用的頻道，之後每次自己測用一個。"""
    made = iter(f"{i:012x}" for i in range(1, 1000))
    return lambda: next(made)


def bell_with(
    conn: ListenConn | None, clock: Ticker, **kw: Any
) -> tuple[PgBell, list[ListenConn | None]]:
    opened: list[ListenConn | None] = []

    def connect() -> ListenConn:
        opened.append(conn)
        if conn is None:
            raise FakeDbError("連不上")
        return conn

    args: dict[str, Any] = {"clock": clock, "token": tokens()} | kw
    return PgBell(connect, lambda: opened[-1].sender, errors=(FakeDbError,), **args), opened  # type: ignore[union-attr]


def stop_after(bell: PgBell, waits: int) -> Callable[[ListenConn], None]:
    """等了幾次之後把門鈴關掉（讓 serve 正常回來）。"""

    def hook(conn: ListenConn) -> None:
        if len(conn.waits) >= waits:
            bell.close()

    return hook


# ---- AC1 資料庫端


def test_ac1_the_database_rings_on_every_new_command_and_the_tables_are_untouched() -> None:
    function, trigger = BELL_DDL
    assert function.startswith("CREATE OR REPLACE FUNCTION web.autotrade_ring()")
    assert f"pg_notify('{CHANNEL}'" in function and "NEW.id" in function
    assert "AFTER INSERT ON web.autotrade_commands" in trigger
    assert trigger.startswith("CREATE OR REPLACE TRIGGER ")  # 可以重複執行
    assert not any("ring" in s for s in COMMANDS_DDL)  # A-011 的八句建表語句不動
    conn = FakeConn(rows=[])
    db = PgRelayDb(lambda: conn, errors=(FakeDbError,), timeout_s=2.0)
    db.ensure_bell()
    made = [sql for sql, _ in conn.executed if sql.startswith("CREATE")]
    assert made == list(BELL_DDL)


# ---- AC2 聽門鈴


def test_ac2_a_ring_wakes_the_waiter_once() -> None:
    clock = Ticker()
    conn = ListenConn(clock)
    bell, _ = bell_with(conn, clock)

    def hook(c: ListenConn) -> None:
        if len(c.waits) == 2:  # 自己測過了，這時有人在網站上按了停止
            c.queue.append(Note(CHANNEL, "autotrade_commands:7"))
        stop_after(bell, 3)(c)

    conn.on_wait = hook
    assert bell.wait(0.0) is False  # 還沒有人按
    bell.serve()
    # 聽的那條連線只聽，測試鈴由另一條連線按
    assert [sql for sql, _ in conn.executed] == [
        f"LISTEN {CHANNEL}",
        "LISTEN autotrade_bell_000000000001",
    ]
    assert len(conn.pings()) == 1 and conn.sender.closed
    assert bell.wait(0.0) is True
    assert bell.wait(0.0) is False  # 一次鈴只叫醒一次
    assert conn.closed  # 收工時把連線關掉


def test_ac2_wait_gives_up_after_the_timeout_without_a_ring() -> None:
    bell, _ = bell_with(None, Ticker())
    began = time_module.monotonic()
    assert bell.wait(0.05) is False
    assert 0.04 <= time_module.monotonic() - began < 1.0


# ---- AC3 自己測


def test_ac3_the_line_is_proved_by_hearing_its_own_ring_and_checked_again_every_check_s() -> None:
    clock = Ticker()
    conn = ListenConn(clock)
    bell, _ = bell_with(conn, clock, check_s=20.0, heard_within_s=5.0)
    states: list[bool | None] = []

    def hook(c: ListenConn) -> None:
        states.append(bell.state.ok)
        stop_after(bell, 4)(c)

    conn.on_wait = hook
    assert bell.state == NOT_YET == BellState(None, "還沒連上")
    bell.serve()
    own = "autotrade_bell_000000000001"
    assert conn.pings() == [
        (own, "000000000002"),
        (own, "000000000003"),
    ]  # 連上時一次、20 秒後再一次
    assert states == [None, True, True, True]  # 聽到自己那一聲才算聽得到
    assert conn.waits[0] == 5.0 and conn.waits[1] == pytest.approx(
        20.0
    )  # 先等自己那一聲，再等到下一次測


def test_ac3_other_programs_and_stale_test_rings_do_not_count() -> None:
    clock = Ticker()
    conn = ListenConn(clock, hears=False)
    bell, _ = bell_with(conn, clock, heard_within_s=5.0)
    own = "autotrade_bell_000000000001"

    def hook(c: ListenConn) -> None:
        if len(c.waits) == 1:
            c.queue.append(Note(own, "不是這一次的代號"))
        if len(c.waits) == 2:
            c.queue.append(Note("autotrade_bell_別支程式", "000000000002"))

    conn.on_wait = hook
    with pytest.raises(BellError, match=r"5 秒內沒聽到"):
        bell.serve()
    assert bell.state.ok is not True and conn.closed


def test_ac3_a_line_that_only_hears_itself_is_not_fooled() -> None:
    # 2026-11-14 雲端實測：連到交易模式的連線池時，別人按的鈴聽不到，但自己按的那一聲會跟著自己那一句的回應回來。
    # 所以測試鈴一定由另一條連線按；聽的那條連線除了 LISTEN 什麼都不送。
    clock = Ticker()
    conn = ListenConn(clock, hears=False)
    bell, _ = bell_with(conn, clock)
    with pytest.raises(BellError):
        bell.serve()
    assert all(sql.startswith("LISTEN ") for sql, _ in conn.executed)
    assert len(conn.pings()) == 1 and bell.state.ok is not True


def test_ac3_a_deaf_line_is_dropped_and_retried_with_growing_gaps() -> None:
    clock = Ticker()
    naps: list[float] = []
    conn = ListenConn(clock, hears=False)  # 例如連到交易模式的連線池：連得上、但永遠聽不到
    bell, opened = bell_with(conn, clock, retry_s=2.0, retry_max_s=10.0)

    def nap(seconds: float) -> None:
        naps.append(seconds)
        if len(naps) == 5:
            bell.close()

    bell.nap = nap
    bell.run_forever()
    assert naps == [2.0, 4.0, 8.0, 10.0, 10.0] and len(opened) == 5
    assert bell.state.ok is False and "沒聽到" in bell.state.detail


def test_ac3_gaps_start_over_after_the_line_was_good() -> None:
    clock = Ticker()
    naps: list[float] = []
    good = ListenConn(clock)
    plan: list[ListenConn | None] = [None, None, good, None]
    opened: list[ListenConn | None] = []

    def connect() -> ListenConn:
        opened.append(plan[len(opened)])
        if opened[-1] is None:
            raise FakeDbError("連不上")
        return good

    def break_later(c: ListenConn) -> None:
        if len(c.waits) == 2:
            c.broken = True  # 確認聽得到之後線路斷了

    good.on_wait = break_later
    bell = PgBell(
        connect,
        lambda: good.sender,
        errors=(FakeDbError,),
        clock=clock,
        token=tokens(),
        retry_s=2.0,
    )

    def nap(seconds: float) -> None:
        naps.append(seconds)
        if len(naps) == 4:
            bell.close()

    bell.nap = nap
    bell.run_forever()
    assert naps == [2.0, 4.0, 2.0, 4.0]  # 好過一次之後，重連的間隔從頭算
    assert bell.state == BellState(False, "連不上")


# ---- AC4 只會變快、不會變慢


def test_ac4_nothing_the_bell_does_can_reach_the_relay_loop() -> None:
    clock = Ticker()
    naps: list[float] = []

    def connect() -> ListenConn:
        raise RuntimeError("沒料到的錯誤")

    bell = PgBell(connect, connect, clock=clock, token=tokens())

    def nap(seconds: float) -> None:
        naps.append(seconds)
        if len(naps) == 2:
            bell.close()

    bell.nap = nap
    bell.run_forever()  # 不丟例外
    assert len(naps) == 2 and bell.state == BellState(False, "沒料到的錯誤")
    assert bell.wait(0.0) is False


def test_ac4_real_thread_hears_a_ring_and_stops_when_closed() -> None:
    conn = ListenConn(Ticker())

    def hook(c: ListenConn) -> None:
        time_module.sleep(0.005)
        if len(c.waits) == 3:
            c.queue.append(Note(CHANNEL, "autotrade_commands:1"))

    conn.on_wait = hook
    bell = PgBell(lambda: conn, lambda: conn.sender, check_s=0.05, heard_within_s=0.5)
    bell.start()
    bell.start()  # 叫兩次也只有一條執行緒
    try:
        assert bell.wait(5.0) is True
        assert bell.state.ok is True
    finally:
        bell.close()
    assert bell.join(5.0)


# ---- AC5 中繼程式的迴圈


@dataclass
class FakeBell:
    """照劇本回答的門鈴。劇本用完就按 Ctrl+C（讓 run 結束）。"""

    rings: list[bool]
    states: list[BellState] = field(default_factory=list)
    waits: list[float] = field(default_factory=list)
    closed: bool = False
    state: BellState = NOT_YET

    def wait(self, timeout_s: float) -> bool:
        self.waits.append(timeout_s)
        if len(self.waits) > len(self.rings):
            raise KeyboardInterrupt
        if self.states:
            self.state = self.states[len(self.waits) - 1]
        return self.rings[len(self.waits) - 1]

    def close(self) -> None:
        self.closed = True


def no_sleep(seconds: float) -> None:
    raise AssertionError("有門鈴的時候不應該再睡覺")


def test_ac5_a_ring_runs_a_round_at_once(tmp_path: Path) -> None:
    db = FakeDb()
    relay = Relay(db, "sim", tmp_path, CONTROL.poll_s)
    bell = FakeBell(rings=[False, True, False])
    said: list[str] = []
    with pytest.raises(KeyboardInterrupt):
        run(relay, log=said.append, bell=bell, sleep=no_sleep)
    assert db.calls.count("pending") == 4  # 開頭一輪＋每次等完（不管是時間到還是被叫醒）各一輪
    assert bell.waits == [CONTROL.poll_s] * 4  # 等的時間就是原本的輪詢間隔：沒有鈴也照樣每 2 秒一輪
    rung = [line for line in said if "門鈴響了" in line]
    assert len(rung) == 1 and "馬上去拿" in rung[0]


def test_ac5_bell_state_changes_are_said_once_each(tmp_path: Path) -> None:
    relay = Relay(FakeDb(), "sim", tmp_path, CONTROL.poll_s)
    ok, bad = BellState(True, ""), BellState(False, "測試鈴 5 秒內沒聽到")
    bell = FakeBell(rings=[False] * 6, states=[BellState(None, "還沒連上"), ok, ok, bad, bad, ok])
    said: list[str] = []
    with pytest.raises(KeyboardInterrupt):
        run(relay, log=said.append, bell=bell, sleep=no_sleep)
    about = [line.split(" ", 2)[2] for line in said if "門鈴" in line]
    assert about == [
        "門鈴接上了：資料庫一有新的指令會馬上知道",
        "門鈴聽不到：測試鈴 5 秒內沒聽到。照舊每 2 秒問一次，不影響遠端停止",
        "門鈴接上了：資料庫一有新的指令會馬上知道",
    ]


def test_ac5_without_a_bell_the_loop_is_the_old_one(tmp_path: Path) -> None:
    db = FakeDb()
    relay = Relay(db, "sim", tmp_path, CONTROL.poll_s)
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) == 3:
            raise KeyboardInterrupt

    said: list[str] = []
    with pytest.raises(KeyboardInterrupt):
        run(relay, log=said.append, sleep=sleep)
    assert slept == [CONTROL.poll_s] * 3 and db.calls.count("pending") == 3
    assert not any("門鈴" in line for line in said)


# ---- AC6 交易程式那一端


def test_ac6_the_command_file_is_noticed_as_soon_as_it_is_replaced(tmp_path: Path) -> None:
    source = FileControl(tmp_path, stale_s=CONTROL.stale_s)
    assert source.changed() is False  # 還沒有指令檔：沒有東西好讀
    relay = Relay(FakeDb(), "sim", tmp_path, CONTROL.poll_s)
    relay.step(at(0))
    assert source.changed() is True
    source.poll(at(0))
    assert source.changed() is False  # 讀過了
    relay.step(at(0.1))  # 中繼程式又寫了一份新的（內容一樣也算）
    assert source.changed() is True
    source.poll(at(0.1))
    assert source.changed() is False


def test_ac6_the_loop_reads_at_once_when_told_the_file_changed() -> None:
    engine, _ = long_one()
    source = FakeSource()
    fresh = [False]
    loop = ControlLoop(engine, source, poll_s=2.0, urgent=lambda: fresh[0])
    loop.step(at(3))
    loop.step(at(3.5))
    assert source.polls == [at(3)]  # 平常照自己的節奏
    fresh[0] = True
    loop.step(at(3.6))
    assert source.polls == [at(3), at(3.6)]  # 指令檔換新了：不等
    fresh[0] = False
    loop.step(at(4.0))
    loop.step(at(5.6))
    assert source.polls == [at(3), at(3.6), at(5.6)]  # 之後從那一次起算

    quiet = FakeSource()
    plain = ControlLoop(engine, quiet, poll_s=2.0)
    plain.step(at(6))
    plain.step(at(7))
    assert quiet.polls == [at(6)]  # 沒接這個檢查的行為不變


# ---- AC7 整條路不等輪詢


def test_ac7_ring_to_halt_without_waiting_for_any_poll(tmp_path: Path) -> None:
    engine, broker = long_one()
    db = FakeDb()
    relay = Relay(db, "sim", tmp_path, CONTROL.poll_s)
    source = FileControl(tmp_path, CONTROL.stale_s)
    loop = ControlLoop(engine, source, CONTROL.poll_s, urgent=source.changed)
    bell = FakeBell(rings=[True])
    said: list[str] = []

    rounds = [0]
    real_step = relay.step

    def step(now: Any) -> Any:
        rounds[0] += 1
        if rounds[0] == 1:
            report = real_step(at(3.0))  # 平常的一輪
            loop.step(at(3.0))
            assert engine.risk.halt is None
            db.commands.append(stop(7, 3.1))  # 使用者按了停止，資料庫發出通知
            return report
        report = real_step(at(3.2))  # 被門鈴叫醒：離上一輪才 0.2 秒
        loop.step(at(3.3))  # 交易程式的下一圈：離它上次讀才 0.3 秒
        return report

    relay.step = step  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt):
        run(relay, log=said.append, bell=bell, sleep=no_sleep)
    assert engine.risk.halt == HALT_KILL and broker.submitted[-1].side.name == "SELL"
    assert rounds[0] == 2

    # 對照：沒有接「指令檔換新就馬上讀」的話，同樣的時間點還沒停
    engine2, _ = long_one()
    folder = tmp_path / "plain"
    folder.mkdir()
    db2 = FakeDb()
    relay2 = Relay(db2, "live", folder, CONTROL.poll_s)
    loop2 = ControlLoop(engine2, FileControl(folder, CONTROL.stale_s), CONTROL.poll_s)
    relay2.step(at(3.0))
    loop2.step(at(3.0))
    db2.commands.append(stop(7, 3.1))
    relay2.step(at(3.2))
    loop2.step(at(3.3))
    assert engine2.risk.halt is None
    loop2.step(at(5.0))
    assert engine2.risk.halt == HALT_KILL


# ---- AC8 指令與後台


@pytest.fixture
def fake_psycopg(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakePsycopg:
        Error = FakeDbError

        @staticmethod
        def connect(dsn: str, **kwargs: Any) -> FakeConn:
            calls.append((dsn, kwargs))
            return FakeConn(rows=[])

    real_import = importlib.import_module

    def fake_import(name: str, package: str | None = None) -> Any:
        return FakePsycopg if name == "psycopg" else real_import(name, package)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    monkeypatch.setenv("AUTOTRADE_TEST_DSN", "postgresql://u:p@h:6543/db")
    return calls


def test_ac8_open_bell_listens_on_its_own_connection_and_port(
    fake_psycopg: list[tuple[str, dict[str, Any]]],
) -> None:
    bell = open_bell("postgresql://u:p@h:6543/db", timeout_s=2.0, port=5432)
    assert fake_psycopg == []  # 還沒開始聽之前不連線
    bell.connect()
    dsn, kwargs = fake_psycopg[0]
    assert dsn == "postgresql://u:p@h:6543/db" and kwargs["port"] == 5432
    # 通知要馬上收得到；線路斷了要發現
    assert kwargs["autocommit"] is True and kwargs["keepalives"] == 1
    bell.connect_ping()  # 按測試鈴的那一條走平常的埠，跟網站寫入指令走的路一樣
    assert "port" not in fake_psycopg[1][1] and fake_psycopg[1][1]["autocommit"] is True
    open_bell("postgresql://u:p@h:5433/db", timeout_s=2.0).connect()
    assert "port" not in fake_psycopg[2][1]  # 沒講就用連線字串自己的埠


def test_ac8_relay_command_opens_the_bell_only_when_asked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_psycopg: list[tuple[str, dict[str, Any]]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[dict[str, Any]] = []

    def fake_run(relay: Relay, **kwargs: Any) -> None:
        seen.append({"relay": relay, **kwargs})

    started: list[PgBell] = []
    monkeypatch.setattr(cli_module, "run", fake_run)
    monkeypatch.setattr(PgBell, "start", lambda self: started.append(self))
    args = ["relay", "--config", str(CONFIG_PATH), "--dir", str(tmp_path)]
    args += ["--dsn-env", "AUTOTRADE_TEST_DSN"]

    assert main(args) == 0
    assert seen[-1]["bell"] is None  # 沒講就沒有門鈴，跟 A-011 一樣

    assert main([*args, "--bell", "--bell-port", "5432"]) == 0
    bell = seen[-1]["bell"]
    assert isinstance(bell, PgBell) and started == [bell]  # 開始在背景聽
    bell.connect()
    assert fake_psycopg[-1][1]["port"] == 5432
    made: list[tuple[str, tuple[Any, ...]]] = []
    relay = seen[-1]["relay"]
    relay.db._connect = lambda: FakeConn(rows=[], executed=made)
    relay.ensure()  # 建表的時候順便把門鈴的觸發器建好
    assert [sql for sql, _ in made if sql.startswith(("CREATE", "ALTER"))] == [
        *COMMANDS_DDL,
        *BELL_DDL,
    ]

    assert main([*args, "--bell", "--once"]) == 0
    assert seen[-1]["bell"] is None and len(started) == 1  # 只跑一輪不需要門鈴

    with pytest.raises(SystemExit):
        main([*args, "--bell-port", "5432"])  # 只給埠、沒說要開門鈴
    assert "--bell-port 要跟 --bell 一起用" in capsys.readouterr().err


def test_ac8_admin_starts_every_relay_with_the_bell(tmp_path: Path) -> None:
    config = tmp_path / "tmf_sim.toml"
    config.write_text(CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    ticks = tmp_path / "Daily_2026_11_13.csv"
    ticks.write_text("x\n", encoding="utf-8")
    spawn = FakeSpawn()
    jobs = Jobs(tmp_path, spawn=spawn, python="py", bell_ports={"TWQUANT_SUPABASE_DSN": 5432})
    try:
        jobs.start_run(RunRequest(config, "momentum", None, "live", "TWQUANT_SUPABASE_DSN"))
        cloud = spawn.calls[1]
        assert cloud[3] == "relay" and "--bell" in cloud
        assert cloud[cloud.index("--bell-port") + 1] == "5432"
        jobs.stop_run()
        jobs.start_drill(DrillRequest(config, ticks, TODAY, "09:00:00", 1.0, 1.0, "TWQUANT_PG_DSN"))
        local = spawn.calls[-2]
        assert local[3] == "relay" and "--bell" in local and "--bell-port" not in local
    finally:
        jobs.close()


def test_ac8_admin_command_takes_bell_ports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    got: list[dict[str, Any]] = []

    def fake_admin(*args: Any, **kwargs: Any) -> int:
        got.append(kwargs)
        return 0

    monkeypatch.setattr(cli_module, "run_admin", fake_admin)
    base = ["admin", "--config-dir", str(tmp_path), "--data-dir", str(tmp_path)]
    base += ["--ticks-dir", str(tmp_path), "--no-open"]
    assert main([*base, "--bell-port", "TWQUANT_SUPABASE_DSN=5432"]) == 0
    assert got[-1]["bell_ports"] == {"TWQUANT_SUPABASE_DSN": 5432}
    assert main(base) == 0 and got[-1]["bell_ports"] == {}
    for bad in ("5432", "TWQUANT_SUPABASE_DSN=abc", "NOPE=5432"):
        with pytest.raises(SystemExit):
            main([*base, "--bell-port", bad])
