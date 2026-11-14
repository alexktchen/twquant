"""後台啟動的背景程式（A-012）：演練時的「中繼程式＋演練程式」兩個程序。一次只准一場演練。

程式用 `python -m autotrade …` 另外起程序，輸出寫進資料夾裡的紀錄檔，後台只看紀錄檔與程序還在不在。
連線字串不經過後台：只把「環境變數名稱」與 `.env` 的路徑交給中繼程式，由它自己讀。
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import IO, Any, Protocol

from autotrade.model import TAIPEI

DSN_ENVS = ("TWQUANT_PG_DSN", "TWQUANT_SUPABASE_DSN")  # 本機資料庫、雲端資料庫
TAIL_LINES = 40


class Proc(Protocol):
    """一個在跑的程序（subprocess.Popen 的一小部分）。"""

    def poll(self) -> int | None:
        """還在跑回 None，結束了回結束碼。"""
        ...

    def terminate(self) -> None:
        """請它結束。"""
        ...

    def wait(self, timeout: float | None = None) -> int:
        """等它結束。"""
        ...

    def kill(self) -> None:
        """強制結束。"""
        ...


Spawner = Callable[[Sequence[str], IO[str], Path], Proc]


def popen(args: Sequence[str], log: IO[str], cwd: Path) -> Proc:
    """真的起一個程序（輸出不緩衝，紀錄檔馬上看得到）。"""
    return subprocess.Popen(
        list(args), stdout=log, stderr=subprocess.STDOUT, cwd=cwd, env=child_env(dict(os.environ))
    )


def child_env(environ: dict[str, str]) -> dict[str, str]:
    """開出來的程式要用的環境：輸出不緩衝、UTF-8，而且一定找得到 autotrade 這個套件。

    後台如果是用 `tools/run.py` 開的，套件的位置只加在後台自己這個程序裡，
    開出來的 `python -m autotrade …` 會找不到（2026-11-14 演練一開就結束）。所以明講給它。
    """
    package_parent = str(Path(__file__).resolve().parents[1])
    old = environ.get("PYTHONPATH", "")
    return {
        **environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": package_parent + (os.pathsep + old if old else ""),
    }


@dataclass(frozen=True)
class DrillRequest:
    """後台送來的演練要求。"""

    config: Path
    ticks: Path
    day: date
    start: str
    minutes: float
    speed: float
    dsn_env: str | None  # None＝不起中繼程式（只在本機看演練程式自己的反應）
    feed: str = "taifex"  # recorded＝富邦即時錄下來的


class JobError(RuntimeError):
    """演練起不來：已經有一場在跑、參數不對。"""


def tail(path: Path, lines: int = TAIL_LINES) -> list[str]:
    """紀錄檔最後幾行（檔案還沒有就是空的）。"""
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]


@dataclass
class DrillJob:
    """一場演練：中繼程式（可以沒有）＋演練程式。"""

    folder: Path
    started_at: datetime
    request: DrillRequest
    procs: dict[str, Proc]
    logs: dict[str, IO[str]]
    commands: dict[str, list[str]] = field(default_factory=dict)  # 每個程序是用哪一行指令開的
    stopped: bool = False  # 是使用者按停止停掉的（不是自己結束）

    def running(self) -> bool:
        """演練程式還在不在跑。"""
        return self.procs["drill"].poll() is None

    def stop(self, wait_s: float = 10.0) -> None:
        """兩個程序都請它結束；不肯就強制結束。"""
        for proc in self.procs.values():
            if proc.poll() is None:
                proc.terminate()
        for proc in self.procs.values():
            try:
                proc.wait(timeout=wait_s)
            except subprocess.TimeoutExpired:
                proc.kill()
        for log in self.logs.values():
            log.close()

    def state(self) -> dict[str, Any]:
        """給後台頁面看的現況。"""
        return {
            "running": self.running(),
            "started_at": self.started_at.isoformat(),
            "folder": str(self.folder),
            "day": self.request.day.isoformat(),
            "start": self.request.start,
            "minutes": self.request.minutes,
            "speed": self.request.speed,
            "relay": self.request.dsn_env,
            "feed": self.request.feed,
            "stopped": self.stopped,
            "procs": {name: proc.poll() for name, proc in self.procs.items()},
            "commands": {name: " ".join(args) for name, args in self.commands.items()},
            "pids": {name: getattr(proc, "pid", None) for name, proc in self.procs.items()},
            "logs": {name: tail(self.folder / f"{name}.log") for name in self.procs},
        }


@dataclass
class Jobs:
    """後台手上的背景程式。data_dir 底下每場演練一個資料夾。"""

    data_dir: Path
    env_file: Path | None = None
    spawn: Spawner = popen
    python: str = sys.executable
    workdir: Path = field(default_factory=lambda: Path(__file__).resolve().parents[2])
    drill: DrillJob | None = None

    def start_drill(self, req: DrillRequest, now: datetime | None = None) -> DrillJob:
        """起一場演練。已經有一場在跑就拒絕。"""
        if self.drill is not None and self.drill.running():
            raise JobError("已經有一場演練在跑；先停掉再開新的")
        if self.drill is not None:
            self.drill.stop()
        if req.dsn_env is not None and req.dsn_env not in DSN_ENVS:
            raise JobError(f"資料庫只能選 {DSN_ENVS}：{req.dsn_env!r}")
        if not req.ticks.is_file():
            raise JobError(f"找不到逐筆檔：{req.ticks.name}")
        if not req.config.is_file():
            raise JobError(f"找不到設定檔：{req.config.name}")
        if not 0 < req.minutes <= 120 or not 0 < req.speed <= 60:
            raise JobError("分鐘要在 0～120、倍速要在 0～60")
        now = now or datetime.now(TAIPEI)
        folder = self.data_dir / f"drill_{now:%Y%m%d_%H%M%S}"
        again = 1
        while folder.exists():  # 同一秒內停掉又開一場：資料夾名字後面加編號
            again += 1
            folder = self.data_dir / f"drill_{now:%Y%m%d_%H%M%S}_{again}"
        folder.mkdir(parents=True, exist_ok=False)
        logs: dict[str, IO[str]] = {}
        procs: dict[str, Proc] = {}
        commands: dict[str, list[str]] = {}
        base = [self.python, "-m", "autotrade"]
        if req.dsn_env is not None:
            relay = [*base, "relay", "--config", str(req.config), "--dir", str(folder)]
            relay += ["--consumer", "sim", "--dsn-env", req.dsn_env]
            if self.env_file is not None:
                relay += ["--env-file", str(self.env_file)]
            logs["relay"] = (folder / "relay.log").open("w", encoding="utf-8")
            procs["relay"] = self.spawn(relay, logs["relay"], self.workdir)
            commands["relay"] = relay
        drill = [*base, "drill", "--config", str(req.config), "--ticks", str(req.ticks)]
        drill += ["--day", req.day.isoformat(), "--from", req.start, "--dir", str(folder)]
        drill += ["--minutes", f"{req.minutes:g}", "--speed", f"{req.speed:g}"]
        drill += ["--feed", req.feed]
        logs["drill"] = (folder / "drill.log").open("w", encoding="utf-8")
        procs["drill"] = self.spawn(drill, logs["drill"], self.workdir)
        commands["drill"] = drill
        self.drill = DrillJob(folder, now, req, procs, logs, commands)
        return self.drill

    def stop_drill(self) -> bool:
        """停掉現在的演練；沒有在跑的回 False。"""
        if self.drill is None:
            return False
        was = self.drill.running()
        self.drill.stopped = self.drill.stopped or was
        self.drill.stop()
        return was

    def close(self) -> None:
        """後台關掉時把演練一起停掉。"""
        self.stop_drill()
