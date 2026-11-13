"""A-014：模擬交易程式（盤中常駐）。使用者 2026-11-14：「從後台啟動交易程式」。

這裡測交易程式本身與它讀行情的方式；行情中繼在 test_a014_ticks.py，後台在 test_a014_admin.py。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

from autotrade import cli
from autotrade.broker.sim import SimBroker
from autotrade.cli import main, run_trade
from autotrade.config import Config, ConfigError, load_config
from autotrade.control import outbox_name
from autotrade.engine import Engine, Event
from autotrade.feed.follow import FollowFile, PacedTicks
from autotrade.feed.recorded import HEADER, read_recorded_day
from autotrade.feed.replay import FeedError
from autotrade.journal import read_journal
from autotrade.model import ACTION_STOP, TAIPEI, Command, Fill, OrderRequest, Side, Tick
from autotrade.risk import HALT_KILL
from autotrade.runner import (
    QUIT_NAME,
    Runner,
    earlier_stop,
    paced_clock,
    release_name,
)
from test_a011_drill import FakeClock, write_inbox

CONFIG_PATH = Path(__file__).parents[1] / "configs" / "tmf_sim.toml"
DAY = date(2026, 11, 16)  # 星期一
OPEN = datetime(2026, 11, 16, 8, 45, tzinfo=TAIPEI)


def small_cfg() -> Config:
    """只開 5 分鐘的一天（08:45～08:50），測試才跑得快。"""
    base = load_config(CONFIG_PATH)
    return replace(
        base,
        session=replace(base.session, start=time(8, 45), end=time(8, 50)),
        risk=replace(
            base.risk,
            entry_start=time(8, 45),
            entry_end=time(8, 48),
            force_flat_time=time(8, 49),
        ),
    )


def day_ticks(n: int = 600) -> list[Tick]:
    """每 0.5 秒一筆，價格上上下下；08:45:00 開始。"""
    return [
        Tick(ts=OPEN + timedelta(seconds=i * 0.5), price=27000.0 + (i * 7) % 40, qty=1 + i % 3)
        for i in range(n)
    ]


def line_of(tick: Tick) -> str:
    return f"{tick.ts.isoformat(timespec='microseconds')},{tick.price:g},{tick.qty},,\n"


@dataclass
class InOut:
    """測試用的策略：到了 buy_at 買 1 口，到了 sell_at 賣掉；被退單就回到上一步。"""

    buy_at: time = time(8, 45, 30)
    sell_at: time = time(8, 47, 0)
    state: str = "wait"

    def on_tick(self, tick: Tick, position: int) -> list[OrderRequest]:
        if self.state == "wait" and tick.ts.time() >= self.buy_at:
            self.state = "buying"
            return [OrderRequest(ts=tick.ts, symbol="TMF", side=Side.BUY, qty=1, reason="進")]
        if self.state == "long" and tick.ts.time() >= self.sell_at:
            self.state = "selling"
            return [OrderRequest(ts=tick.ts, symbol="TMF", side=Side.SELL, qty=1, reason="出")]
        return []

    def on_fill(self, fill: Fill) -> None:
        self.state = "long" if fill.side == Side.BUY else "done"

    def on_reject(self, req: OrderRequest, reason: str) -> None:
        self.state = {"buying": "wait", "selling": "long"}.get(self.state, self.state)


def replay_fills(cfg: Config, ticks: list[Tick]) -> list[tuple[str, str, int, float]]:
    """同一份行情直接重播（不經過交易程式的迴圈）——交易程式的結果要跟它一樣。"""
    engine = Engine(cfg, InOut(), SimBroker(cfg.sim))
    for tick in ticks:
        engine.on_tick(tick)
    engine.finish()
    return [(f.ts.isoformat(), f.side.name, f.qty, f.price) for f in engine.ledger.fills]


def journal_fills(folder: Path) -> list[tuple[str, str, int, float]]:
    """最新那一本日誌裡的成交。"""
    latest = sorted(folder.glob("journal_*.jsonl"))[-1]
    return [
        (r.ts.isoformat(), r.data["side"], int(r.data["qty"]), float(r.data["price"]))
        for r in read_journal(latest)
        if r.kind == "fill"
    ]


@dataclass
class Feeder:
    """假的行情中繼：時間到了才把逐筆接到檔案後面（比時鐘慢 lag_s 秒）。"""

    path: Path
    ticks: list[Tick]
    lag_s: float = 1.0
    sent: int = 0

    def __post_init__(self) -> None:
        if self.path.exists():  # 重開時接著既有的檔案寫，跟真的行情中繼一樣
            self.sent = len(self.path.read_text(encoding="utf-8").splitlines()) - 1

    def pump(self, now: datetime) -> None:
        if not self.path.exists():
            self.path.write_text(",".join(HEADER) + "\n", encoding="utf-8", newline="\n")
        limit = now - timedelta(seconds=self.lag_s)
        text = ""
        while self.sent < len(self.ticks) and self.ticks[self.sent].ts <= limit:
            text += line_of(self.ticks[self.sent])
            self.sent += 1
        if text:
            with self.path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(text)


@dataclass
class Rig:
    """一次啟動：假時鐘、假的兩支中繼程式、交易程式。"""

    folder: Path
    clock: FakeClock
    ticks: list[Tick] = field(default_factory=day_ticks)
    said: list[str] = field(default_factory=list)
    commands: Callable[[datetime], list[Command]] = lambda now: []
    relay: bool = True
    hook: Callable[[datetime], None] = lambda now: None

    def runner(self, cfg: Config | None = None, **kw: Any) -> Runner:
        feeder = Feeder(self.folder / "ticks.csv", self.ticks)
        feeder.pump(self.clock.now)

        def sleep(seconds: float) -> None:
            self.clock.sleep(seconds)
            feeder.pump(self.clock.now)
            if self.relay:
                write_inbox(self.folder, self.clock.now, self.commands(self.clock.now))
            self.hook(self.clock.now)

        if self.relay:
            write_inbox(self.folder, self.clock.now, [])
        cfg = cfg or small_cfg()
        return Runner(
            cfg,
            kw.pop("strategy", InOut()),
            FollowFile(self.folder / "ticks.csv", DAY, cfg.session.start, cfg.session.end),
            self.folder,
            "inout",
            clock=self.clock,
            sleep=sleep,
            say=self.said.append,
            close_grace_s=5.0,
            **kw,
        )


def at(hh: int, mm: int, ss: int = 0) -> datetime:
    return datetime(2026, 11, 16, hh, mm, ss, tzinfo=TAIPEI)


# ---- AC1 跟著一直在長大的逐筆檔讀


def test_ac1_follow_reads_only_complete_new_lines(tmp_path: Path) -> None:
    path = tmp_path / "ticks.csv"
    feed = FollowFile(path, DAY, time(8, 45), time(13, 45))
    assert feed.poll(OPEN) == []  # 檔案還沒出現＝還沒有行情
    ticks = day_ticks(4)
    path.write_text(",".join(HEADER) + "\n" + line_of(ticks[0]), encoding="utf-8", newline="\n")
    assert feed.poll(OPEN) == [ticks[0]]
    assert feed.poll(OPEN) == []
    half = line_of(ticks[1])
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(half[:20])  # 寫到一半
    assert feed.poll(OPEN) == []
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(half[20:] + line_of(ticks[2]) + line_of(ticks[3])[:5])
    assert feed.poll(OPEN) == [ticks[1], ticks[2]]


def test_ac1_follow_keeps_only_this_day_and_session(tmp_path: Path) -> None:
    path = tmp_path / "ticks.csv"
    rows = [
        "2026-11-16T08:44:59.900000+08:00,27000,1,26999,27000",  # 開盤前
        "2026-11-16T08:45:00.100000+08:00,27001,2,27000,27001",
        "2026-11-16T13:45:00.000000+08:00,27002,1,,",
        "2026-11-16T13:45:00.100000+08:00,27003,1,,",  # 收盤後
    ]
    path.write_text("\n".join([",".join(HEADER), *rows]) + "\n", encoding="utf-8")
    got = FollowFile(path, DAY, time(8, 45), time(13, 45)).poll(OPEN)
    assert [(t.price, t.qty, t.bid, t.ask) for t in got] == [
        (27001.0, 2, 27000.0, 27001.0),
        (27002.0, 1, None, None),
    ]
    assert FollowFile(path, date(2026, 11, 17), time(8, 45), time(13, 45)).poll(OPEN) == []


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("time,price,size,bid,ask\n", "欄位要是"),
        ("t,price,size,bid,ask\n2026-11-16T09:00:00+08:00,27000,1\n", "欄位數不對"),
        ("t,price,size,bid,ask\n2026-11-16T09:00:00,27000,1,,\n", "時區"),
        (
            "t,price,size,bid,ask\n2026-11-16T09:00:01+08:00,27000,1,,\n"
            "2026-11-16T09:00:00+08:00,27000,1,,\n",
            "比前一列早",
        ),
        ("t,price,size,bid,ask\n2026-11-16T09:00:00+08:00,abc,1,,\n", "不是數字"),
        ("t,price,size,bid,ask\n2026-11-16T09:00:00+08:00,27000,0,,\n", "口數要大於 0"),
    ],
)
def test_ac1_follow_raises_on_bad_data(tmp_path: Path, text: str, why: str) -> None:
    path = tmp_path / "ticks.csv"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(FeedError, match=why):
        FollowFile(path, DAY, time(8, 45), time(13, 45)).poll(OPEN)


def test_ac1_follow_refuses_a_file_that_shrinks_or_vanishes(tmp_path: Path) -> None:
    path = tmp_path / "ticks.csv"
    ticks = day_ticks(3)
    path.write_text(",".join(HEADER) + "\n" + "".join(map(line_of, ticks)), encoding="utf-8")
    feed = FollowFile(path, DAY, time(8, 45), time(13, 45))
    assert len(feed.poll(OPEN)) == 3
    path.write_text(",".join(HEADER) + "\n", encoding="utf-8")
    with pytest.raises(FeedError, match="變短了"):
        feed.poll(OPEN)
    path.unlink()
    with pytest.raises(FeedError, match="不見了"):
        feed.poll(OPEN)


def test_ac1_a_finished_follow_file_replays_the_same(tmp_path: Path) -> None:
    """盤中跟著讀的檔，收盤後拿去 replay --feed recorded 讀出來要是同一份行情。"""
    path = tmp_path / "ticks.csv"
    ticks = day_ticks(50)
    path.write_text(",".join(HEADER) + "\n" + "".join(map(line_of, ticks)), encoding="utf-8")
    followed = FollowFile(path, DAY, time(8, 45), time(13, 45)).poll(OPEN)
    assert followed == read_recorded_day(path, DAY, time(8, 45), time(13, 45)) == ticks


# ---- AC3／AC4 主迴圈；結果只由行情決定


def test_ac3_a_normal_day_runs_to_the_close_and_matches_the_replay(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeClock(at(8, 44, 50)))
    status = tmp_path / "status.json"
    report = rig.runner(status=status).run()
    assert report.reason == "closed" and report.ok and report.position == 0
    assert report.ticks == 600 and report.fills == 2 and report.halt is None
    snap = json.loads(status.read_text(encoding="utf-8"))
    assert (snap["status"], snap["source"], snap["mode"], snap["day"]) == (
        "closed",
        "live",
        "sim",
        "2026-11-16",
    )
    assert snap["strategy"] == "inout" and snap["control"]["listening"] is True
    expected = replay_fills(small_cfg(), day_ticks())
    assert [(f["ts"], f["side"], f["qty"], f["price"]) for f in snap["fills"]] == expected
    assert journal_fills(tmp_path) == expected
    text = "\n".join(rig.said)
    assert (
        "模擬交易程式開始：TMF、策略 inout、2026-11-16（08:45～08:50）。會聽遠端緊急停止。" in text
    )
    assert "08:44:50 還沒開盤（08:45 開盤），等行情中。" in text
    assert "BUY 1 口" in text and "SELL 1 口" in text
    assert "程式結束（收盤，今天結束）：行情 600 筆、成交 2 筆、部位 0 口" in text
    assert report.day_pnl_twd is not None and f"今天損益 {report.day_pnl_twd:+,.0f} 元" in text


def test_ac3_status_is_written_while_running(tmp_path: Path) -> None:
    seen: list[dict[str, Any]] = []
    status = tmp_path / "status.json"

    def peek(now: datetime) -> None:
        if status.exists() and now.second % 30 == 0 and now.microsecond == 0:
            seen.append(json.loads(status.read_text(encoding="utf-8")))

    rig = Rig(tmp_path, FakeClock(at(8, 44, 50)), hook=peek)
    rig.runner(status=status, status_every_s=5.0).run()
    live = [s for s in seen if s["status"] == "live"]
    assert live and {s["position"] for s in live} == {0, 1}  # 盤中看得到部位在變
    assert all(s["source"] == "live" for s in live)


@pytest.mark.parametrize("start", [at(8, 46, 10), at(8, 49, 30), at(9, 30)])
def test_ac4_a_late_start_recomputes_from_the_first_tick(tmp_path: Path, start: datetime) -> None:
    """晚啟動（已經進場之後、強制平倉時間之後、收盤之後）：從今天第一筆重算，結果跟重播一樣。"""
    rig = Rig(tmp_path, FakeClock(start))
    report = rig.runner().run()
    assert report.reason == "closed" and report.ticks == 600
    assert journal_fills(tmp_path) == replay_fills(small_cfg(), day_ticks())
    assert any("補上今天到現在的行情" in line for line in rig.said)


@dataclass
class FakeNotifier:
    """記下交給通知的事件。"""

    events: list[Event] = field(default_factory=list)
    polls: list[datetime] = field(default_factory=list)
    closed: int = 0

    def on_event(self, event: Event) -> None:
        self.events.append(event)

    def poll(self, now: datetime) -> None:
        self.polls.append(now)

    def close(self) -> None:
        self.closed += 1


def test_ac4_catching_up_sends_no_notifications_and_runs_no_timers(tmp_path: Path) -> None:
    """補行情那一段算出來的成交不是現在發生的：不通知。進場在 08:45:30，啟動在 08:46:10。"""
    notifier = FakeNotifier()
    rig = Rig(tmp_path, FakeClock(at(8, 46, 10)))
    rig.runner(notifier=notifier).run()
    fills = [e for e in notifier.events if e.kind == "fill"]
    assert [e.data["side"] for e in fills] == ["SELL"]  # 買進是補行情時算出來的，沒有通知
    assert notifier.polls and min(notifier.polls) >= at(8, 46, 10) and notifier.closed == 1


def test_ac3_waits_for_the_command_relay_at_startup_before_raising_the_alarm(
    tmp_path: Path,
) -> None:
    """指令中繼跟交易程式是一起啟動的。2026-11-14 實際開起來，每次啟動都先記一筆「讀不到遠端指令」，
    兩秒後又恢復——手機也會跟著響一次假警報。剛啟動時先等它寫出第一份新的指令檔。"""
    started = at(8, 44, 50)
    write_inbox(tmp_path, at(8, 0), [])  # 早上那一次留下來的舊指令檔

    def late_relay(now: datetime) -> None:
        if now >= started + timedelta(seconds=3):
            write_inbox(tmp_path, now, [])

    rig = Rig(tmp_path, FakeClock(started), relay=False, hook=late_relay)
    rig.runner().run()
    assert not any("讀不到遠端指令" in line for line in rig.said)
    # 指令中繼一直沒起來：過了等待時間照樣報，只報一次
    other = tmp_path / "b"
    other.mkdir()
    rig = Rig(other, FakeClock(started), relay=False)
    rig.runner().run()
    downs = [line for line in rig.said if "讀不到遠端指令" in line]
    assert len(downs) == 1 and downs[0].startswith("08:45:05") and "中繼程式沒在跑" in downs[0]


def test_ac3_refuses_live_mode_and_missing_sections(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeClock(at(8, 44, 50)))
    with pytest.raises(ConfigError, match="只跑模擬"):
        rig.runner(replace(small_cfg(), mode="live")).run()
    with pytest.raises(ConfigError, match=r"\[control\]"):
        rig.runner(replace(small_cfg(), control=None)).run()
    with pytest.raises(ConfigError, match=r"\[export\]"):
        rig.runner(replace(small_cfg(), export=None), status=tmp_path / "s.json").run()
    with pytest.raises(FileNotFoundError):
        replace(rig.runner(), folder=tmp_path / "nope").run()


# ---- AC5 今天按過的遠端停止，重開之後還在


def pressed_at(when: datetime) -> Callable[[datetime], list[Command]]:
    return lambda now: (
        [Command(id=7, action=ACTION_STOP, day=DAY, created_at=when)] if now >= when else []
    )


def test_ac5_a_remote_stop_survives_a_restart_until_released(tmp_path: Path) -> None:
    pressed = at(8, 46, 30)
    quit_at = at(8, 47, 10)

    def quit_later(now: datetime) -> None:
        if now >= quit_at:
            (tmp_path / QUIT_NAME).write_text("x", encoding="utf-8")

    first = Rig(tmp_path, FakeClock(at(8, 44, 50)), commands=pressed_at(pressed), hook=quit_later)
    one = first.runner().run()
    assert (one.reason, one.halt, one.position, one.fills) == ("quit", HALT_KILL, 0, 2)
    stopped = journal_fills(tmp_path)
    assert stopped[1][1] == "SELL" and stopped[1][0] < at(8, 46, 40).isoformat()  # 被停止平掉的
    applied = earlier_stop(tmp_path, DAY)
    assert applied is not None and pressed <= applied <= pressed + timedelta(seconds=3)

    # 重開（08:47:30）：算到那個時間一樣停手、平倉，之後不交易；成交跟第一次一模一樣
    second = Rig(tmp_path, FakeClock(at(8, 47, 30)))
    two = second.runner().run()
    assert (two.reason, two.halt, two.position) == ("closed", HALT_KILL, 0)
    assert journal_fills(tmp_path) == stopped
    text = "\n".join(second.said)
    assert (
        f"今天 {applied:%H:%M:%S} 按過遠端緊急停止" in text and "套用今天稍早的遠端緊急停止" in text
    )
    assert not (tmp_path / QUIT_NAME).exists()  # 上一次的「結束」不會讓這一次馬上收工

    # 在電腦前明講解除再啟動：照常交易，結果等於整天重播；之後不用再講一次
    third = Rig(tmp_path, FakeClock(at(8, 48, 0)))
    three = third.runner(release_stop=True).run()
    assert three.halt is None and journal_fills(tmp_path) == replay_fills(small_cfg(), day_ticks())
    assert "已在這台電腦解除" in "\n".join(third.said)
    assert json.loads((tmp_path / release_name(DAY)).read_text(encoding="utf-8"))["at"]
    assert earlier_stop(tmp_path, DAY) is None
    fourth = Rig(tmp_path, FakeClock(at(8, 49, 0)))
    assert fourth.runner().run().halt is None


def test_ac5_earlier_stop_reads_the_results_file_strictly(tmp_path: Path) -> None:
    assert earlier_stop(tmp_path, DAY) is None
    path = tmp_path / outbox_name(DAY)

    def row(stage: str, code: str, when: datetime) -> str:
        return json.dumps({"command_id": 1, "stage": stage, "code": code, "at": when.isoformat()})

    path.write_text(
        row("ack", "ignored", at(8, 50)) + "\n" + row("done", "flat", at(8, 55)) + "\n",
        encoding="utf-8",
    )
    assert earlier_stop(tmp_path, DAY) is None  # 沒執行的指令、收尾的回報都不算
    with path.open("a", encoding="utf-8") as fh:
        fh.write(row("ack", "flattening", at(9, 10)) + "\n" + row("ack", "flat", at(9, 5)) + "\n")
        fh.write('{"command_id": 2, "stage": "ack", "co')  # 當機時寫到一半的最後一行
    assert earlier_stop(tmp_path, DAY) == at(9, 5)
    (tmp_path / release_name(DAY)).write_text(
        json.dumps({"at": at(9, 7).isoformat()}), encoding="utf-8"
    )
    assert earlier_stop(tmp_path, DAY) == at(9, 10)  # 解除之前的不算，之後的算
    path.write_text("不是 JSON\n" + row("ack", "flat", at(9, 5)) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="第 1 行讀不懂"):
        earlier_stop(tmp_path, DAY)


# ---- AC6 收盤與結束


def test_ac6_quit_request_and_ctrl_c_end_the_program(tmp_path: Path) -> None:
    def quit_now(now: datetime) -> None:
        if now >= at(8, 46, 0):
            (tmp_path / QUIT_NAME).write_text("x", encoding="utf-8")

    rig = Rig(tmp_path, FakeClock(at(8, 44, 50)), hook=quit_now)
    report = rig.runner().run()
    assert (
        report.reason == "quit" and report.ok and report.position == 1
    )  # 模擬：不平倉，重開會重算
    assert any("程式結束（收到「結束」）" in line and "部位 1 口" in line for line in rig.said)

    def interrupt(now: datetime) -> None:
        if now >= at(8, 46, 0):
            raise KeyboardInterrupt

    other = tmp_path / "b"
    other.mkdir()
    rig = Rig(other, FakeClock(at(8, 44, 50)), hook=interrupt)
    assert rig.runner().run().reason == "interrupt"


def test_ac6_a_day_without_ticks_says_so(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeClock(at(8, 49, 0)), ticks=[])
    status = tmp_path / "status.json"
    report = rig.runner(status=status).run()
    assert report.reason == "no_ticks" and report.ok and report.day_pnl_twd is None
    assert not status.exists()
    text = "\n".join(rig.said)
    assert "開盤了但還沒收到今天的行情：富邦錄製程式有在跑嗎？今天有開盤嗎？" in text
    assert "程式結束（今天沒有收到任何行情）" in text


def test_ac6_a_position_left_at_the_close_is_an_error(tmp_path: Path) -> None:
    cfg = small_cfg()
    cfg = replace(cfg, risk=replace(cfg.risk, force_flat_time=time(13, 30)))  # 收盤前不會強制平倉
    rig = Rig(tmp_path, FakeClock(at(8, 44, 50)))
    report = rig.runner(cfg, strategy=InOut(sell_at=time(23, 0))).run()
    assert report.reason == "not_flat" and not report.ok and report.position == 1
    assert any("收盤檢查沒過" in line for line in rig.said)


def test_ac6_a_broken_tick_file_ends_the_program_with_the_reason(tmp_path: Path) -> None:
    def corrupt(now: datetime) -> None:
        if now == at(8, 46, 0):
            with (tmp_path / "ticks.csv").open("a", encoding="utf-8") as fh:
                fh.write("2026-11-16T08:00:00+08:00,1,1,,\n")  # 時間倒退

    rig = Rig(tmp_path, FakeClock(at(8, 44, 50)), hook=corrupt)
    report = rig.runner().run()
    assert report.reason == "feed_error" and not report.ok
    assert any("行情檔有問題，程式結束" in line and "比前一列早" in line for line in rig.said)


def test_ac6_says_when_ticks_stop_and_when_they_come_back(tmp_path: Path) -> None:
    ticks = [t for t in day_ticks() if not at(8, 46) <= t.ts < at(8, 47)]  # 中間一分鐘沒有行情
    rig = Rig(tmp_path, FakeClock(at(8, 44, 50)), ticks=ticks)
    rig.runner(strategy=InOut(buy_at=time(23, 0)), stale_note_s=20.0).run()
    stale = [line for line in rig.said if "沒有新的" in line]
    assert len(stale) == 1 and "最後一筆 08:45:59" in stale[0]
    assert sum("行情恢復了" in line for line in rig.said) == 1


# ---- AC7 回放


def test_ac7_paced_clock_runs_faster_than_real_time() -> None:
    real = FakeClock(datetime(2026, 11, 14, 1, 0, tzinfo=TAIPEI))
    clock = paced_clock(at(8, 44, 50), 60.0, real)
    assert clock() == at(8, 44, 50)
    real.sleep(2.0)
    assert clock() == at(8, 46, 50)
    with pytest.raises(ValueError, match="speed"):
        paced_clock(at(8, 44, 50), 0.0, real)
    with pytest.raises(ValueError, match="時區"):
        paced_clock(datetime(2026, 11, 16, 8, 45), 1.0, real)


def test_ac7_playback_matches_the_replay_and_touches_no_control_files(tmp_path: Path) -> None:
    real = FakeClock(datetime(2026, 11, 14, 1, 0, tzinfo=TAIPEI))  # 半夜回放
    said: list[str] = []
    source = PacedTicks(day_ticks())
    status = tmp_path / "status.json"
    runner = Runner(
        small_cfg(),
        InOut(),
        source,
        tmp_path,
        "inout",
        clock=paced_clock(at(8, 44, 50), 30.0, real),
        sleep=real.sleep,
        say=said.append,
        status=status,
        status_source="replay",
        control=False,
        close_grace_s=5.0,
    )
    report = runner.run()
    assert report.reason == "closed" and source.left == 0
    assert journal_fills(tmp_path) == replay_fills(small_cfg(), day_ticks())
    snap = json.loads(status.read_text(encoding="utf-8"))
    assert snap["source"] == "replay" and snap["control"]["listening"] is False
    names = {p.name for p in tmp_path.iterdir()}
    assert names == {"status.json", next(n for n in names if n.startswith("journal_"))}
    assert "回放：不聽遠端指令、不上網站。" in said[0]


# ---- AC10 指令


def write_day(path: Path) -> Path:
    path.write_text(",".join(HEADER) + "\n" + "".join(map(line_of, day_ticks())), encoding="utf-8")
    return path


def test_ac10_run_command_plays_back_a_recorded_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real = FakeClock(datetime(2026, 11, 14, 1, 0, tzinfo=TAIPEI))
    monkeypatch.setattr(cli, "wall_clock", real)
    monkeypatch.setattr(
        cli,
        "paced_clock",
        lambda first, speed: paced_clock(first, speed, real),
    )
    monkeypatch.setattr(
        cli, "Runner", lambda *a, **kw: Runner(*a, **{**kw, "sleep": real.sleep, "tick_s": 5.0})
    )
    day_file = write_day(tmp_path / "TMF_2026-11-16.csv")
    args = ["run", "--config", str(CONFIG_PATH), "--dir", str(tmp_path), "--strategy", "none"]
    play = ["--play", str(day_file), "--day", "2026-11-16", "--speed", "60"]
    assert main([*args, *play, "--status", str(tmp_path / "status.json")]) == 0
    out = capsys.readouterr().out
    assert "模擬交易程式開始：TMF、策略 none、2026-11-16" in out and "行情 600 筆" in out
    snap = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert (snap["status"], snap["source"]) == ("closed", "replay")


def test_ac10_run_command_checks_its_arguments(tmp_path: Path) -> None:
    day_file = write_day(tmp_path / "TMF_2026-11-16.csv")
    with pytest.raises(ValueError, match="要給其中一個"):
        run_trade(CONFIG_PATH, tmp_path, "none")
    with pytest.raises(ValueError, match="要給其中一個"):
        run_trade(CONFIG_PATH, tmp_path, "none", ticks_file=tmp_path / "t.csv", play=day_file)
    with pytest.raises(ValueError, match="--day"):
        run_trade(CONFIG_PATH, tmp_path, "none", play=day_file)
    with pytest.raises(ValueError, match="回放不發手機通知"):
        run_trade(CONFIG_PATH, tmp_path, "none", play=day_file, day=DAY, notify="send")
    with pytest.raises(ValueError, match="--push-subs"):
        run_trade(CONFIG_PATH, tmp_path, "none", play=day_file, day=DAY, push_subs=tmp_path / "p")
    with pytest.raises(ValueError, match="--ms-repo"):
        run_trade(CONFIG_PATH, tmp_path, "momentum", play=day_file, day=DAY)
