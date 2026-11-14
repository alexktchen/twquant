"""遠端緊急停止（A-011／D-225）在交易程式這一側。

交易程式自己**不連資料庫**：連雲端的事由另一支中繼程式（relay.py，另一個程序）做，兩邊只透過本機的檔案交換——
中繼程式把停止指令寫成「指令檔」，交易程式把處理結果接在「回報檔」後面。資料庫再慢、再卡，都拖不到交易。

這條路**只能叫停**。這個檔案裡沒有、也不可以加任何解除、開始交易或改設定的動作；解除只有在電腦前呼叫 `Engine.release_kill`。
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from autotrade.engine import Engine
from autotrade.model import ACTION_STOP, Command, CommandResult, require_taipei

INBOX_NAME = "commands.json"  # 中繼程式寫、交易程式讀：今天還沒被這支程式回報過的停止指令
REPLACE_TRIES = 5  # 改名被擋時最多試幾次
REPLACE_WAIT_S = 0.03  # 每次之間等多久
HEARTBEAT_NAME = "engine.seen"  # 交易程式每次讀指令檔就碰一下；中繼程式只看它的修改時間


def outbox_name(day: date) -> str:
    """回報檔的檔名：一天一個，交易程式只往後接，中繼程式只讀。"""
    return f"results_{day.isoformat()}.jsonl"


class ControlError(RuntimeError):
    """讀不到指令或回報寫不出去。交易不因此停下來，但要記下來。"""


@dataclass(frozen=True)
class ControlState:
    """遠端停止這條路現在的狀況，給狀態快照用。"""

    listening: bool
    ok: bool | None  # 最近一次讀指令成不成功；還沒讀過是 None
    checked_at: datetime | None  # 最近一次成功讀到的時間
    poll_s: float | None = None  # 多久讀一次；沒在聽是 None


class ControlSource(Protocol):
    """交易程式看得到的指令來源。兩個動作都必須很快回來（只碰本機），不可以連網路。"""

    def poll(self, now: datetime) -> list[Command]:
        """今天還沒處理完的指令。讀不到就丟 ControlError。"""
        ...

    def report(self, result: CommandResult, now: datetime) -> None:
        """記下一筆處理結果。寫不出去就丟 ControlError。"""
        ...


@dataclass
class ControlLoop:
    """把指令來源接到引擎。盤中執行程式要用**時鐘**定時呼叫 step（行情停住也要讀得到指令），而且只能在引擎那一條執行緒上叫。"""

    engine: Engine
    source: ControlSource
    poll_s: float
    # 「現在就該讀」的檢查（A-015）：回 True 就不等 poll_s。只能看本機的東西、要很快回來（例：FileControl.changed）
    urgent: Callable[[], bool] | None = None
    _next: datetime | None = None
    _ok: bool | None = None
    _checked_at: datetime | None = None
    _report_ok: bool = True
    _unsent: list[CommandResult] = field(default_factory=list)

    @property
    def state(self) -> ControlState:
        """現在的狀況。"""
        return ControlState(
            listening=True, ok=self._ok, checked_at=self._checked_at, poll_s=self.poll_s
        )

    def step(self, now: datetime) -> None:
        """到了該讀的時間就讀一次指令、交給引擎、記下結果。讀不到不影響交易，只記事件。"""
        now = require_taipei(now, "ControlLoop.step 的 now")
        waiting = self._next is not None and now < self._next
        if waiting and (self.urgent is None or not self.urgent()):
            return
        commands: list[Command] = []
        try:
            commands = self.source.poll(now)
        except ControlError as exc:
            if self._ok is not False:
                self.engine.note_control_link(now, up=False, detail=f"讀不到遠端指令：{exc}")
            self._ok = False
        else:
            if self._ok is False:
                self.engine.note_control_link(now, up=True, detail="遠端指令恢復可以讀")
            self._ok, self._checked_at = True, now
        # 就算這一次讀不到新指令，也要繼續追蹤先前的停止有沒有真的平掉
        self._unsent += self.engine.on_control(commands, now)
        self._send(now)
        self._next = now + timedelta(seconds=self.poll_s)

    def _send(self, now: datetime) -> None:
        while self._unsent:
            try:
                self.source.report(self._unsent[0], now)
            except ControlError as exc:
                if self._report_ok:
                    self.engine.note_control_link(
                        now, up=False, detail=f"停止的結果記不下來（之後會再試）：{exc}"
                    )
                self._report_ok = False
                return
            self._unsent.pop(0)
            if not self._report_ok:
                self.engine.note_control_link(now, up=True, detail="停止的結果已經記下來")
                self._report_ok = True


# ── 本機檔案的格式（交易程式與中繼程式共用）──


def command_to_json(cmd: Command) -> dict[str, Any]:
    """指令 → 指令檔裡的一筆。"""
    row: dict[str, Any] = {
        "id": cmd.id,
        "action": cmd.action,
        "day": cmd.day.isoformat(),
        "created_at": cmd.created_at.isoformat(),
    }
    if cmd.target is not None:  # 停止沒有對象（所有程式都執行），格式跟 A-011 一樣
        row["target"] = cmd.target
    return row


def command_from_json(row: dict[str, Any]) -> Command:
    """指令檔裡的一筆 → 指令。"""
    return Command(
        id=int(row["id"]),
        action=str(row["action"]),
        day=date.fromisoformat(str(row["day"])),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        target=None if row.get("target") is None else str(row["target"]),
    )


def result_to_json(result: CommandResult, now: datetime) -> dict[str, Any]:
    """處理結果 → 回報檔裡的一行。"""
    return {
        "command_id": result.command_id,
        "stage": result.stage,
        "code": result.code,
        "note": result.note,
        "created_at": None if result.created_at is None else result.created_at.isoformat(),
        "action": result.action,
        "after_tick": None if result.after_tick is None else result.after_tick.isoformat(),
        "at": now.isoformat(),
    }


def result_from_json(row: dict[str, Any]) -> CommandResult:
    """回報檔裡的一行 → 處理結果。"""
    return CommandResult(
        command_id=int(row["command_id"]),
        stage=str(row["stage"]),
        code=str(row["code"]),
        note=str(row["note"]),
        created_at=None
        if row.get("created_at") is None
        else require_taipei(datetime.fromisoformat(str(row["created_at"])), "created_at"),
        action=str(row.get("action", ACTION_STOP)),  # 舊的回報檔沒有這一欄：那時只有停止
        after_tick=None
        if row.get("after_tick") is None
        else require_taipei(datetime.fromisoformat(str(row["after_tick"])), "after_tick"),
    )


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """先寫暫存檔再改名：讀的人只會看到舊的或新的整份，不會看到寫到一半的。

    Windows 上對方正好開著檔在讀的那一瞬間改名會被擋（PermissionError）：等一下再試幾次。
    只有中繼程式會叫這個函式，等待不會發生在交易程式那一條執行緒上。
    """
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8", newline="\n")
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(REPLACE_WAIT_S)


@dataclass
class FileControl:
    """用本機資料夾裡的檔案當指令來源：只讀指令檔、只往回報檔後面接。不連網路。"""

    folder: Path
    stale_s: float
    _seen: tuple[int, int] | None = None  # 上次讀的時候指令檔是哪一個（檔案編號、修改時間）

    def _stamp(self) -> tuple[int, int] | None:
        try:
            info = (self.folder / INBOX_NAME).stat()
        except OSError:
            return None
        return info.st_ino, info.st_mtime_ns

    def changed(self) -> bool:
        """指令檔在上次讀過之後換新了沒（A-015）。只看檔案本身、不讀內容；還沒有檔案算沒有。

        中繼程式每一輪都會換一份新的；被門鈴叫醒時馬上換。交易程式看到換新就馬上讀，不必等自己的 poll_s。
        """
        stamp = self._stamp()
        return stamp is not None and stamp != self._seen

    def _read_inbox(self) -> str:
        path = self.folder / INBOX_NAME
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ControlError(f"還沒有指令檔 {INBOX_NAME}（中繼程式沒在跑？）") from exc
        except PermissionError:
            # Windows 上中繼程式正好在換檔的那一瞬間會讀不到：馬上再讀一次
            try:
                return path.read_text(encoding="utf-8")
            except OSError as exc:
                raise ControlError(f"讀不到指令檔：{exc}") from exc
        except OSError as exc:
            raise ControlError(f"讀不到指令檔：{exc}") from exc

    def poll(self, now: datetime) -> list[Command]:
        """讀指令檔。檔案太舊、或中繼程式自己說連不上雲端，都算讀不到。"""
        try:
            (self.folder / HEARTBEAT_NAME).write_text(now.isoformat(), encoding="utf-8")
        except OSError as exc:
            raise ControlError(f"寫不了 {HEARTBEAT_NAME}：{exc}") from exc
        # 先記下是哪一份再讀：讀的當下又被換掉，下一圈會再讀一次，不會漏
        self._seen = self._stamp()
        text = self._read_inbox()
        try:
            data = json.loads(text)
            written = require_taipei(datetime.fromisoformat(str(data["written_at"])), "written_at")
            ok, error = bool(data["ok"]), data.get("error")
            commands = [command_from_json(row) for row in data["commands"]]
        except (ValueError, KeyError, TypeError) as exc:
            raise ControlError(f"指令檔的內容不對：{exc}") from exc
        age = (now - written).total_seconds()
        if age > self.stale_s:
            raise ControlError(f"指令檔已經 {age:.0f} 秒沒更新（中繼程式沒在跑？）")
        if not ok:
            raise ControlError(f"中繼程式讀不到雲端的指令：{error}")
        return commands

    def report(self, result: CommandResult, now: datetime) -> None:
        """把一筆結果接在今天的回報檔後面（一行一筆）。"""
        line = json.dumps(result_to_json(result, now), ensure_ascii=False)
        path = self.folder / outbox_name(now.date())
        try:
            # 上一次寫到一半就斷掉（斷電、磁碟滿）會留下沒有換行結尾的殘行：先補換行，殘行自己成一行，不吃掉這一筆
            torn = (
                path.exists() and path.stat().st_size > 0 and not path.read_bytes().endswith(b"\n")
            )
            with path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(("\n" if torn else "") + line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            raise ControlError(f"寫不了回報檔：{exc}") from exc
