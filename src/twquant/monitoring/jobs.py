"""伺服器端工作執行器（資料後台「一鍵補抓」D-018＋「手動補跑每日 pipeline」）。

在 API 伺服器端以子行程執行 twquant CLI，取代「複製指令、自己開終端貼上」。工作
分兩類、共用同一 JobManager（單一併發）：

- **補抓**：指令由缺口識別 (table, source, from, to) 推導（build_args）。
- **手動 pipeline**：排程若錯過每日觸發（機器離線等），從介面補跑完整每日 pipeline
  （pipeline_args＝`pipeline --date <day>`，與 scheduler 16:40 觸發同一路徑）。

安全邊界（D-018 對 D-016 唯讀原則的收斂放寬）：

- 白名單：補抓指令只由缺口識別推導、pipeline 只收單一交易日，皆不接受命令字串（無注入面）。
- 單一併發：同時最多一個工作——資料湖寫入不併發（pipeline 內含擷取，故與補抓互斥），
  也避免對免費來源疊加請求量。
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


def fetchable_tracks() -> list[dict[str, str]]:
    """可手動抓取的資料軌白名單（供前端「手動抓取歷史資料」表單的資料集選單）。

    單一真相源為 _TRACK_LABEL（＝INGEST_TRACKS 每日軌 + 月營收），與一鍵補抓共用；
    `unit` 決定前端輸入粒度：月營收以 YYYY-MM 為端點，其餘（含月批的指數，build_args
    內部自行截成月）收 YYYY-MM-DD。回傳順序即白名單插入順序（每日軌在前、月營收在後）。
    """
    return [
        {
            "table": table,
            "source": source,
            "label": label,
            "unit": "month" if table == "monthly_revenue" else "date",
        }
        for (table, source), label in _TRACK_LABEL.items()
    ]


def build_args(table: str, source: str, frm: str, to: str) -> list[str]:
    """由缺口識別推導 twquant 子指令 argv（白名單；非法輸入一律 ValueError）。

    - 月營收（from/to 為 YYYY-MM）與指數（月批）→ ingest-revenue / ingest-index 或對應 backfill。
    - 每日表單日 → `ingest --date`；區間 → `backfill --tables <table> --no-skip-existing`：
      backfill 的續傳判斷以 prices_daily 分區為準，補其他表時必須關閉，否則
      「行情已存在、法人缺」的日子會被整天跳過。
    """
    if table == "market_holidays":
        return ["ingest-holidays"]  # 官方開休市日曆：端點無年度參數，整年一抓（from/to 忽略）
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
    if table == "market_holidays":
        return "官方開休市日曆"
    zh = _TRACK_LABEL.get((table, source), f"{table}:{source}")
    return f"{zh} {frm}" if frm == to else f"{zh} {frm}~{to}"


# ── 手動補跑每日 pipeline（排程錯過時的介面補跑）─────────────────────────
def resolve_trading_day(date: str | None) -> str:
    """None／空字串 → 今日（台北）ISO；否則驗證為 ISO 日期字串（非法 → ValueError）。

    排程以 `now_tw().date()` 觸發，故手動補跑預設同語意；伺服器時區與台北可能不同，
    一律以台北換日，不用呼叫端的本地日期。
    """
    if not date:
        from twquant.conventions import now_tw

        return now_tw().date().isoformat()
    return _parse_date(date).isoformat()


def pipeline_args(day: str) -> list[str]:
    """手動補跑每日 pipeline 的 twquant 子指令 argv（day 為已解析 ISO 交易日）。

    與 scheduler 常駐迴圈跑的是同一 `pipeline` 指令（擷取→品質閘門→訊號→供應層→
    選股池快照，見 cli._run_daily_pipeline），故「補跑」等同「重放當日排程」。
    """
    return ["pipeline", "--date", day]


def pipeline_label(day: str) -> str:
    return f"每日 pipeline {day}"


def _weekday_span(a: dt.date, b: dt.date) -> int:
    """[a, b] 含端點的平日數；空資料湖尚無交易日曆時，作交易日數的保守估計。"""
    return sum(1 for i in range((b - a).days + 1) if (a + dt.timedelta(days=i)).weekday() < 5)


def needs_force(wh: Warehouse, table: str, source: str, frm: str, to: str) -> bool:
    """D-015 護欄：TWSE 行情回補超過 TWSE_BACKFILL_WARN 個交易日 → 需明示 force。

    空資料湖尚無交易日曆（`cal.load` 會去查 prices_daily）→ 改以區間平日數估交易日：
    讓「初次建置」也套同一護欄，且不因無日曆而讓端點 500（手動抓取表單的主要情境）。
    """
    if not (table == "prices_daily" and source == "twse"):
        return False
    a, b = _parse_date(frm), _parse_date(to)
    if wh.latest_date("prices_daily") is None:
        return _weekday_span(a, b) > TWSE_BACKFILL_WARN
    from twquant.data import calendar as cal

    return len(cal.load(wh).between(a, b)) > TWSE_BACKFILL_WARN


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
