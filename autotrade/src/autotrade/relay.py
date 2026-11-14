"""中繼程式（A-011／D-225）：交易程式跟雲端之間**唯一會連資料庫**的東西，另外一支程序在跑。

每隔幾秒做四件事：
（一）從資料庫拿今天還沒被「這支交易程式」回報過的停止指令，寫成本機的指令檔；
（二）把交易程式接在回報檔後面的結果送回資料庫；
（三）告訴資料庫「中繼程式還活著、交易程式多久前讀過指令檔」，網站才知道現在按停止到不到得了；
（四）狀態檔有更新就送上去。

資料庫慢、斷線、卡住，只會卡住這支程式；交易程式照跑，而且從指令檔的時間看得出這條路斷了。
這裡同樣只能叫停：不新增指令、不刪指令、不寫任何會讓交易程式開始下單的東西。
"""

from __future__ import annotations

import importlib
import json
import time as time_module
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol

from autotrade.bell import CHANNEL, Bell
from autotrade.control import (
    HEARTBEAT_NAME,
    INBOX_NAME,
    ControlError,
    command_to_json,
    outbox_name,
    result_from_json,
    write_json_atomic,
)
from autotrade.export import STATUS_UPSERT, status_row
from autotrade.model import STAGE_ACK, STAGE_DONE, TAIPEI, Command, CommandResult

CONSUMERS = ("sim", "live", "watchdog")

# 跟網站 frontend/functions/_lib/autotrade.ts 的 COMMANDS_DDL 一字不差（兩邊各有測試守著）。
# 指令表只可能有「停止」（CHECK）；回報一支程式一列，模擬的回報蓋不到真單的；RLS 是多一道保險。
COMMANDS_DDL: tuple[str, ...] = (
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

# 門鈴（A-015／D-228）：指令表每新增一列，資料庫自己發一則通知，中繼程式不必等下一次輪詢。
# 由資料庫發（觸發器）而不是網站發：網站走的 Cloudflare Hyperdrive 不支援 NOTIFY。通知只帶表名與編號，不當指令用。
# 不放進 COMMANDS_DDL：那八句跟網站一字不差；這兩句只有中繼程式帶 --bell 啟動時才建，可以重複執行。
BELL_DDL: tuple[str, ...] = (
    "CREATE OR REPLACE FUNCTION web.autotrade_ring() RETURNS trigger LANGUAGE plpgsql "
    f"SET search_path = '' AS $$BEGIN PERFORM pg_notify('{CHANNEL}', TG_TABLE_NAME || ':' || NEW.id::text); "
    "RETURN NULL; END$$",
    "CREATE OR REPLACE TRIGGER autotrade_commands_ring AFTER INSERT ON web.autotrade_commands "
    "FOR EACH ROW EXECUTE FUNCTION web.autotrade_ring()",
)

# 一次最多拿幾筆（一天的上限是 20 筆，這只是保險）
PENDING_LIMIT = 50
# 今天、而且「這支程式」還沒回報過的指令。不用「編號比上次大」：指令表重建過、編號重來，也不會漏
PENDING_SQL = (
    "SELECT c.id, c.action, c.day, c.created_at FROM web.autotrade_commands c "
    "WHERE c.day = %s AND NOT EXISTS (SELECT 1 FROM web.autotrade_command_acks a "
    f"WHERE a.command_id = c.id AND a.consumer = %s) ORDER BY c.id LIMIT {PENDING_LIMIT}"
)
# 一支程式對一筆指令只留第一次的回報；時間用資料庫的時鐘
# 只回報給「編號與按下時間都對得上」的那一筆：指令表重建過、編號重來時，舊的回報不會記到新指令頭上；
# 指令已經不在了就什麼都不寫（不會因為外鍵錯誤卡住後面的回報）
ACK_SQL = (
    "INSERT INTO web.autotrade_command_acks (command_id, consumer, code, note) "
    "SELECT c.id, %s, %s, %s FROM web.autotrade_commands c WHERE c.id = %s AND c.created_at = %s "
    "ON CONFLICT (command_id, consumer) DO NOTHING"
)
# 最後的結果自己就寫得進去（前面的 ack 萬一沒送到，也不會默默更新不到任何一列）
DONE_SQL = (
    "INSERT INTO web.autotrade_command_acks "
    "(command_id, consumer, code, note, done_at, done_code, done_note) "
    "SELECT c.id, %s, %s, %s, now(), %s, %s FROM web.autotrade_commands c "
    "WHERE c.id = %s AND c.created_at = %s "
    "ON CONFLICT (command_id, consumer) DO UPDATE SET done_at = now(), "
    "done_code = EXCLUDED.done_code, done_note = EXCLUDED.done_note"
)
BEAT_SQL = (
    "INSERT INTO web.autotrade_listeners (consumer, relay_seen_at, engine_seen_at, poll_s) "
    "VALUES (%s, now(), now() - make_interval(secs => %s::double precision), %s) "
    "ON CONFLICT (consumer) DO UPDATE SET relay_seen_at = EXCLUDED.relay_seen_at, "
    "engine_seen_at = EXCLUDED.engine_seen_at, poll_s = EXCLUDED.poll_s"
)
# 每一筆交易開頭先設逾時（set_config 的第三個參數 true＝只在這筆交易有效；連線池是交易層級的，設在連線上留不住）
TIMEOUT_SQL = (
    "SELECT set_config('statement_timeout', %s, true), set_config('lock_timeout', %s, true)"
)


class RelayDb(Protocol):
    """中繼程式用到的資料庫動作。出錯一律丟 ControlError。"""

    def pending(self, today: date, consumer: str) -> list[Command]:
        """今天、這支程式還沒回報過的指令。"""
        ...

    def ack(self, result: CommandResult, consumer: str) -> None:
        """記下「收到了、當下怎麼處理」（只留第一次）。"""
        ...

    def done(self, result: CommandResult, consumer: str) -> None:
        """記下最後的結果（之後的會蓋掉之前的）。"""
        ...

    def beat(self, consumer: str, engine_age_s: float | None, poll_s: float) -> None:
        """回報中繼程式還活著，以及交易程式多久前讀過指令檔（沒讀過是 None）。"""
        ...

    def push_status(self, row: tuple[str, str, str, str, str, str]) -> None:
        """把一份狀態寫進 serving.autotrade_status。"""
        ...


class PgRelayDb:
    """用 psycopg 連資料庫。每個動作自己一筆交易、有逾時；連線斷了下一次重連。"""

    def __init__(
        self, connect: Callable[[], Any], errors: tuple[type[BaseException], ...], timeout_s: float
    ) -> None:
        self._connect = connect
        self._errors = errors
        self._timeout_ms = str(int(timeout_s * 1000))
        self._conn: Any = None

    def _run(self, sql: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
        try:
            if self._conn is None:
                self._conn = self._connect()
            with self._conn.transaction():
                self._conn.execute(TIMEOUT_SQL, (self._timeout_ms, self._timeout_ms))
                cursor = self._conn.execute(sql, params)
                return list(cursor.fetchall()) if cursor.description is not None else []
        except self._errors as exc:
            conn, self._conn = self._conn, None
            if conn is not None:
                conn.close()
            raise ControlError(str(exc).strip() or type(exc).__name__) from exc

    def ensure(self) -> None:
        """確保三張表都在。只在啟動時做一次，不在盤中重連時做。"""
        for statement in COMMANDS_DDL:
            self._run(statement, ())

    def ensure_bell(self) -> None:
        """確保門鈴的觸發器在（A-015）。只有開門鈴時才做，跟建表一樣只做一次。"""
        for statement in BELL_DDL:
            self._run(statement, ())

    def pending(self, today: date, consumer: str) -> list[Command]:
        """今天、這支程式還沒回報過的指令。"""
        return [
            Command(id=int(i), action=str(action), day=day, created_at=created_at)
            for i, action, day, created_at in self._run(PENDING_SQL, (today, consumer))
        ]

    def ack(self, result: CommandResult, consumer: str) -> None:
        """記下「收到了、當下怎麼處理」。"""
        self._run(
            ACK_SQL, (consumer, result.code, result.note, result.command_id, result.created_at)
        )

    def done(self, result: CommandResult, consumer: str) -> None:
        """記下最後的結果。"""
        self._run(
            DONE_SQL,
            (
                consumer,
                result.code,
                result.note,
                result.code,
                result.note,
                result.command_id,
                result.created_at,
            ),
        )

    def beat(self, consumer: str, engine_age_s: float | None, poll_s: float) -> None:
        """回報中繼程式還活著、交易程式多久前讀過指令檔。"""
        self._run(BEAT_SQL, (consumer, engine_age_s, poll_s))

    def push_status(self, row: tuple[str, str, str, str, str, str]) -> None:
        """把一份狀態寫進 serving.autotrade_status（表由 push_status.py 或 twquant 建）。"""
        self._run(STATUS_UPSERT, row)


def open_pg(dsn: str, timeout_s: float) -> PgRelayDb:
    """用連線字串建立資料庫連線。需要 psycopg（pip install "autotrade[web]"）。"""
    psycopg = importlib.import_module("psycopg")

    def connect() -> Any:
        # prepare_threshold=None：雲端的連線池（Supabase transaction pooler）不支援預備語句，
        # 不關掉的話同一句查詢跑到第五次就會開始失敗（本機資料庫測不出來）。keepalives：線路默默斷掉時十幾秒內發現。
        return psycopg.connect(
            dsn,
            autocommit=True,
            prepare_threshold=None,
            connect_timeout=max(1, int(timeout_s)),
            keepalives=1,
            keepalives_idle=5,
            keepalives_interval=2,
            keepalives_count=2,
        )

    return PgRelayDb(connect, errors=(psycopg.Error, OSError), timeout_s=timeout_s)


@dataclass(frozen=True)
class RelayReport:
    """中繼程式跑一輪的結果（給記錄與測試看）。"""

    ok: bool
    commands: int
    shipped: int
    status_pushed: bool
    error: str | None


@dataclass
class Relay:
    """資料庫 ↔ 本機檔案。consumer 是這支交易程式的名字（sim／live／watchdog）。"""

    db: RelayDb
    consumer: str
    folder: Path
    poll_s: float
    status: Path | None = None
    ensure: Callable[[], None] | None = None  # 確保資料表存在；成功一次之後就不再叫
    _ensured: bool = False
    _day: date | None = None
    _shipped: int = 0
    _status_mtime: float | None = None

    def __post_init__(self) -> None:
        if self.consumer not in CONSUMERS:
            raise ValueError(f"consumer 要是 {CONSUMERS} 其中之一，拿到 {self.consumer!r}")

    def _new_lines(self, today: date) -> list[str]:
        if self._day != today:
            self._day, self._shipped = today, 0  # 換日換檔，從頭算
        path = self.folder / outbox_name(today)
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").split("\n")[:-1]  # 最後一段沒有換行結尾＝還在寫
        return lines[self._shipped :]

    def _ship(self, today: date) -> tuple[int, str | None]:
        """把回報檔裡還沒送的結果照順序送回資料庫；回傳（送了幾筆、有什麼問題）。

        資料庫出錯就停在那一行，下一輪再送。內容不對的那一行跳過並回報——不能讓一行壞資料卡住後面真正的結果。
        """
        shipped, problem = 0, None
        for line in self._new_lines(today):
            try:
                result = result_from_json(json.loads(line))
                if result.stage not in (STAGE_ACK, STAGE_DONE):
                    raise ValueError(f"不認得的階段 {result.stage!r}")
                if result.created_at is None:
                    raise ValueError("沒有帶指令按下的時間")
            except (ValueError, KeyError, TypeError) as exc:
                problem = problem or f"回報檔有一行內容不對（已跳過）：{exc}"
                self._shipped += 1
                continue
            try:
                if result.stage == STAGE_ACK:
                    self.db.ack(result, self.consumer)
                else:
                    self.db.done(result, self.consumer)
            except ControlError as exc:
                return shipped, str(exc)
            self._shipped += 1
            shipped += 1
        return shipped, problem

    def _engine_age(self, now: datetime) -> float | None:
        path = self.folder / HEARTBEAT_NAME
        if not path.exists():
            return None
        return max(0.0, now.timestamp() - path.stat().st_mtime)

    def step(self, now: datetime) -> RelayReport:
        """跑一輪。資料庫或檔案出錯都不會丟例外（中繼程式不能因為一次錯誤就結束）：回報在結果裡，下一輪再試。"""
        try:
            return self._step(now)
        except (OSError, UnicodeDecodeError) as exc:
            return RelayReport(False, 0, 0, False, f"檔案讀寫出錯：{exc}")

    def _step(self, now: datetime) -> RelayReport:
        if self.ensure is not None and not self._ensured:
            # 開機時網路常常還沒好：連不上就寫進指令檔、下一輪再試，不讓整支程式結束
            try:
                self.ensure()
            except ControlError as exc:
                write_json_atomic(
                    self.folder / INBOX_NAME,
                    {"written_at": now.isoformat(), "ok": False, "error": str(exc), "commands": []},
                )
                return RelayReport(False, 0, 0, False, str(exc))
            self._ensured = True
        # 先把結果送回去，再拿指令：剛回報過的指令就不會又出現在指令檔裡
        shipped, ship_error = self._ship(now.date())
        commands: list[Command] = []
        poll_error: str | None = None
        try:
            commands = self.db.pending(now.date(), self.consumer)
        except ControlError as exc:
            poll_error = str(exc)
        write_json_atomic(
            self.folder / INBOX_NAME,
            {
                "written_at": now.isoformat(),
                "ok": poll_error is None,
                "error": poll_error,
                "commands": [command_to_json(c) for c in commands],
            },
        )
        pushed, other_error = False, None
        try:
            self.db.beat(self.consumer, self._engine_age(now), self.poll_s)
            pushed = self._push_status()
        except ControlError as exc:
            other_error = str(exc)
        except (ValueError, KeyError, TypeError) as exc:
            other_error = f"狀態檔的內容不對：{exc}"
        error = poll_error or ship_error or other_error
        return RelayReport(error is None, len(commands), shipped, pushed, error)

    def _push_status(self) -> bool:
        if self.status is None or not self.status.exists():
            return False
        mtime = self.status.stat().st_mtime
        if mtime == self._status_mtime:
            return False
        self.db.push_status(status_row(self.status.read_text(encoding="utf-8")))
        self._status_mtime = mtime
        return True


def run(
    relay: Relay,
    *,
    once: bool = False,
    log: Callable[[str], None] = print,
    bell: Bell | None = None,
    sleep: Callable[[float], None] = time_module.sleep,
) -> None:
    """一直跑到被中斷（Ctrl+C）。只在「好 ↔ 壞」改變、或有東西送出去時印一行，不洗版。

    bell（A-015）：有門鈴時，兩輪之間不是睡覺而是等門鈴——有人按就馬上跑下一輪，沒人按也照樣每 poll_s 一輪。
    """
    last_ok: bool | None = None
    bell_ok: bool | None = None
    while True:
        now = datetime.now(TAIPEI)
        report = relay.step(now)
        if report.ok != last_ok or report.shipped or report.commands:
            state = "正常" if report.ok else f"有問題：{report.error}"
            log(
                f"{now:%H:%M:%S} {relay.consumer} {state}｜待處理指令 {report.commands}｜"
                f"送出回報 {report.shipped}｜狀態{'已送' if report.status_pushed else '沒變'}"
            )
        last_ok = report.ok
        if once:
            return
        if bell is None:
            sleep(relay.poll_s)
            continue
        rung = bell.wait(relay.poll_s)
        now = datetime.now(TAIPEI)
        heard = bell.state
        if heard.ok is not None and heard.ok != bell_ok:
            bell_ok = heard.ok
            log(
                f"{now:%H:%M:%S} {relay.consumer} 門鈴接上了：資料庫一有新的指令會馬上知道"
                if heard.ok
                else f"{now:%H:%M:%S} {relay.consumer} 門鈴聽不到：{heard.detail}。"
                f"照舊每 {relay.poll_s:g} 秒問一次，不影響遠端停止"
            )
        if rung:
            log(f"{now:%H:%M:%S} {relay.consumer} 門鈴響了：資料庫有新的指令，馬上去拿")
