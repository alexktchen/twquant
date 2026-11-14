"""後台啟動的背景程式（A-012）：演練時的「中繼程式＋演練程式」兩個程序。一次只准一場演練。

程式用 `python -m autotrade …` 另外起程序，輸出寫進資料夾裡的紀錄檔，後台只看紀錄檔與程序還在不在。
連線字串不經過後台：只把「環境變數名稱」與 `.env` 的路徑交給中繼程式，由它自己讀。
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import IO, Any, Protocol

from autotrade.model import TAIPEI
from autotrade.runner import QUIT_NAME

DSN_ENVS = ("TWQUANT_PG_DSN", "TWQUANT_SUPABASE_DSN")  # 本機資料庫、雲端資料庫
TAIL_LINES = 40
TICKS_DSN_ENV = "TWQUANT_PG_DSN"  # 富邦錄製程式把逐筆寫在本機資料庫，行情中繼只讀那裡
TICKS_NAME = "ticks.csv"
STATUS_NAME = "status.json"
PLAY_SPEED_MAX = 240.0


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


@dataclass(frozen=True)
class RunRequest:
    """後台送來的「啟動交易程式」（A-014）。source：live＝今天的即時行情、play＝回放錄好的一天。"""

    config: Path
    strategy: str
    ms_repo: Path | None
    source: str
    dsn_env: str | None = None  # live：指令中繼連哪個資料庫（遠端停止、把狀態送上網站）
    notify: bool = False  # live：發手機通知
    release_stop: bool = False  # live：解除今天稍早的遠端停止
    play_file: Path | None = None
    play_day: date | None = None
    start: str = "08:44:50"  # play：從那一天的幾點開始
    speed: float = 1.0  # play：幾倍速


def read_status(path: Path) -> dict[str, Any] | None:
    """交易程式寫的狀態檔裡，後台頁面要看的那幾項；還沒有檔案回 None。"""
    if not path.exists():
        return None
    try:
        snap = json.loads(path.read_text(encoding="utf-8"))
        return {
            key: snap[key]
            for key in (
                "day",
                "status",
                "state",
                "halt",
                "position",
                "avg_price",
                "last_price",
                "last_ts",
                "day_pnl_twd",
                "realized_twd",
                "open_pnl_twd",
                "entries",
                "updated_at",
            )
        } | {"fills": snap["fills"][-8:], "fill_count": len(snap["fills"])}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"error": f"狀態檔讀不懂：{exc}"}


@dataclass
class RunJob:
    """一次交易程式的執行：行情中繼＋指令中繼（回放沒有這兩支）＋交易程式。"""

    folder: Path
    started_at: datetime
    request: RunRequest
    procs: dict[str, Proc]
    logs: dict[str, IO[str]]
    commands: dict[str, list[str]]
    stopped: bool = False  # 是使用者按「結束」的（不是自己結束）
    ended_at: datetime | None = None  # 後台發現它結束的時間
    helpers_stopped: bool = False
    warned: set[str] = field(default_factory=set)  # 自己結束、已經報過的中繼程式

    def running(self) -> bool:
        """交易程式還在不在跑。"""
        return self.procs["run"].poll() is None

    def ask_quit(self) -> None:
        """請交易程式自己收工（它每一圈都會看這個檔）。"""
        (self.folder / QUIT_NAME).write_text("quit", encoding="utf-8")

    def stop_all(self, wait_s: float = 10.0) -> None:
        """還活著的程序都請它結束；不肯就強制結束。兩支中繼程式也在這裡收掉。"""
        _end(self.procs.values(), wait_s)
        for log in self.logs.values():
            log.close()
        self.helpers_stopped = True

    def state(self) -> dict[str, Any]:
        """給後台頁面看的現況。"""
        req = self.request
        return {
            "running": self.running(),
            "started_at": self.started_at.isoformat(),
            "folder": str(self.folder),
            "config": req.config.name,
            "source": req.source,
            "relay": req.dsn_env,
            "notify": req.notify,
            "play_day": None if req.play_day is None else req.play_day.isoformat(),
            "start": req.start,
            "speed": req.speed,
            "stopped": self.stopped,
            "procs": {name: proc.poll() for name, proc in self.procs.items()},
            "commands": {name: " ".join(args) for name, args in self.commands.items()},
            "pids": {name: getattr(proc, "pid", None) for name, proc in self.procs.items()},
            "logs": {name: tail(self.folder / f"{name}.log") for name in self.procs},
            "status": read_status(self.folder / STATUS_NAME),
        }


def _end(procs: Iterable[Proc], wait_s: float) -> None:
    alive = [proc for proc in procs if proc.poll() is None]
    for proc in alive:
        proc.terminate()
    for proc in alive:
        try:
            proc.wait(timeout=wait_s)
        except subprocess.TimeoutExpired:
            proc.kill()


@dataclass
class Jobs:
    """後台手上的背景程式。data_dir 底下每場演練一個資料夾。"""

    data_dir: Path
    env_file: Path | None = None
    spawn: Spawner = popen
    python: str = sys.executable
    workdir: Path = field(default_factory=lambda: Path(__file__).resolve().parents[2])
    # 指令中繼聽門鈴（A-015）時，哪個資料庫要改連哪個埠（環境變數名 → 埠）；沒列的用連線字串自己的埠
    bell_ports: dict[str, int] = field(default_factory=dict)
    # 後台自己的中繼程式（A-017）：聽網站來的「啟動／結束交易程式」
    remote_proc: Proc | None = None
    remote_log: IO[str] | None = None
    remote_command: list[str] = field(default_factory=list)
    drill: DrillJob | None = None
    run: RunJob | None = None

    def start_drill(self, req: DrillRequest, now: datetime | None = None) -> DrillJob:
        """起一場演練。已經有一場在跑就拒絕。"""
        if self.drill is not None and self.drill.running():
            raise JobError("已經有一場演練在跑；先停掉再開新的")
        if self.run is not None and self.run.running():
            raise JobError("交易程式正在跑；先按「結束」再演練")
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
            relay += ["--consumer", "sim", "--dsn-env", req.dsn_env, *self._bell(req.dsn_env)]
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

    def _bell(self, dsn_env: str) -> list[str]:
        """指令中繼一律開門鈴（A-015）；這個資料庫聽的埠不一樣就一起講。"""
        port = self.bell_ports.get(dsn_env)
        return ["--bell"] if port is None else ["--bell", "--bell-port", str(port)]

    def start_remote(self, config: Path, dsn_env: str, folder: Path) -> list[str]:
        """開後台自己的中繼程式（A-017）：把網站來的啟動／結束寫成 folder 裡的指令檔。只開一支。"""
        if self.remote_proc is not None and self.remote_proc.poll() is None:
            raise JobError("遙控中繼已經在跑")
        if dsn_env not in DSN_ENVS:
            raise JobError(f"遙控中繼的資料庫只能選 {DSN_ENVS}：{dsn_env!r}")
        folder.mkdir(parents=True, exist_ok=True)
        command = [self.python, "-m", "autotrade", "relay", "--config", str(config)]
        command += ["--dir", str(folder), "--consumer", "admin", "--dsn-env", dsn_env]
        command += self._bell(dsn_env)
        if self.env_file is not None:
            command += ["--env-file", str(self.env_file)]
        self.remote_log = (folder / "relay.log").open("a", encoding="utf-8")
        self.remote_proc = self.spawn(command, self.remote_log, self.workdir)
        self.remote_command = command
        return command

    def stop_remote(self) -> None:
        """收掉後台自己的中繼程式。"""
        if self.remote_proc is not None:
            _end([self.remote_proc], 5.0)
            self.remote_proc = None
        if self.remote_log is not None:
            self.remote_log.close()
            self.remote_log = None

    def stop_drill(self) -> bool:
        """停掉現在的演練；沒有在跑的回 False。"""
        if self.drill is None:
            return False
        was = self.drill.running()
        self.drill.stopped = self.drill.stopped or was
        self.drill.stop()
        return was

    def _busy(self) -> str | None:
        if self.run is not None and self.run.running():
            return "交易程式已經在跑；先按「結束」"
        if self.drill is not None and self.drill.running():
            return "演練正在跑；先停掉演練"
        return None

    def start_run(self, req: RunRequest, now: datetime | None = None) -> RunJob:
        """啟動交易程式。一次只准一個，而且不能跟演練同時（兩邊都是「模擬」這個名字在回報）。"""
        busy = self._busy()
        if busy is not None:
            raise JobError(busy)
        if self.run is not None:
            self.run.stop_all()
        if not req.config.is_file():
            raise JobError(f"找不到設定檔：{req.config.name}")
        now = now or datetime.now(TAIPEI)
        base = [self.python, "-m", "autotrade"]
        env = [] if self.env_file is None else ["--env-file", str(self.env_file)]
        if req.source == "live":
            if req.dsn_env not in DSN_ENVS:
                raise JobError(f"指令中繼的資料庫只能選 {DSN_ENVS}：{req.dsn_env!r}")
            folder = self.data_dir / "run" / now.date().isoformat()  # 同一天重開共用同一個資料夾
            folder.mkdir(parents=True, exist_ok=True)
            ticks_file, status = folder / TICKS_NAME, folder / STATUS_NAME
            config = str(req.config)
            plan = {
                "ticks": [*base, "ticks", "--config", config, "--out", str(ticks_file)],
                "relay": [*base, "relay", "--config", config, "--dir", str(folder)],
                "run": [*base, "run", "--config", config, "--dir", str(folder)],
            }
            plan["ticks"] += ["--dsn-env", TICKS_DSN_ENV, *env]
            plan["relay"] += ["--consumer", "sim", "--dsn-env", req.dsn_env]
            plan["relay"] += ["--status", str(status), *env, *self._bell(req.dsn_env)]
            plan["run"] += ["--strategy", req.strategy, "--ticks-file", str(ticks_file)]
            plan["run"] += ["--status", str(status)]
            if req.notify:
                plan["run"] += ["--notify", "send", *env]
                plan["run"] += ["--notify-log", str(folder / "notify_failed.log")]
            if req.release_stop:
                plan["run"].append("--release-stop")
        elif req.source == "play":
            if req.play_file is None or req.play_day is None or not req.play_file.is_file():
                raise JobError("回放要選一天富邦錄好的逐筆")
            if not 0 < req.speed <= PLAY_SPEED_MAX:
                raise JobError(f"倍速要在 0～{PLAY_SPEED_MAX:g}")
            folder = self.data_dir / f"play_{now:%Y%m%d_%H%M%S}"
            again = 1
            while folder.exists():
                again += 1
                folder = self.data_dir / f"play_{now:%Y%m%d_%H%M%S}_{again}"
            folder.mkdir(parents=True, exist_ok=False)
            plan = {"run": [*base, "run", "--config", str(req.config), "--dir", str(folder)]}
            plan["run"] += ["--strategy", req.strategy, "--play", str(req.play_file)]
            plan["run"] += ["--day", req.play_day.isoformat(), "--from", req.start]
            plan["run"] += ["--speed", f"{req.speed:g}", "--status", str(folder / STATUS_NAME)]
        else:
            raise JobError(f"行情來源只能是 live 或 play：{req.source!r}")
        if req.ms_repo is not None:
            plan["run"] += ["--ms-repo", str(req.ms_repo)]
        (folder / QUIT_NAME).unlink(missing_ok=True)  # 上一次的「結束」不算
        logs: dict[str, IO[str]] = {}
        procs: dict[str, Proc] = {}
        for name, args in plan.items():  # 順序：行情中繼、指令中繼、交易程式
            # 同一天重開時接在原本的紀錄後面，早上的紀錄不會不見
            logs[name] = (folder / f"{name}.log").open("a", encoding="utf-8")
            procs[name] = self.spawn(args, logs[name], self.workdir)
        self.run = RunJob(folder, now, req, procs, logs, plan)
        return self.run

    def stop_run(self, quit_wait_s: float = 15.0) -> bool:
        """請交易程式結束（先好好講，等不到才強制），兩支中繼程式一起收掉；沒有在跑的回 False。"""
        job = self.run
        if job is None:
            return False
        was = job.running()
        if was:
            job.stopped = True
            job.ask_quit()
            with contextlib.suppress(subprocess.TimeoutExpired):  # 等不到：下面 stop_all 會強制結束
                job.procs["run"].wait(timeout=quit_wait_s)
        job.stop_all()
        return was

    def close(self) -> None:
        """後台關掉時把演練與交易程式一起停掉。"""
        self.stop_drill()
        self.stop_run()
        self.stop_remote()
