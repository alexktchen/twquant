"""獨立看門程式（A-020／D-231 補充）：自己一支程序，盯著交易程式。

盯四件事，任何一件出事就「請券商那邊平倉、要交易程式停手、發『要處理』的通知」：
（一）交易程式沒心跳（它每次讀指令檔都會碰一下心跳檔）；
（二）盤中行情停住（行情檔太久沒有新的）；
（三）交易程式帳上的部位跟券商的不一樣；
（四）遠端緊急停止按下去了，主程式卻沒回報（D-225 預留給看門程式的那一條）。

停手是寫一個本機檔案（control.write_local_stop）：交易程式活著就馬上停手、平倉；死了重開，也會算到那個時間停手。
解除一樣只能在電腦前。看門程式自己不連資料庫；它的指令檔與回報檔由另一支中繼程式（名字 watchdog）顧。

**模擬的限制**：模擬的部位只存在交易程式自己的記憶裡，看門程式沒有另一個帳可以對、也沒有東西可以平——
第（三）項在模擬時不檢查，「平倉」只有記錄。接上元大之後才有真的券商連線（WatchBroker）。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from autotrade.config import Config, ConfigError
from autotrade.control import (
    HEARTBEAT_NAME,
    ControlError,
    ControlSource,
    outbox_name,
    read_local_stop,
    release_name,
    write_local_stop,
)
from autotrade.model import (
    ACTION_STOP,
    RESULT_IGNORED,
    RESULT_SUSPENDED,
    STAGE_ACK,
    STAGE_DONE,
    Command,
    CommandResult,
    require_taipei,
)

Alert = Callable[[datetime, str, Sequence[str]], None]


class WatchBroker(Protocol):
    """看門程式自己的券商連線。兩個動作都不能丟例外以外的東西出來騙人：做不到就在回傳的文字裡講。"""

    def position(self) -> int | None:
        """券商那邊現在的部位；沒有辦法知道是 None（模擬）。"""
        ...

    def flatten(self, why: str) -> tuple[str, str]:
        """把部位平掉。回傳（結果代碼、做了什麼）。"""
        ...


class SimWatchBroker:
    """模擬：沒有另一個帳。照實講做不到什麼。"""

    def position(self) -> int | None:
        """模擬的部位只在交易程式裡，這裡不知道。"""
        return None

    def flatten(self, why: str) -> tuple[str, str]:
        """沒有東西可以平：只回報這件事。"""
        return (
            RESULT_SUSPENDED,
            "模擬的部位只存在交易程式裡，看門程式沒有東西可以平；已要求交易程式停手、平倉",
        )


@dataclass
class Watchdog:
    """看門程式本身。step 用時鐘定時叫。"""

    cfg: Config
    folder: Path  # 交易程式的資料夾（心跳檔、回報檔在這）
    ticks_file: Path
    status: Path
    broker: WatchBroker
    source: ControlSource  # 看門程式自己的指令檔與回報檔（另一個資料夾）
    alert: Alert
    say: Callable[[str], None] = print
    _raised: set[str] = field(default_factory=set)
    _first_seen: dict[int, datetime] = field(default_factory=dict)
    _acked: dict[int, str] = field(default_factory=dict)  # 回報過「收到」的指令 → 當時的代碼
    _closed: set[int] = field(default_factory=set)  # 回報過最後結果的
    _source_ok: bool = True

    def __post_init__(self) -> None:
        if self.cfg.watchdog is None or self.cfg.control is None:
            raise ConfigError("設定檔沒有 [watchdog] 與 [control]，不能跑看門程式")

    def _age(self, path: Path, now: datetime) -> float | None:
        try:
            return max(0.0, now.timestamp() - path.stat().st_mtime)
        except FileNotFoundError:
            return None

    def _stop_active(self, now: datetime) -> bool:
        """今天是不是已經有一份還沒被解除的停手檔。"""
        try:
            asked = read_local_stop(self.folder, now.date())
        except ValueError:
            return True  # 讀不懂：交易程式會當成要停，這裡不蓋掉它
        if asked is None:
            return False
        release = self.folder / release_name(now.date())
        try:
            released = require_taipei(
                datetime.fromisoformat(str(json.loads(release.read_text(encoding="utf-8"))["at"])),
                "解除的時間",
            )
        except (FileNotFoundError, ValueError, KeyError, TypeError):
            return True
        return asked[0] > released

    def _act(self, now: datetime, head: str, lines: Sequence[str]) -> tuple[str, str]:
        """出事了：請券商平倉、要交易程式停手、通知。回傳券商那一步的（代碼、做了什麼）。"""
        code, did = self.broker.flatten(head)
        if not self._stop_active(now):
            write_local_stop(self.folder, now, head)
        self.say(f"{now:%H:%M:%S} 看門程式：{head}。{did}")
        self.alert(now, head, [*lines, did, "要在電腦上解除，程式才會再交易。"])
        return code, did

    def _check(self, kind: str, bad: bool, now: datetime, head: str, lines: Sequence[str]) -> None:
        if bad and kind not in self._raised:
            self._raised.add(kind)
            self._act(now, head, lines)
        elif not bad and kind in self._raised:
            self._raised.discard(kind)
            self.say(f"{now:%H:%M:%S} 看門程式：{head}——這個狀況解除了（停手還在，要人解除）")

    def _watch_trader(self, now: datetime) -> None:
        assert self.cfg.watchdog is not None
        w, session = self.cfg.watchdog, self.cfg.session
        if not session.start <= now.time() < session.end:
            self._raised -= {"heartbeat", "ticks", "position"}  # 盤外不盯；隔天重新算
            return
        beat = self._age(self.folder / HEARTBEAT_NAME, now)
        self._check(
            "heartbeat",
            beat is None or beat > w.heartbeat_stale_s,
            now,
            "交易程式沒有心跳",
            [
                "盤中找不到交易程式的心跳。"
                if beat is None
                else f"交易程式已經 {beat:.0f} 秒沒有動靜（上限 {w.heartbeat_stale_s:g} 秒）。"
            ],
        )
        opened = (
            now - datetime.combine(now.date(), session.start, tzinfo=now.tzinfo)
        ).total_seconds()
        # 今天有過行情才盯（交易程式收到第一筆才會寫狀態檔）：假日整天沒有行情不是出事，
        # 那種情況交易程式自己會在紀錄裡講「開盤了但沒收到行情」
        if opened >= w.tick_stale_s and self.status.exists():
            tick = self._age(self.ticks_file, now)
            self._check(
                "ticks",
                tick is None or tick > w.tick_stale_s,
                now,
                "盤中行情停住了",
                [
                    "今天還沒有任何行情進來。"
                    if tick is None
                    else f"行情已經 {tick:.0f} 秒沒有新的（上限 {w.tick_stale_s:g} 秒）。"
                ],
            )
        at_broker = self.broker.position()
        if at_broker is not None:
            mine = self._ledger_position()
            self._check(
                "position",
                mine is not None and mine != at_broker,
                now,
                "部位跟券商對不上",
                [f"交易程式帳上 {mine} 口，券商 {at_broker} 口。"],
            )

    def _ledger_position(self) -> int | None:
        try:
            return int(json.loads(self.status.read_text(encoding="utf-8"))["position"])
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.say(f"看門程式：狀態檔讀不懂，這一輪不比對部位：{exc}")
            return None

    def _main_reports(self, command_id: int, now: datetime) -> dict[str, tuple[str, str]]:
        """主程式對這筆指令回報過什麼：階段 →（代碼、說明）。"""
        path = self.folder / outbox_name(now.date())
        try:
            lines = path.read_text(encoding="utf-8").split("\n")[:-1]
        except FileNotFoundError:
            return {}
        found: dict[str, tuple[str, str]] = {}
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if int(row["command_id"]) == command_id:
                    found[str(row["stage"])] = (str(row["code"]), str(row["note"]))
            except (ValueError, KeyError, TypeError):
                continue  # 壞掉的一行由中繼程式回報；這裡只找這一筆指令的
        return found

    def _report(self, cmd: Command, now: datetime, stage: str, code: str, note: str) -> None:
        result = CommandResult(cmd.id, stage, code, note, created_at=cmd.created_at)
        self.source.report(result, now)
        if stage == STAGE_ACK:
            self._acked[cmd.id] = code
        else:
            self._closed.add(cmd.id)

    def _watch_stop(self, cmd: Command, now: datetime) -> None:
        assert self.cfg.watchdog is not None
        if cmd.id in self._closed:
            return
        if cmd.action != ACTION_STOP or cmd.day != now.date():
            if cmd.id not in self._acked:
                self._report(
                    cmd, now, STAGE_ACK, RESULT_IGNORED, "看門程式只處理今天的緊急停止，沒有執行"
                )
                self._closed.add(cmd.id)
            return
        first = self._first_seen.setdefault(cmd.id, now)
        main = self._main_reports(cmd.id, now)
        if cmd.id not in self._acked:
            if STAGE_ACK in main:
                code, note = main[STAGE_ACK]
                self._report(cmd, now, STAGE_ACK, code, f"主程式已經處理，看門程式沒有動作：{note}")
            elif (now - first).total_seconds() > self.cfg.watchdog.stop_grace_s:
                waited = (now - first).total_seconds()
                code, did = self._act(
                    now,
                    "遠端緊急停止：主程式沒有回報，由看門程式處理",
                    [f"停止按下去 {waited:.0f} 秒了，交易程式沒有回報收到。"],
                )
                self._report(cmd, now, STAGE_ACK, code, f"主程式沒有回報，由看門程式處理：{did}")
                self._report(cmd, now, STAGE_DONE, code, did)
            return
        if STAGE_DONE in main:
            code, note = main[STAGE_DONE]
            self._report(cmd, now, STAGE_DONE, code, f"主程式：{note}")

    def step(self, now: datetime) -> None:
        """看一輪。這裡的錯誤不能讓看門程式自己結束：指令檔讀不到只記一次，下一輪再試。"""
        now = require_taipei(now, "Watchdog.step 的 now")
        self._watch_trader(now)
        try:
            for cmd in self.source.poll(now):
                self._watch_stop(cmd, now)
        except ControlError as exc:
            if self._source_ok:
                self.say(
                    f"{now:%H:%M:%S} 看門程式：讀不到自己的指令檔（遠端停止的備援暫時沒有）：{exc}"
                )
            self._source_ok = False
        else:
            if not self._source_ok:
                self.say(f"{now:%H:%M:%S} 看門程式：指令檔恢復可以讀")
            self._source_ok = True
