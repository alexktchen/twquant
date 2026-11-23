"""A-024：agent 自己的紀錄（開始、每一個遠端操作都抄進來）與啟動時的漏錄檢查。"""

from __future__ import annotations

import tomllib
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

from autotrade import cli
from autotrade.config import ConfigError, parse_config
from autotrade.jobs import HEADLINE
from autotrade.model import TAIPEI
from autotrade.tickrelay import RecordCheck, TickRelay, file_times, recording_note, run
from test_a011_drill import FakeClock
from test_a014_ticks import CONFIG_PATH, DAY, FakeDb, row
from test_a019_agent import Spawned, clock

CHECK = RecordCheck(time(8, 45), time(13, 45), late_s=120.0, gap_s=120.0)
OPEN = datetime(2026, 11, 16, 8, 45, tzinfo=TAIPEI)


def ticks(*seconds: float) -> list[datetime]:
    return [OPEN + timedelta(seconds=s) for s in seconds]


def every(start: float, stop: float, step: float = 30.0) -> list[float]:
    out, at = [], start
    while at <= stop:
        out.append(at)
        at += step
    return out


# ---- AC1 漏錄檢查


def test_ac1_a_full_day_passes() -> None:
    note = recording_note(ticks(*every(0.5, 17999.0)), clock(13, 47), CHECK)
    assert note.startswith("今天的行情檢查過了：08:45:00～13:44:30") and "沒有發現漏錄" in note


def test_ac1_nothing_recorded_since_the_open_is_flagged() -> None:
    note = recording_note([], clock(21, 14), CHECK)
    assert note.startswith("⚠ 今天的行情可能整段漏錄") and "08:45 開盤到 13:45:00" in note
    # 盤前的夜盤逐筆不算數
    assert recording_note(ticks(-3600.0), clock(9, 0), CHECK).startswith("⚠ 今天的行情可能整段漏錄")


def test_ac1_late_start_gaps_and_an_early_end_are_each_named() -> None:
    seconds = [
        *every(600.0, 3600.0),
        *every(5400.0, 9000.0),
    ]  # 晚 10 分鐘開始、中間斷 30 分鐘、11:15 就停
    note = recording_note(ticks(*seconds), clock(13, 47), CHECK)
    assert note.startswith("⚠ 今天的行情可能有漏錄：")
    assert "開頭缺 08:45:00～08:55:00" in note
    assert "中間有 1 段超過 120 秒沒有行情（最長 09:45:00～10:15:00）" in note
    assert "行情停在 11:15:00（檢查到 13:45:00）" in note
    # 盤中檢查：只看到現在，不會把還沒到的時間當成漏
    assert "沒有發現漏錄" in recording_note(ticks(*every(1.0, 100.0)), clock(8, 47), CHECK)


def test_ac1_checks_run_after_the_open_and_after_the_close_and_not_on_weekends() -> None:
    assert CHECK.due(DAY) == [clock(8, 47), clock(13, 47)]
    assert CHECK.due(DAY.replace(day=14)) == []  # 星期六


def test_ac1_the_relay_reports_once_when_started_after_the_close(tmp_path: Path) -> None:
    out = tmp_path / "ticks.csv"
    relay = TickRelay(
        FakeDb(rows=[row(s, 27000.0) for s in every(1.0, 17999.0)]), DAY, "TMFK6", out
    )
    now = FakeClock(clock(21, 14))
    said: list[str] = []
    rounds = 0

    def sleep(seconds: float) -> None:
        nonlocal rounds
        rounds += 1
        now.sleep(seconds)
        if rounds == 5:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(relay, 0.5, log=said.append, sleep=sleep, clock=now, check=CHECK)
    marked = [line for line in said if HEADLINE in line]
    assert len(marked) == 1 and "沒有發現漏錄" in marked[0]  # 開盤後、收盤後兩次都到期了：只講一次
    assert len(file_times(out)) == relay.total


def test_ac1_an_unreachable_database_is_said_once_and_checked_when_it_is_back(
    tmp_path: Path,
) -> None:
    db = FakeDb(fail="connection timeout expired")
    relay = TickRelay(db, DAY, "TMFK6", tmp_path / "ticks.csv")
    now = FakeClock(clock(9, 30))
    said: list[str] = []
    rounds = 0

    def sleep(seconds: float) -> None:
        nonlocal rounds
        rounds += 1
        now.sleep(seconds)
        if rounds == 4:
            db.fail = None
        if rounds == 8:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(relay, 0.5, log=said.append, sleep=sleep, clock=now, check=CHECK)
    marked = [line.split(HEADLINE, 1)[1].strip() for line in said if HEADLINE in line]
    assert len(marked) == 2
    assert marked[0].startswith("⚠ 讀不到行情資料庫") and "Docker" in marked[0]
    assert marked[1].startswith("⚠ 今天的行情可能整段漏錄")


def test_ac1_before_the_open_and_without_settings_nothing_is_checked(tmp_path: Path) -> None:
    for check, start in ((CHECK, clock(8, 0)), (None, clock(21, 0))):
        relay = TickRelay(FakeDb(), DAY, "TMFK6", tmp_path / f"{start:%H}.csv")
        now = FakeClock(start)
        said: list[str] = []

        def sleep(seconds: float, now: FakeClock = now, end: datetime = start) -> None:
            now.sleep(seconds)
            if now() >= end + timedelta(seconds=3):
                raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            run(relay, 0.5, log=said.append, sleep=sleep, clock=now, check=check)
        assert not [line for line in said if HEADLINE in line]


def test_ac1_settings_come_from_the_config_file() -> None:
    raw = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg = parse_config(raw)
    assert cfg.agent is not None
    assert cfg.agent.record_late_s is not None and cfg.agent.record_gap_s is not None

    def with_agent(**changes: object) -> dict[str, Any]:
        agent = {k: v for k, v in {**raw["agent"], **changes}.items() if v is not None}
        return {**raw, "agent": agent}

    with pytest.raises(ConfigError, match="一起設"):
        parse_config(with_agent(record_gap_s=None))
    with pytest.raises(ConfigError, match="大於 0"):
        parse_config(with_agent(record_gap_s=-1.0))
    off = parse_config(with_agent(record_gap_s=None, record_late_s=None)).agent
    assert off is not None and off.record_late_s is None  # 都不設＝不檢查


# ---- AC2 交易程式與看門程式講的每一行都抄進 agent 的紀錄


def test_ac2_the_trader_and_watchdog_lines_are_copied_and_others_only_when_marked(
    tmp_path: Path,
) -> None:
    world = Spawned(tmp_path)
    folder = tmp_path / "2026-11-16"
    folder.mkdir()
    (folder / "run.log").write_text("昨天晚上留下的舊紀錄\n", encoding="utf-8")
    sup = world.supervisor()
    sup.step(clock(8, 30))
    with (folder / "run.log").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write("08:41:51  收到遠端開始自動交易（第 60 號，08:41:50 按下）\n寫到一半")
    with (folder / "relay.log").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"08:47:00 已接 100 筆\n08:47:00 {HEADLINE} ⚠ 今天的行情可能有漏錄：開頭缺\n")
    sup.step(clock(8, 42))
    copied = [line for line in world.said if line.startswith("[")]
    assert copied == [
        "[relay] 08:47:00 ※ ⚠ 今天的行情可能有漏錄：開頭缺",
        "[run] 08:41:51  收到遠端開始自動交易（第 60 號，08:41:50 按下）",
    ]
    with (folder / "run.log").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write("的那一行\n09:00:01  緊急停止\n")
    sup.close()  # 收掉之前剩下的也抄
    assert [line for line in world.said if line.startswith("[")][2:] == [
        "[run] 寫到一半的那一行",
        "[run] 09:00:01  緊急停止",
    ]
    assert not [line for line in world.said if "舊紀錄" in line or "已接" in line]


# ---- AC3 agent.log


def test_ac3_everything_the_agent_says_also_goes_to_its_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    say = cli.agent_log(tmp_path / cli.AGENT_LOG)
    say("交易 agent 開始")
    say("[run] 08:41:51  收到遠端開始自動交易")
    assert capsys.readouterr().out.splitlines() == [
        "交易 agent 開始",
        "[run] 08:41:51  收到遠端開始自動交易",
    ]
    written = (tmp_path / cli.AGENT_LOG).read_text(encoding="utf-8").splitlines()
    assert [line[20:] for line in written] == [
        "交易 agent 開始",
        "[run] 08:41:51  收到遠端開始自動交易",
    ]
    assert all(datetime.fromisoformat(line[:19]) for line in written)  # 每行前面有日期時間
