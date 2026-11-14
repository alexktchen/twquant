"""A-014 AC2：行情中繼。本機資料庫裡富邦錄的逐筆 → 一個只往後接的檔案，交易程式跟著讀。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

from autotrade import cli
from autotrade.cli import main
from autotrade.config import ConfigError
from autotrade.feed.follow import FollowFile
from autotrade.feed.recorded import HEADER, read_recorded_day
from autotrade.model import TAIPEI
from autotrade.tickrelay import (
    TICKS_SQL,
    PgTickDb,
    Row,
    TickDbError,
    TickRelay,
    fubon_symbol,
    run,
    third_wednesday,
)
from test_a011_drill import FakeClock

CONFIG_PATH = Path(__file__).parents[1] / "configs" / "tmf_sim.toml"
DAY = date(2026, 11, 16)


def stamp(seconds: float) -> str:
    ts = datetime(2026, 11, 16, 8, 45, tzinfo=TAIPEI) + timedelta(seconds=seconds)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%f") + "+08:00"


def row(seconds: float, price: float, size: int = 1, quote: bool = True) -> Row:
    return (stamp(seconds), price, size, price - 1 if quote else None, price if quote else None)


@dataclass
class FakeDb:
    """假的資料庫：rows 是「現在資料庫裡有的」，照寫入的先後排。"""

    rows: list[Row] = field(default_factory=list)
    fail: str | None = None
    asked: list[tuple[date, str, str]] = field(default_factory=list)

    def fetch(self, day: date, symbol: str, since: str) -> list[Row]:
        self.asked.append((day, symbol, since))
        if self.fail is not None:
            raise TickDbError(self.fail)
        return [r for r in self.rows if r[0] >= since]  # 同樣格式的時間字串可以直接比大小


def lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def test_ac2_front_month_symbol_follows_the_recorder_rule() -> None:
    assert third_wednesday(2026, 11) == date(2026, 11, 18)
    assert fubon_symbol("TMF", date(2026, 11, 13)) == "TMFK6"
    assert fubon_symbol("TMF", date(2026, 11, 17)) == "TMFK6"
    assert fubon_symbol("TMF", date(2026, 11, 18)) == "TMFL6"  # 結算日當天起用下個月
    assert fubon_symbol("MXF", date(2026, 12, 16)) == "MXFA7"  # 跨年
    assert fubon_symbol("TMF", date(2027, 1, 4)) == "TMFA7"


def test_ac2_appends_only_what_is_new(tmp_path: Path) -> None:
    out = tmp_path / "ticks.csv"
    db = FakeDb()
    relay = TickRelay(db, DAY, "TMFK6", out)
    assert relay.resume() == 0 and lines(out) == [",".join(HEADER)]
    assert relay.step().written == 0
    assert db.asked[-1] == (DAY, "TMFK6", "2026-11-16T00:00:00+08:00")
    db.rows += [row(0.1, 27000.0, 3), row(0.2, 27001.5, 1, quote=False)]
    report = relay.step()
    assert (report.ok, report.written, report.error) == (True, 2, None)
    assert lines(out)[1:] == [
        "2026-11-16T08:45:00.100000+08:00,27000,3,26999,27000",
        "2026-11-16T08:45:00.200000+08:00,27001.5,1,,",
    ]
    assert relay.step().written == 0 and len(lines(out)) == 3  # 沒有新的就不寫
    db.rows.append(row(0.3, 27002.0))
    assert relay.step().written == 1 and relay.total == 3


def test_ac2_several_ticks_at_the_same_instant_are_neither_lost_nor_doubled(
    tmp_path: Path,
) -> None:
    """同一毫秒好幾筆，而且分兩次才寫進資料庫：每一筆剛好寫一次。"""
    out = tmp_path / "ticks.csv"
    db = FakeDb([row(0.1, 27000.0), row(0.5, 27001.0), row(0.5, 27002.0)])
    relay = TickRelay(db, DAY, "TMFK6", out)
    relay.resume()
    assert relay.step().written == 3
    db.rows += [row(0.5, 27003.0), row(0.5, 27004.0)]  # 同一個時間又來兩筆
    assert relay.step().written == 2
    db.rows.append(row(0.9, 27005.0))
    assert relay.step().written == 1 and relay.step().written == 0
    prices = [line.split(",")[1] for line in lines(out)[1:]]
    assert prices == ["27000", "27001", "27002", "27003", "27004", "27005"]


def test_ac2_restart_continues_the_existing_file(tmp_path: Path) -> None:
    out = tmp_path / "ticks.csv"
    db = FakeDb([row(0.1, 27000.0), row(0.5, 27001.0), row(0.5, 27002.0)])
    first = TickRelay(db, DAY, "TMFK6", out)
    first.resume()
    first.step()
    db.rows += [row(0.5, 27003.0), row(1.0, 27004.0)]
    again = TickRelay(db, DAY, "TMFK6", out)  # 行情中繼重新啟動
    assert again.resume() == 3
    assert again.step().written == 2 and again.total == 5
    assert [line.split(",")[1] for line in lines(out)[1:]] == [
        "27000",
        "27001",
        "27002",
        "27003",
        "27004",
    ]


def test_ac2_refuses_a_file_it_did_not_write_or_that_was_torn(tmp_path: Path) -> None:
    out = tmp_path / "ticks.csv"
    out.write_text("time,price\n", encoding="utf-8")
    with pytest.raises(ValueError, match="不是行情中繼寫的檔案"):
        TickRelay(FakeDb(), DAY, "TMFK6", out).resume()
    out.write_text(",".join(HEADER) + "\n2026-11-16T08:45:00.1", encoding="utf-8")
    with pytest.raises(ValueError, match="最後一行沒有寫完"):
        TickRelay(FakeDb(), DAY, "TMFK6", out).resume()


def test_ac2_database_trouble_is_reported_not_raised(tmp_path: Path) -> None:
    out = tmp_path / "ticks.csv"
    db = FakeDb([row(0.1, 27000.0)], fail="connection refused")
    relay = TickRelay(db, DAY, "TMFK6", out)
    relay.resume()
    report = relay.step()
    assert (report.ok, report.written, report.error) == (False, 0, "connection refused")
    db.fail = None
    assert relay.step().written == 1  # 恢復之後接著寫，沒有漏


def test_ac2_the_file_reads_back_as_the_same_ticks(tmp_path: Path) -> None:
    """行情中繼寫的檔：盤中跟著讀、收盤後整個讀，都是同一份行情。"""
    out = tmp_path / "ticks.csv"
    db = FakeDb([row(i * 0.25, 27000.0 + i, 1 + i % 2, quote=i % 3 != 0) for i in range(40)])
    relay = TickRelay(db, DAY, "TMFK6", out)
    relay.resume()
    relay.step()
    followed = FollowFile(out, DAY, time(8, 45), time(13, 45)).poll(datetime.now(TAIPEI))
    assert followed == read_recorded_day(out, DAY, time(8, 45), time(13, 45))
    assert [(t.price, t.qty) for t in followed] == [(27000.0 + i, 1 + i % 2) for i in range(40)]
    assert followed[0].bid is None and followed[1].bid == 27000.0


def test_ac2_only_reads_the_database() -> None:
    sql = TICKS_SQL.upper()
    assert sql.startswith("SELECT ") and "MARKET_LIVE.TICKS" in sql
    assert not any(word in sql for word in ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP"))
    assert "ORDER BY T, CTID" in sql


class FakeConn:
    def __init__(self, rows: list[tuple[Any, ...]], fail: bool = False) -> None:
        self.rows, self.fail, self.closed = rows, fail, False

    def execute(self, sql: str, params: tuple[Any, ...]) -> FakeConn:
        if self.fail:
            raise OSError("server closed the connection")
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows

    def close(self) -> None:
        self.closed = True


def test_ac2_reconnects_after_an_error() -> None:
    conns = [FakeConn([], fail=True), FakeConn([(stamp(0.1), 27000, 2, None, 27001.0)])]
    made: list[FakeConn] = []

    def connect() -> FakeConn:
        made.append(conns[len(made)])
        return made[-1]

    db = PgTickDb(connect, errors=(OSError,))
    with pytest.raises(TickDbError, match="server closed the connection"):
        db.fetch(DAY, "TMFK6", stamp(0))
    assert made[0].closed
    assert db.fetch(DAY, "TMFK6", stamp(0)) == [(stamp(0.1), 27000.0, 2, None, 27001.0)]
    assert len(made) == 2


def test_ac2_run_loop_prints_changes_not_every_round(tmp_path: Path) -> None:
    out = tmp_path / "ticks.csv"
    db = FakeDb(fail="connection refused")
    relay = TickRelay(db, DAY, "TMFK6", out)
    clock = FakeClock(datetime(2026, 11, 16, 8, 44, 0, tzinfo=TAIPEI))
    said: list[str] = []
    rounds = 0

    def sleep(seconds: float) -> None:
        nonlocal rounds
        rounds += 1
        clock.sleep(seconds)
        if rounds == 3:
            db.fail = None
        if rounds == 5:
            db.rows += [row(0.1, 27000.0), row(0.2, 27001.0)]
        if rounds == 400:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(relay, 0.5, log=said.append, sleep=sleep, clock=clock, report_s=60.0)
    assert said[0] == f"行情中繼開始：2026-11-16 TMFK6 → {out}"
    assert said[1].endswith("讀不到資料庫（會一直再試）：connection refused")
    assert said[2].endswith("資料庫讀得到")
    assert said[3].endswith("開始有行情了：先接上 2 筆")
    assert [line.split(" ", 1)[1] for line in said[4:]] == ["已接 2 筆"] * 3  # 每分鐘一行
    assert relay.total == 2


def test_ac10_ticks_command_writes_the_file_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = FakeDb([row(0.1, 27000.0)])
    opened: list[str] = []

    def fake_open(dsn: str, timeout_s: float) -> FakeDb:
        opened.append(dsn)
        return db

    monkeypatch.setattr(cli, "open_tick_db", fake_open)
    monkeypatch.setenv("TEST_TICKS_DSN", "postgresql://local/x")
    out = tmp_path / "ticks.csv"
    args = ["ticks", "--config", str(CONFIG_PATH), "--out", str(out), "--dsn-env", "TEST_TICKS_DSN"]
    assert main([*args, "--day", "2026-11-16", "--once"]) == 0
    assert db.asked[-1][:2] == (DAY, "TMFK6") and opened == ["postgresql://local/x"]
    assert len(lines(out)) == 2
    printed = capsys.readouterr().out
    assert "行情中繼開始：2026-11-16 TMFK6" in printed and "postgresql" not in printed
    assert main([*args, "--day", "2026-11-16", "--symbol", "TMFL6", "--once"]) == 0
    assert db.asked[-1][1] == "TMFL6"
    monkeypatch.delenv("TEST_TICKS_DSN")
    with pytest.raises(ConfigError, match="TEST_TICKS_DSN 沒有設定"):
        main([*args, "--once"])
    with pytest.raises(FileNotFoundError):
        main([*args[:3], "--out", str(tmp_path / "nope" / "t.csv"), "--once"])
