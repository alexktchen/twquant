"""模擬交易程式（A-014／D-227）：盤中常駐。跟著行情跑策略、聽遠端緊急停止、把狀態寫給網站、發通知。

現在只跑模擬（模擬券商）。真單要等元大的行情與下單轉接（A-008、A-009），這裡會直接拒絕。

幾條規矩：
- 兩個時鐘。行情用行情自己的時間（跟重播一樣，所以同一份行情重算結果相同）；
  定時的事（停手後平倉、讀遠端指令、合併通知）用電腦的時鐘。
- 結果只由「今天的行情」加「今天按過的遠端停止」決定：晚啟動或中途重開，都從今天第一筆行情重算一次，
  不會因為程式停過就少幾筆。重算舊行情的那一段（補行情）不跑定時的事、不發通知。
- 今天按過遠端緊急停止的話，重開之後算到那個時間一樣停手、平倉，之後不再交易；要再交易得在電腦前明講解除。
- 只碰本機檔案：行情由行情中繼接在檔案後面，遠端指令由指令中繼寫成檔案（A-011）。
"""

from __future__ import annotations

import json
import os
import time as time_module
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from autotrade.broker.sim import SimBroker
from autotrade.config import Config, ConfigError
from autotrade.control import INBOX_NAME, ControlLoop, FileControl, outbox_name
from autotrade.engine import EndOfDayError, Engine, Event
from autotrade.export import dumps, snapshot
from autotrade.feed.follow import TickSource
from autotrade.feed.replay import FeedError
from autotrade.journal import Journal
from autotrade.model import RESULT_IGNORED, STAGE_ACK, TAIPEI, require_taipei
from autotrade.notify import Notifier
from autotrade.strategy.base import Strategy

QUIT_NAME = "quit.request"  # 後台按「結束」時放在資料夾裡的檔案；程式看到就收工
# 印在畫面上的事件（委託、成交、停手、停損、遠端指令、連線、被擋下、部位修正）
SHOWN = frozenset(
    {
        "order",
        "fill",
        "halt",
        "release",
        "stop",
        "control",
        "control_link",
        "cancel",
        "cancelled",
        "reject",
        "correction",
        "link",
    }
)
REASON_TEXT = {
    "closed": "收盤，今天結束",
    "quit": "收到「結束」",
    "interrupt": "被中斷（Ctrl+C）",
    "no_ticks": "今天沒有收到任何行情",
    "not_flat": "收盤檢查沒過",
    "feed_error": "行情檔有問題",
}
OK_REASONS = frozenset({"closed", "quit", "interrupt", "no_ticks"})


def wall_clock() -> datetime:
    """電腦現在的時間（Asia/Taipei）。"""
    return datetime.now(TAIPEI)


def paced_clock(
    first: datetime, speed: float, real: Callable[[], datetime] = wall_clock
) -> Callable[[], datetime]:
    """回放用的假時鐘：從 first 開始，走得比真的快 speed 倍。"""
    if speed <= 0:
        raise ValueError(f"speed 要大於 0：{speed}")
    first = require_taipei(first, "回放開始的時間")
    began = real()
    return lambda: first + (real() - began) * speed


def release_name(day: date) -> str:
    """「解除今天的停止」記在哪個檔案。"""
    return f"release_{day.isoformat()}.json"


def earlier_stop(folder: Path, day: date) -> datetime | None:
    """今天稍早有沒有執行過遠端緊急停止（看回報檔），而且之後沒有被解除；有就回傳最早那一次的時間。

    回報檔是交易程式自己一行一行接上去的（A-011）。最後一行沒寫完（當機）不算；其他讀不懂的行就 raise。
    """
    released: datetime | None = None
    release = folder / release_name(day)
    if release.exists():
        released = require_taipei(
            datetime.fromisoformat(str(json.loads(release.read_text(encoding="utf-8"))["at"])),
            f"{release} 的時間",
        )
    path = folder / outbox_name(day)
    if not path.exists():
        return None
    stops: list[datetime] = []
    lines = path.read_text(encoding="utf-8").split("\n")[:-1]  # 最後一段沒有換行結尾＝沒寫完
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            stage, code = str(row["stage"]), str(row["code"])
            at = require_taipei(datetime.fromisoformat(str(row["at"])), "at")
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"{path} 第 {number} 行讀不懂：{exc}") from exc
        if stage == STAGE_ACK and code != RESULT_IGNORED and (released is None or at > released):
            stops.append(at)
    return min(stops, default=None)


@dataclass(frozen=True)
class RunReport:
    """程式結束時的結果。"""

    reason: str  # REASON_TEXT 的其中一個
    ticks: int
    fills: int
    position: int
    halt: str | None
    day_pnl_twd: float | None  # 沒收到行情是 None

    @property
    def ok(self) -> bool:
        """是不是正常結束（收盤、被要求結束、今天沒行情）。"""
        return self.reason in OK_REASONS


@dataclass
class Runner:
    """模擬交易程式本身。clock 與 sleep 可以換掉（回放用加速的時鐘；測試用假的，不真的等）。"""

    cfg: Config
    strategy: Strategy
    source: TickSource
    folder: Path
    strategy_name: str
    clock: Callable[[], datetime] = wall_clock
    sleep: Callable[[float], None] = time_module.sleep
    say: Callable[[str], None] = print
    status: Path | None = None
    status_source: str = "live"  # 給網站的「資料來源」：live＝即時行情、replay＝回放
    control: bool = True  # 回放不聽遠端指令（那條路用的是真的時鐘）
    notifier: Notifier | None = None
    release_stop: bool = False  # 解除今天稍早的遠端停止
    status_every_s: float = 5.0
    tick_s: float = 0.2  # 一圈多久
    close_grace_s: float = 60.0  # 收盤後再等多久才結算（等最後幾筆行情進來）
    lag_ok_s: float = 10.0  # 最新行情離現在這麼近，就算補完了
    settle_s: float = 3.0  # 或者：這麼久都沒有新的舊行情進來，也算補完了
    wait_note_s: float = 60.0  # 還沒有行情時，多久提醒一次
    stale_note_s: float = 30.0  # 盤中行情這麼久沒有新的就提醒
    control_grace_s: float = 15.0  # 剛啟動時等指令中繼寫出第一份指令檔，最多等這麼久
    _live: bool = field(default=False, init=False)
    _status_blocked: bool = field(default=False, init=False)

    def _forward(self, event: Event) -> None:
        # 補行情那一段算出來的事不發通知：不是現在發生的
        if self._live and self.notifier is not None:
            self.notifier.on_event(event)

    def _check(self) -> None:
        if self.cfg.mode != "sim":
            raise ConfigError(
                "這支程式現在只跑模擬：真單要等元大的行情與下單接上（A-008、A-009），"
                f"設定檔的 mode 是 {self.cfg.mode!r}"
            )
        if not self.folder.is_dir():
            raise FileNotFoundError(f"資料夾不存在：{self.folder}")
        if self.control and self.cfg.control is None:
            raise ConfigError("設定檔沒有 [control]（遠端緊急停止），不能跑交易程式")
        if self.status is not None and self.cfg.export is None:
            raise ConfigError("設定檔沒有 [export]，不能輸出狀態")

    def _journal(self, start: datetime) -> Journal:
        # 每次啟動一本新的日誌：重開會從頭重算，接在舊的後面會變成重複的事
        stem = f"journal_{start:%Y-%m-%d_%H%M%S}"
        path, again = self.folder / f"{stem}.jsonl", 1
        while path.exists():
            again += 1
            path = self.folder / f"{stem}_{again}.jsonl"
        return Journal(path)

    def _stop_to_apply(self, start: datetime) -> datetime | None:
        if not self.control:
            return None
        at = earlier_stop(self.folder, start.date())
        if at is None:
            return None
        if self.release_stop:
            release = self.folder / release_name(start.date())
            tmp = release.with_name(release.name + ".tmp")
            tmp.write_text(json.dumps({"at": start.isoformat()}), encoding="utf-8")
            os.replace(tmp, release)
            self.say(f"今天 {at:%H:%M:%S} 按過遠端緊急停止：已在這台電腦解除，這次照常交易。")
            return None
        self.say(
            f"今天 {at:%H:%M:%S} 按過遠端緊急停止：這次算到那個時間一樣停手、平倉，之後不再交易。"
            "要再交易，請在後台勾「解除今天的停止」再啟動。"
        )
        return at

    def run(self) -> RunReport:
        """跑到收盤、被要求結束、或行情檔出問題為止。"""
        self._check()
        start = self.clock()
        day = start.date()
        close_at = datetime.combine(day, self.cfg.session.end, tzinfo=TAIPEI) + timedelta(
            seconds=self.close_grace_s
        )
        open_at = datetime.combine(day, self.cfg.session.start, tzinfo=TAIPEI)
        stop_at = self._stop_to_apply(start)
        quit_file = self.folder / QUIT_NAME
        quit_file.unlink(missing_ok=True)  # 上一次留下來的不算
        log = self._journal(start)

        def sink(event: Event) -> None:
            log.write(event)  # 日誌先寫，通知在後
            self._forward(event)

        engine = Engine(self.cfg, self.strategy, SimBroker(self.cfg.sim), sink=sink)
        loop: ControlLoop | None = None
        if self.control:
            control = self.cfg.control
            assert control is not None  # _check 保證有
            inbox = FileControl(self.folder, control.stale_s)
            loop = ControlLoop(engine, inbox, control.poll_s, urgent=inbox.changed)
        self.say(
            f"模擬交易程式開始：{self.cfg.contract.symbol}、策略 {self.strategy_name}、{day}"
            f"（{self.cfg.session.start:%H:%M}～{self.cfg.session.end:%H:%M}）。"
            + ("會聽遠端緊急停止。" if loop is not None else "回放：不聽遠端指令、不上網站。")
        )
        reason, count, shown = "", 0, 0
        listening = False  # 指令中繼跟交易程式是一起啟動的：等它寫出第一份新的指令檔再開始讀
        caught_up, quiet_since = False, None
        next_status = start
        next_wait_note, stale = start, False
        try:
            while True:
                now = self.clock()
                ticks = self.source.poll(now)
                for tick in ticks:
                    if stop_at is not None and tick.ts >= stop_at:
                        self._apply_stop(engine, stop_at)
                        stop_at = None
                    engine.on_tick(tick)
                count += len(ticks)
                if not caught_up:
                    if ticks:
                        quiet_since = None
                    else:
                        quiet_since = quiet_since or now
                        last = engine.last_tick
                        if (
                            last is None
                            or (now - last.ts).total_seconds() <= self.lag_ok_s
                            or (now - quiet_since).total_seconds() >= self.settle_s
                        ):
                            caught_up = self._live = True
                            if count:
                                fills = len(engine.ledger.fills)
                                self.say(
                                    f"補上今天到現在的行情 {count} 筆（模擬成交 {fills} 筆，"
                                    f"部位 {engine.ledger.position} 口）；接下來跟著即時行情走。"
                                )
                if caught_up:
                    if stop_at is not None:
                        self._apply_stop(engine, stop_at)
                        stop_at = None
                    engine.on_timer(now)
                    if loop is not None:
                        listening = listening or self._relay_ready(now, start)
                        if listening:
                            loop.step(now)
                    if self.notifier is not None:
                        self.notifier.poll(now)
                shown = self._show(engine.events, shown)
                if self.status is not None and engine.last_tick is not None and now >= next_status:
                    self._write_status(engine, loop, now, "live")
                    next_status = now + timedelta(seconds=self.status_every_s)
                if quit_file.exists():
                    reason = "quit"
                    break
                if caught_up and now >= close_at:
                    reason = "closed"
                    break
                if caught_up and now < close_at:
                    last = engine.last_tick
                    if last is None and now >= next_wait_note:
                        self.say(
                            f"{now:%H:%M:%S} 還沒開盤（{open_at:%H:%M} 開盤），等行情中。"
                            if now < open_at
                            else f"{now:%H:%M:%S} 開盤了但還沒收到今天的行情："
                            "富邦錄製程式有在跑嗎？今天有開盤嗎？"
                        )
                        next_wait_note = now + timedelta(seconds=self.wait_note_s)
                    elif last is not None:
                        lag = (now - last.ts).total_seconds()
                        in_session = now.time() < self.cfg.session.end
                        if not stale and in_session and lag > self.stale_note_s:
                            stale = True
                            self.say(
                                f"{now:%H:%M:%S} 行情已經 {lag:.0f} 秒沒有新的"
                                f"（最後一筆 {last.ts:%H:%M:%S}）：富邦錄製程式還在跑嗎？"
                            )
                        elif stale and lag <= self.stale_note_s:
                            stale = False
                            self.say(f"{now:%H:%M:%S} 行情恢復了。")
                self.sleep(self.tick_s)
        except KeyboardInterrupt:
            reason = "interrupt"
        except FeedError as exc:
            reason = "feed_error"
            self.say(f"行情檔有問題，程式結束：{exc}")
        finally:
            log.close()
        if reason == "closed":
            reason = self._close(engine, loop)
        if self.notifier is not None:
            self.notifier.close()
        last = engine.last_tick
        report = RunReport(
            reason,
            count,
            len(engine.ledger.fills),
            engine.ledger.position,
            engine.risk.halt,
            None if last is None else engine.day_pnl(last.price),
        )
        pnl = "" if report.day_pnl_twd is None else f"、今天損益 {report.day_pnl_twd:+,.0f} 元"
        self.say(
            f"程式結束（{REASON_TEXT[reason]}）：行情 {report.ticks} 筆、成交 {report.fills} 筆、"
            f"部位 {report.position} 口{pnl}"
            + ("" if report.halt is None else f"、停手中（{report.halt}）")
        )
        return report

    def _relay_ready(self, now: datetime, start: datetime) -> bool:
        """指令檔是不是新的（指令中繼已經在寫了）。等超過 control_grace_s 就不等：照常去讀，讀不到會記下來。

        不等的話，每次啟動都會先記一筆「讀不到遠端指令」再馬上恢復，手機也會跟著響一次假警報。
        """
        if (now - start).total_seconds() >= self.control_grace_s:
            return True
        control = self.cfg.control
        assert control is not None
        try:
            data = json.loads((self.folder / INBOX_NAME).read_text(encoding="utf-8"))
            written = require_taipei(datetime.fromisoformat(str(data["written_at"])), "written_at")
        except (OSError, ValueError, KeyError, TypeError):
            return False  # 還沒有、正在換檔、或內容不對：再等一下；過了等待時間由 ControlLoop 去報
        return (now - written).total_seconds() <= control.stale_s

    def _apply_stop(self, engine: Engine, at: datetime) -> None:
        engine.kill(at)
        self.say(f"{at:%H:%M:%S}  套用今天稍早的遠端緊急停止：停手、平倉")

    def _close(self, engine: Engine, loop: ControlLoop | None) -> str:
        last = engine.last_tick
        if last is None:
            return "no_ticks"
        try:
            engine.finish()
        except EndOfDayError as exc:
            self.say(f"收盤檢查沒過：{exc}")
            return "not_flat"
        if self.status is not None:
            self._write_status(engine, loop, last.ts, "closed")
        return "closed"

    def _show(self, events: list[Event], shown: int) -> int:
        for event in events[shown:]:
            if event.kind in SHOWN:
                self.say(f"{event.ts:%H:%M:%S}  {event.text}")
        return len(events)

    def _write_status(
        self, engine: Engine, loop: ControlLoop | None, now: datetime, status: str
    ) -> None:
        assert self.status is not None
        snap = snapshot(
            engine,
            now,
            source=self.status_source,
            status=status,
            strategy=self.strategy_name,
            control=None if loop is None else loop.state,
        )
        tmp = self.status.with_name(self.status.name + ".tmp")
        tmp.write_text(dumps(snap), encoding="utf-8", newline="\n")
        try:
            os.replace(tmp, self.status)
        except PermissionError:
            # Windows 上指令中繼正好開著狀態檔在讀的那一瞬間換不了檔：這一次不換，下一次寫的時候再換。
            # 不能為了一份給網站看的狀態讓交易程式停下來。
            if not self._status_blocked:
                self._status_blocked = True
                self.say(f"{now:%H:%M:%S} 狀態檔正好被別的程式開著，這一次沒換成；下一次再寫")
        else:
            self._status_blocked = False
