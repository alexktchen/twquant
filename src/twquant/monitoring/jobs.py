"""補抓工作執行器（資料後台「一鍵補抓」，D-018）。

在 API 伺服器端以子行程執行 twquant CLI 補抓，取代「複製指令、自己開終端貼上」。
安全邊界（D-018 對 D-016 唯讀原則的收斂放寬）：

- 白名單：指令只由缺口識別 (table, source, from, to) 推導，不接受命令字串（無注入面）。
- 單一併發：同時最多一個工作——資料湖寫入不併發，也避免對免費來源疊加請求量。
- D-015 護欄：TWSE 行情大區間回補需呼叫端明示 force（前端先 confirm）。

工作狀態存於行程記憶體（uvicorn 單行程），重啟即清空——後台是操作台而非稽核
紀錄；成敗落在 ingest_log（單一真相源），矩陣/缺口端點自然反映補抓結果。
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field

from twquant.data.warehouse import Warehouse
from twquant.monitoring.snapshot import CONFIGS_DIR, INGEST_TRACKS, TWSE_BACKFILL_WARN

_REPO_ROOT = CONFIGS_DIR.parent
TAIL_LINES = 120  # 每個工作保留的輸出尾行數（記憶體上限）
HISTORY = 20  # 保留的工作歷史筆數

# 允許補抓的 (table, source)：每日軌 + 月營收（與缺口清單的來源一致）。
_TRACK_LABEL: dict[tuple[str, str], str] = {(t, s): label for t, s, label in INGEST_TRACKS}
_TRACK_LABEL[("monthly_revenue", "mops")] = "月營收"


def _parse_date(s: str) -> dt.date:
    try:
        return dt.date.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(f"日期格式錯誤：{s}") from exc


def _check_month(s: str) -> None:
    try:
        dt.datetime.strptime(s, "%Y-%m")  # 只驗格式，非時間點
    except ValueError as exc:
        raise ValueError(f"月份格式錯誤：{s}") from exc


def build_args(table: str, source: str, frm: str, to: str) -> list[str]:
    """由缺口識別推導 twquant 子指令 argv（白名單；非法輸入一律 ValueError）。

    - 月營收（from/to 為 YYYY-MM）與指數（月批）→ ingest-revenue / ingest-index 或對應 backfill。
    - 每日表單日 → `ingest --date`；區間 → `backfill --tables <table> --no-skip-existing`：
      backfill 的續傳判斷以 prices_daily 分區為準，補其他表時必須關閉，否則
      「行情已存在、法人缺」的日子會被整天跳過。
    """
    if (table, source) not in _TRACK_LABEL:
        raise ValueError(f"不在補抓白名單：{table}:{source}")
    if table == "monthly_revenue":
        _check_month(frm)
        _check_month(to)
        if frm == to:
            return ["ingest-revenue", "--month", frm]
        return ["backfill-revenue", "--from", frm, "--to", to]
    a, b = _parse_date(frm), _parse_date(to)
    if a > b:
        raise ValueError("起始日需早於或等於結束日")
    if table == "index_daily":
        fm, tm = frm[:7], to[:7]
        if fm == tm:
            return ["ingest-index", "--month", fm]
        return ["backfill-index", "--from", fm, "--to", tm]
    if a == b:
        return ["ingest", "--date", frm, "--tables", table]
    return ["backfill", "--from", frm, "--to", to, "--tables", table, "--no-skip-existing"]


def full_argv(args: list[str]) -> list[str]:
    """twquant 子指令 → 完整子行程 argv（同一個 Python 環境，免依賴 PATH）。"""
    return [sys.executable, "-m", "twquant", *args]


def display_command(args: list[str]) -> str:
    return "twquant " + " ".join(args)


def job_label(table: str, source: str, frm: str, to: str) -> str:
    zh = _TRACK_LABEL.get((table, source), f"{table}:{source}")
    return f"{zh} {frm}" if frm == to else f"{zh} {frm}~{to}"


def needs_force(wh: Warehouse, table: str, source: str, frm: str, to: str) -> bool:
    """D-015 護欄：TWSE 行情回補超過 TWSE_BACKFILL_WARN 個交易日 → 需明示 force。"""
    if not (table == "prices_daily" and source == "twse"):
        return False
    from twquant.data import calendar as cal

    sessions = cal.load(wh).between(_parse_date(frm), _parse_date(to))
    return len(sessions) > TWSE_BACKFILL_WARN


@dataclass
class Job:
    """單一補抓工作（tail 只留尾行；讀寫皆由 JobManager 的鎖保護）。"""

    id: str
    label: str
    command: str
    status: str = "running"  # running / success / failed
    started: str = ""
    ended: str | None = None
    exit_code: int | None = None
    tail: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "command": self.command,
            "status": self.status,
            "started": self.started,
            "ended": self.ended,
            "exit_code": self.exit_code,
            "tail": list(self.tail),
        }


class JobBusyError(RuntimeError):
    """已有補抓工作進行中（單一併發）。"""


class JobManager:
    """行程內補抓工作註冊表：單一併發、保留最近 HISTORY 筆、輸出尾行即時可查。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: list[Job] = []  # 新在前
        self._seq = 0

    def running(self) -> bool:
        with self._lock:
            return any(j.status == "running" for j in self._jobs)

    def jobs(self) -> list[dict[str, object]]:
        with self._lock:
            return [j.to_dict() for j in self._jobs]

    def get(self, jid: str) -> dict[str, object] | None:
        with self._lock:
            return next((j.to_dict() for j in self._jobs if j.id == jid), None)

    def start(self, label: str, command: str, argv: list[str]) -> dict[str, object]:
        """啟動子行程並登記工作；已有工作進行中 → JobBusyError。"""
        from twquant.conventions import now_tw

        env = {**os.environ, "NO_COLOR": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        with self._lock:
            if any(j.status == "running" for j in self._jobs):
                raise JobBusyError("已有補抓工作進行中，請等它結束")
            # argv 由白名單推導（build_args/full_argv），非使用者字串——無注入面。
            proc = subprocess.Popen(
                argv,
                cwd=_REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            self._seq += 1
            job = Job(
                id=f"j{self._seq}",
                label=label,
                command=command,
                started=now_tw().isoformat(timespec="seconds"),
            )
            self._jobs.insert(0, job)
            del self._jobs[HISTORY:]
        threading.Thread(target=self._pump, args=(job, proc), daemon=True).start()
        with self._lock:
            return job.to_dict()

    def _pump(self, job: Job, proc: subprocess.Popen[str]) -> None:
        """背景讀子行程輸出到尾行緩衝，結束時記錄狀態（daemon thread）。

        以 `with proc` 確保 stdout 管道在結束時關閉——否則子行程結束後 FileIO 被 GC
        會噴 ResourceWarning（strict 測試視為錯誤）。
        """
        from twquant.conventions import now_tw

        with proc:
            out = proc.stdout
            if out is not None:
                for line in out:
                    with self._lock:
                        job.tail.append(line.rstrip())
                        del job.tail[:-TAIL_LINES]
            code = proc.wait()
        with self._lock:
            job.exit_code = code
            job.status = "success" if code == 0 else "failed"
            job.ended = now_tw().isoformat(timespec="seconds")


MANAGER = JobManager()
