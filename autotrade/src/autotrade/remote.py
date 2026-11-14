"""後台執行從網站來的「啟動／結束交易程式」（A-017／D-229）。

平倉、暫停／恢復由交易程式自己執行（engine）；啟動與結束只有後台做得到，因為它才開得了程式。
後台自己不連資料庫：另外開一支中繼程式（relay --consumer admin）把指令寫成本機的檔案，這裡只讀那個檔、寫回報檔。

遠端啟動只能啟動「啟動後台時在指令列指定的那一份設定」，用今天的即時行情；
不能選別的設定、不能改設定、不能解除今天的緊急停止。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from autotrade.control import ControlError, ControlSource
from autotrade.model import (
    ACTION_QUIT,
    ACTION_START,
    ADMIN_ACTIONS,
    RESULT_ACCEPTED,
    RESULT_IGNORED,
    RESULT_QUIT,
    RESULT_REFUSED,
    RESULT_STARTED,
    STAGE_ACK,
    STAGE_DONE,
    Command,
    CommandResult,
    require_taipei,
)

REMOTE_DIR = "remote"  # <後台的 data-dir>/remote：後台與它的中繼程式交換檔案的地方
MODE_TEXT = {"sim": "模擬", "live": "真單"}
ACTION_TEXT = {ACTION_START: "啟動", ACTION_QUIT: "結束"}


@dataclass
class Remote:
    """看遙控指令檔、執行、回報。start／stop 是後台自己的兩個動作，refuse 是它們會丟的「不能做」。"""

    source: ControlSource
    config: Path  # 遠端只能啟動這一份
    mode: str  # 那一份設定的模式（sim／live）
    dsn_env: str  # 啟動時指令中繼連哪個資料庫
    notify: bool  # 那一份設定有沒有通知
    start: Callable[[dict[str, Any]], Any]
    stop: Callable[[], dict[str, Any]]
    say: Callable[[str, str], None]
    refuse: tuple[type[Exception], ...]
    started_at: datetime
    grace_s: float = 15.0  # 剛開的時候中繼程式還沒寫出第一份指令檔：這段時間讀不到不算問題
    _seen: dict[int, datetime] = field(default_factory=dict)
    _ok: bool | None = None

    def step(self, now: datetime) -> None:
        """讀一次遙控指令檔，有新的就執行並回報。要定時叫（後台的背景執行緒）。讀不到只講一次。"""
        now = require_taipei(now, "Remote.step 的 now")
        try:
            commands = self.source.poll(now)
        except ControlError as exc:
            waiting = (now - self.started_at).total_seconds() < self.grace_s
            if self._ok is not False and not waiting:
                self.say(f"遠端：讀不到遙控指令（{exc}）。從網站啟動／結束暫時到不了", "warn")
                self._ok = False
            return
        if self._ok is False:
            self.say("遠端：遙控指令恢復可以讀", "info")
        self._ok = True
        for cmd in sorted(commands, key=lambda c: c.id):
            if self._seen.get(cmd.id) == cmd.created_at:
                continue  # 做過了，回報還在路上
            self._seen[cmd.id] = cmd.created_at
            self._handle(cmd, now)

    def _report(self, cmd: Command, now: datetime, stage: str, code: str, note: str) -> None:
        result = CommandResult(cmd.id, stage, code, note, cmd.created_at, cmd.action)
        try:
            self.source.report(result, now)
        except ControlError as exc:
            self.say(f"遠端：結果記不下來（{exc}）：{note}", "warn")

    def _handle(self, cmd: Command, now: datetime) -> None:
        if cmd.day != now.date():
            why = f"是 {cmd.day} 的指令，不是今天的，沒有執行"
            return self._report(cmd, now, STAGE_ACK, RESULT_IGNORED, why)
        if cmd.action not in ADMIN_ACTIONS:
            why = f"不認得的動作 {cmd.action!r}，沒有執行"
            return self._report(cmd, now, STAGE_ACK, RESULT_IGNORED, why)
        who = MODE_TEXT.get(str(cmd.target), str(cmd.target))
        what = f"{ACTION_TEXT[cmd.action]}{who}的交易程式"
        self.say(f"遠端：網站要求{what}（{cmd.created_at:%H:%M:%S} 按下）", "info")
        # 先回報收到了再動手：結束可能要等交易程式收工好幾秒
        self._report(cmd, now, STAGE_ACK, RESULT_ACCEPTED, f"後台收到了：{what}")
        code, note = self._start(cmd) if cmd.action == ACTION_START else self._quit(cmd)
        self._report(cmd, now, STAGE_DONE, code, note)
        self.say(f"遠端：{note}", "warn" if code == RESULT_REFUSED else "info")

    def _wrong_mode(self, cmd: Command) -> str | None:
        if cmd.target == self.mode:
            return None
        mine = MODE_TEXT.get(self.mode, self.mode)
        asked = MODE_TEXT.get(str(cmd.target), str(cmd.target))
        return f"沒有執行：這台後台可以遙控的設定（{self.config.name}）是{mine}，不是{asked}"

    def _start(self, cmd: Command) -> tuple[str, str]:
        wrong = self._wrong_mode(cmd)
        if wrong is not None:
            return RESULT_REFUSED, wrong
        body = {
            "config": self.config.name,
            "source": "live",
            "dsn_env": self.dsn_env,
            "notify": self.notify,
        }  # 沒有 release_stop：今天按過緊急停止的話，啟動之後一樣是停手的
        try:
            self.start(body)
        except self.refuse as exc:
            return RESULT_REFUSED, f"沒有執行：{exc}"
        return RESULT_STARTED, f"已啟動交易程式（{self.config.name}、今天的即時行情）"

    def _quit(self, cmd: Command) -> tuple[str, str]:
        wrong = self._wrong_mode(cmd)
        if wrong is not None:
            return RESULT_REFUSED, wrong
        was = bool(self.stop().get("was_running"))
        return RESULT_QUIT, "已結束交易程式" if was else "交易程式本來就沒有在跑"
