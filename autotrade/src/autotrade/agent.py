"""常駐的交易 agent（A-019／D-231）：一支指令把行情中繼、指令中繼、交易程式、看門程式一起開起來，並且一直顧著。

- 哪一支自己結束了就講原因並重開（間隔逐次加倍，活得夠久就從頭算）。
- 交易程式每天開盤前才開；收盤後它自己結算、正常結束，今天就不再開，隔天開盤前再開。
- 換日（半夜）時全部收掉、換成新的一天的資料夾重開。
- Ctrl+C 全部一起收掉。

這一支自己不交易、不連資料庫、不連券商：它只開程序、看程序還在不在。
「啟動／停止」不是開關程序，是交易程式裡待命／運作的切換（engine），所以手機隨時叫得動。
"""

from __future__ import annotations

import time as time_module
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import IO

from autotrade.config import AgentConfig
from autotrade.jobs import STATUS_NAME, TICKS_NAME, Proc, Spawner, _end, popen
from autotrade.model import TAIPEI, require_taipei

WATCHDOG_DIR = "watchdog"  # 看門程式自己的指令檔與回報檔放在當天資料夾底下的這裡


@dataclass(frozen=True)
class Child:
    """要顧著的一支程序。"""

    name: str
    args: tuple[str, ...]
    not_before: time | None = None  # 今天這個時間之前不開
    done_ok: bool = False  # 結束碼 0＝今天的事做完了，不重開
    not_after: time | None = (
        None  # 過了這個時間才出錯結束的，今天不再重開（收盤後重算一整天沒有意義）
    )


@dataclass(frozen=True)
class DayPlan:
    """某一天要開哪些程序、紀錄檔放哪。"""

    folder: Path
    children: tuple[Child, ...]


@dataclass(frozen=True)
class AgentSetup:
    """組出每天那幾行指令要用的東西（都從指令列來）。"""

    python: str
    config: Path
    base: Path  # 每天一個子資料夾
    strategy: str
    mode: str  # 設定檔的 mode：指令中繼用它當名字
    dsn_env: str  # 指令中繼連哪個資料庫
    ticks_dsn_env: str  # 行情中繼讀哪個資料庫
    ms_repo: Path | None = None
    env_file: Path | None = None
    bell_port: int | None = None
    notify: str | None = None  # send／print／None


def day_plan(setup: AgentSetup, cfg: AgentConfig, session_end: time, day: date) -> DayPlan:
    """那一天要開的五支程序。資料夾沒有就建。"""
    folder = setup.base / day.isoformat()
    (folder / WATCHDOG_DIR).mkdir(parents=True, exist_ok=True)
    head = (setup.python, "-m", "autotrade")
    config = ("--config", str(setup.config))
    env = () if setup.env_file is None else ("--env-file", str(setup.env_file))
    bell = (
        ("--bell",) if setup.bell_port is None else ("--bell", "--bell-port", str(setup.bell_port))
    )
    ticks_file, status = folder / TICKS_NAME, folder / STATUS_NAME
    relay = (*head, "relay", *config, "--dsn-env", setup.dsn_env, *env, *bell)
    run: tuple[str, ...] = (*head, "run", *config, "--dir", str(folder), "--agent")
    run += ("--strategy", setup.strategy, "--ticks-file", str(ticks_file), "--status", str(status))
    watch: tuple[str, ...] = (*head, "watchdog", *config, "--dir", str(folder))
    watch += ("--ticks-file", str(ticks_file), "--status", str(status))
    if setup.ms_repo is not None:
        run += ("--ms-repo", str(setup.ms_repo))
    if setup.notify is not None:
        log = ("--notify-log", str(folder / "notify_failed.log"))
        run += ("--notify", setup.notify, *env, *log)
        watch += ("--notify", setup.notify, *env, *log)
    ticks: tuple[str, ...] = (*head, "ticks", *config, "--out", str(ticks_file))
    ticks = (*ticks, "--day", day.isoformat(), "--dsn-env", setup.ticks_dsn_env, *env)
    main_relay = (*relay, "--dir", str(folder), "--status", str(status))
    main_relay = (*main_relay, "--bus", "--consumer", setup.mode)
    watch_relay = (*relay, "--dir", str(folder / WATCHDOG_DIR), "--consumer", "watchdog")
    children = (
        Child("ticks", ticks),
        Child("relay", main_relay),
        Child("relay_watchdog", watch_relay),
        Child("watchdog", watch),
        Child("run", run, not_before=cfg.trader_start, done_ok=True, not_after=session_end),
    )
    return DayPlan(folder, children)


@dataclass
class _Slot:
    child: Child
    proc: Proc | None = None
    log: IO[str] | None = None
    started_at: datetime | None = None
    next_at: datetime | None = None
    wait_s: float = 0.0
    done: bool = False
    starts: int = 0


@dataclass
class Supervisor:
    """顧著一天的那幾支程序。step 用時鐘定時叫；spawn 可以換掉（測試用假的，不真的開程序）。"""

    cfg: AgentConfig
    plan: Callable[[date], DayPlan]
    workdir: Path
    spawn: Spawner = popen
    say: Callable[[str], None] = print
    stop_wait_s: float = 10.0
    _day: date | None = None
    _folder: Path = field(default_factory=Path)
    _slots: list[_Slot] = field(default_factory=list)

    def starts(self, name: str) -> int:
        """今天這支程序開過幾次（給測試與狀態看）。"""
        return next((s.starts for s in self._slots if s.child.name == name), 0)

    def _roll(self, now: datetime) -> None:
        if self._slots:
            self.say(f"{now:%H:%M:%S} 換日：收掉昨天的程序，開 {now.date()} 的")
        self.close()
        plan = self.plan(now.date())
        self._day = now.date()
        self._folder = plan.folder
        self._slots = [_Slot(child, wait_s=self.cfg.restart_wait_s) for child in plan.children]

    def _start(self, slot: _Slot, now: datetime) -> None:
        name = slot.child.name
        # 接在今天原本的紀錄後面：重開之前的紀錄不會不見
        slot.log = (self._folder / f"{name}.log").open("a", encoding="utf-8")
        slot.proc = self.spawn(slot.child.args, slot.log, self.workdir)
        slot.started_at, slot.next_at = now, None
        slot.starts += 1
        again = "" if slot.starts == 1 else f"（今天第 {slot.starts} 次）"
        self.say(f"{now:%H:%M:%S} 開 {name}{again}")

    def _ended(self, slot: _Slot, code: int, now: datetime) -> None:
        child = slot.child
        assert slot.started_at is not None
        lived = (now - slot.started_at).total_seconds()
        slot.proc = None
        if slot.log is not None:
            slot.log.close()
            slot.log = None
        if child.done_ok and code == 0:
            slot.done = True
            self.say(f"{now:%H:%M:%S} {child.name} 今天的事做完了（正常結束），明天再開")
            return
        if child.not_after is not None and now.time() >= child.not_after:
            slot.done = True
            self.say(
                f"{now:%H:%M:%S} {child.name} 結束（結束碼 {code}）：已經過了 {child.not_after:%H:%M}，"
                f"今天不再重開。請看 {child.name}.log"
            )
            return
        if lived >= self.cfg.steady_s:
            slot.wait_s = self.cfg.restart_wait_s  # 活得夠久：不是一開就死，間隔從頭算
        slot.next_at = now + timedelta(seconds=slot.wait_s)
        self.say(
            f"{now:%H:%M:%S} {child.name} 自己結束了（結束碼 {code}，活了 {lived:.0f} 秒）："
            f"{slot.wait_s:g} 秒後重開。原因看 {child.name}.log"
        )
        slot.wait_s = min(slot.wait_s * 2, self.cfg.restart_wait_max_s)

    def step(self, now: datetime) -> None:
        """看一輪：換日、該開的開、結束了的處理。"""
        now = require_taipei(now, "Supervisor.step 的 now")
        if now.date() != self._day:
            self._roll(now)
        for slot in self._slots:
            if slot.proc is not None:
                code = slot.proc.poll()
                if code is None:
                    continue
                self._ended(slot, code, now)
            if slot.done:
                continue
            if slot.child.not_before is not None and now.time() < slot.child.not_before:
                continue
            if slot.next_at is not None and now < slot.next_at:
                continue
            self._start(slot, now)

    def close(self) -> None:
        """全部收掉：先好好講，等不到才強制。"""
        _end([s.proc for s in self._slots if s.proc is not None], self.stop_wait_s)
        for slot in self._slots:
            slot.proc = None
            if slot.log is not None:
                slot.log.close()
                slot.log = None


def run(
    supervisor: Supervisor,
    *,
    tick_s: float = 1.0,
    clock: Callable[[], datetime] = lambda: datetime.now(TAIPEI),
    sleep: Callable[[float], None] = time_module.sleep,
) -> None:
    """一直顧到被中斷（Ctrl+C），然後全部收掉。"""
    try:
        while True:
            supervisor.step(clock())
            sleep(tick_s)
    except KeyboardInterrupt:
        supervisor.say("收到中斷：把所有程序收掉")
    finally:
        supervisor.close()


def describe(children: Sequence[Child]) -> str:
    """列出要開的程序（啟動時印一次）。"""
    return "、".join(c.name for c in children)
