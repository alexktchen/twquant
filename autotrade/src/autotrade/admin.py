"""本機後台（A-012／D-226）：在跑交易程式的電腦上，用瀏覽器改設定、開演練。

只聽 127.0.0.1：別台電腦連不到。網站（雲端）仍然只能叫停，改設定與開程式只在這裡。
另外兩道防護，擋「別的網頁偷偷叫你的瀏覽器打這個後台」：
- Host 一定要是 127.0.0.1／localhost 加這個埠（擋 DNS rebinding）；
- 會改東西的請求一定要帶這次啟動才產生的通行碼（藏在頁面裡，別的網站拿不到），而且只收 JSON。
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import threading
from collections import deque
from collections.abc import Callable
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from autotrade.admin_page import PAGE
from autotrade.config import ConfigError
from autotrade.control import FileControl
from autotrade.jobs import DSN_ENVS, DrillRequest, JobError, Jobs, RunRequest, tail
from autotrade.model import TAIPEI
from autotrade.remote import REMOTE_DIR, Remote
from autotrade.settings import (
    FIELDS,
    SettingsError,
    apply_changes,
    read_values,
    shown_value,
    validate,
)

TICKS_NAME = re.compile(r"^Daily_(\d{4})_(\d{2})_(\d{2})\.csv$")  # 期交所盤後逐筆檔
# 富邦即時錄的匯出檔（A-013）
RECORDED_NAME = re.compile(r"^[A-Za-z0-9]+_(\d{4})-(\d{2})-(\d{2})\.csv$")
BODY_MAX = 20_000
TOKEN_HEADER = "X-Admin-Token"
CONSOLE_LINES = 500
SOURCES = {"recorded": "富邦即時錄的", "taifex": "期交所盤後檔"}
RUN_STRATEGY = "momentum"  # 第一個上線目標：動能順勢（SPEC §2）
DB_TEXT = {"TWQUANT_SUPABASE_DSN": "雲端", "TWQUANT_PG_DSN": "本機"}
PROC_TEXT = {"ticks": "行情中繼", "relay": "指令中繼", "run": "交易程式"}
HELPER_LOSS = {"ticks": "收不到新的行情", "relay": "聽不到遠端緊急停止、網站也不會更新"}


class Console:
    """後台在終端機印的每一行，同時留在記憶體裡給頁面的「終端機」分頁看（最近 CONSOLE_LINES 行）。"""

    def __init__(self, lines: int = CONSOLE_LINES, echo: bool = True) -> None:
        self._lines: deque[dict[str, str]] = deque(maxlen=lines)
        self._lock = threading.Lock()
        self.echo = echo

    def say(self, text: str, level: str = "info") -> None:
        """印一行（前面加時間）並記下來。level：info／warn。"""
        now = datetime.now(TAIPEI).strftime("%H:%M:%S")
        with self._lock:
            self._lines.append({"t": now, "level": level, "text": text})
            if self.echo:
                print(f"{now} {text}", flush=True)

    def lines(self) -> list[dict[str, str]]:
        """目前留著的每一行（舊到新）。"""
        with self._lock:
            return list(self._lines)


def process_info() -> dict[str, Any]:
    """這個後台跑在哪裡：哪一支程式、哪個 Python、哪一行指令、哪個資料夾、程序編號。"""
    script = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    return {
        "pid": os.getpid(),
        "script": str(script.resolve()) if script is not None else "",
        "python": sys.executable,
        "command": " ".join(sys.argv),
        "cwd": str(Path.cwd()),
        "started_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
    }


def tick_files(folder: Path | None, recorded: Path | None = None) -> list[dict[str, str]]:
    """演練能拿的逐筆檔：富邦即時錄的排前面，再來是期交所盤後檔；各自新到舊。"""
    out = []
    for feed, where, pattern in (
        ("recorded", recorded, RECORDED_NAME),
        ("taifex", folder, TICKS_NAME),
    ):
        if where is None or not where.is_dir():
            continue
        for path in where.iterdir():
            match = pattern.match(path.name)
            if match:
                day = f"{match[1]}-{match[2]}-{match[3]}"
                out.append({"name": path.name, "day": day, "feed": feed})
    return sorted(out, key=lambda r: (r["feed"] == "recorded", r["day"]), reverse=True)


class Admin:
    """後台要用到的東西：設定檔所在的資料夾、逐筆檔的資料夾、背景程式。"""

    def __init__(
        self,
        config_dir: Path,
        ticks_dir: Path | None,
        jobs: Jobs,
        console: Console | None = None,
        recorded_dir: Path | None = None,
        ms_repo: Path | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(TAIPEI),
        helper_linger_s: float = 8.0,
    ) -> None:
        self.config_dir = config_dir
        self.ticks_dir = ticks_dir
        self.recorded_dir = recorded_dir
        self.ms_repo = ms_repo
        self.clock = clock
        self.helper_linger_s = helper_linger_s  # 交易程式結束後，兩支中繼程式再留幾秒
        self._lock = threading.RLock()  # 網頁的請求與背景的 watch 都會動到 jobs
        self.jobs = jobs
        self.console = console or Console()
        self.process = process_info()
        self.token = secrets.token_urlsafe(24)
        self._ended: object | None = None  # 已經報過「結束」的那場演練
        self.remote: Remote | None = None  # 網站來的啟動／結束（A-017）；沒開就是 None
        self._remote_lost = False  # 已經講過「遙控中繼自己結束了」

    def watch_drill(self) -> None:
        """演練程式自己結束了，就在終端機報一次（每場只報一次）。"""
        drill = self.jobs.drill
        if drill is None or drill is self._ended or drill.running():
            return
        self._ended = drill
        code = drill.procs["drill"].poll()
        drill.stop()  # 演練程式沒了，中繼程式也不用留著
        if code == 0:
            self.console.say(f"演練結束（{drill.request.day}，正常結束）")
        else:
            last = (tail(drill.folder / "drill.log", 1) or ["沒有輸出"])[-1]
            self.console.say(f"演練沒跑完（結束碼 {code}）：{last}", "warn")

    def configs(self) -> list[Path]:
        """可以改的設定檔（資料夾裡的 .toml）。"""
        return sorted(p for p in self.config_dir.glob("*.toml") if p.is_file())

    def config_path(self, name: str) -> Path:
        """設定檔名 → 路徑；只認資料夾裡真的有的那幾個（擋 ../ 這類路徑）。"""
        for path in self.configs():
            if path.name == name:
                return path
        raise SettingsError(f"沒有這個設定檔：{name!r}")

    def state(self) -> dict[str, Any]:
        """後台頁面要的全部現況。"""
        configs = []
        for path in self.configs():
            text = path.read_text(encoding="utf-8")
            entry: dict[str, Any] = {"name": path.name}
            try:
                cfg = validate(text)
                values = read_values(text)
                entry |= {
                    "mode": cfg.mode,
                    "ok": True,
                    "error": None,
                    "values": {f"{f.section}.{f.key}": shown_value(values, f) for f in FIELDS},
                    "has": [f"{f.section}.{f.key}" for f in FIELDS if f.section in values],
                    "control": cfg.control is not None,
                    "export": cfg.export is not None,
                    "notify": cfg.notify is not None,
                }
            except SettingsError as exc:
                entry |= {
                    "mode": None,
                    "ok": False,
                    "error": str(exc),
                    "values": {},
                    "has": [],
                    "control": False,
                    "export": False,
                    "notify": False,
                }
            configs.append(entry)
        self.watch()
        drill, run = self.jobs.drill, self.jobs.run
        return {
            "fields": [
                {
                    "id": f"{f.section}.{f.key}",
                    "section": f.section,
                    "label": f.label,
                    "kind": f.kind,
                    "help": f.help,
                    "optional": f.optional,
                }
                for f in FIELDS
            ],
            "configs": configs,
            "ticks": tick_files(self.ticks_dir, self.recorded_dir),
            "dsn_envs": list(DSN_ENVS),
            "env_file": self.jobs.env_file is not None,
            "drill": None if drill is None else drill.state(),
            "run": None if run is None else run.state(),
            "run_ready": {"ms_repo": self.ms_repo is not None},
            "now": datetime.now(TAIPEI).isoformat(timespec="seconds"),
            "console": self.console.lines(),
            "process": self.process,
        }

    def watch_run(self) -> None:
        """交易程式自己結束了（收盤、出錯）：報一次，過一下子再把兩支中繼程式收掉。

        不馬上收：指令中繼還要把最後一份狀態送上網站。
        """
        job = self.jobs.run
        if job is None:
            return
        if job.running():
            # 交易程式還在跑，中繼程式卻自己結束了：一定要講，不然只會看到行情停住或網站沒更新
            for name, proc in job.procs.items():
                code = proc.poll()
                if name != "run" and code is not None and name not in job.warned:
                    job.warned.add(name)
                    last = (tail(job.folder / f"{name}.log", 1) or ["沒有輸出"])[-1]
                    effect = HELPER_LOSS[name]
                    self.console.say(
                        f"{PROC_TEXT[name]}自己結束了（結束碼 {code}）：{last}。交易程式還在跑，但{effect}；"
                        "請按「結束」再重新啟動",
                        "warn",
                    )
            return
        now = self.clock()
        if job.ended_at is None:
            job.ended_at = now
            if not job.stopped:
                code = job.procs["run"].poll()
                last = (tail(job.folder / "run.log", 1) or ["沒有輸出"])[-1]
                if code == 0:
                    self.console.say(f"交易程式結束了：{last}")
                else:
                    self.console.say(f"交易程式沒跑完（結束碼 {code}）：{last}", "warn")
        if not job.helpers_stopped and (now - job.ended_at).total_seconds() >= self.helper_linger_s:
            job.stop_all()
            if len(job.procs) > 1:
                self.console.say("行情中繼、指令中繼也收掉了")

    def enable_remote(self, config_name: str, dsn_env: str) -> None:
        """讓網站可以啟動／結束交易程式（A-017）：開後台自己的中繼程式，之後 watch 會去讀它寫的指令檔。

        遠端只能啟動 config_name 這一份設定。連線字串一樣不經過後台。
        """
        config = self.config_path(config_name)
        cfg = validate(config.read_text(encoding="utf-8"))
        if cfg.control is None:
            raise ConfigError(f"{config.name} 沒有 [control]，不能拿來遙控")
        folder = self.jobs.data_dir / REMOTE_DIR
        with self._lock:
            command = self.jobs.start_remote(config, dsn_env, folder)
        self.remote = Remote(
            source=FileControl(folder, cfg.control.stale_s),
            config=config,
            mode=cfg.mode,
            dsn_env=dsn_env,
            notify=cfg.notify is not None,
            start=self.start_run,
            stop=self.stop_run,
            say=self.console.say,
            refuse=(SettingsError, JobError),
            started_at=self.clock(),
        )
        self.console.say(
            f"遙控開了：網站可以啟動／結束交易程式（只能啟動 {config.name}、"
            f"連{DB_TEXT.get(dsn_env, dsn_env)}資料庫）"
        )
        pid = getattr(self.jobs.remote_proc, "pid", None)
        self.console.say(f"\u3000遙控中繼（程序編號 {pid}）：{' '.join(command)}")

    def watch_remote(self) -> None:
        """遙控中繼自己結束了就講一次；它在的時候去讀一次遙控指令。"""
        if self.remote is None:
            return
        proc = self.jobs.remote_proc
        if proc is not None and proc.poll() is not None and not self._remote_lost:
            self._remote_lost = True
            last = (tail(self.jobs.data_dir / REMOTE_DIR / "relay.log", 1) or ["沒有輸出"])[-1]
            self.console.say(
                f"遙控中繼自己結束了（結束碼 {proc.poll()}）：從網站啟動／結束到不了。最後一行：{last}",
                "warn",
            )
        self.remote.step(self.clock())

    def watch(self) -> None:
        """定時叫：看演練與交易程式是不是自己結束了；有開遙控就順便讀一次網站來的指令。"""
        with self._lock:
            self.watch_drill()
            self.watch_run()
        # 不握著鎖做：遠端的「結束」要等交易程式收工好幾秒，這段時間頁面的請求不該被卡住
        self.watch_remote()

    def start_watcher(self, every_s: float = 1.0) -> threading.Event:
        """開一條背景執行緒定時叫 watch（頁面沒開著的時候也要有人收尾）。回傳的事件設起來就停。"""
        stop = threading.Event()

        def loop() -> None:
            while not stop.wait(every_s):
                self.watch()

        threading.Thread(target=loop, name="admin-watch", daemon=True).start()
        return stop

    def start_run(self, body: dict[str, Any]) -> dict[str, Any]:
        """啟動交易程式（現在只有模擬）。"""
        config = self.config_path(str(body.get("config", "")))
        cfg = validate(config.read_text(encoding="utf-8"))
        if cfg.mode != "sim":
            raise JobError("這個設定檔是真單；真單還沒接上元大，現在只能跑模擬")
        if cfg.export is None:
            raise JobError("這個設定檔沒有 [export]，寫不出給網站看的狀態")
        if self.ms_repo is None:
            raise JobError("後台啟動時沒有給動能順勢原版程式的資料夾（--ms-repo）")
        source = str(body.get("source", ""))
        if source == "live":
            if cfg.control is None:
                raise JobError("這個設定檔沒有 [control]（遠端緊急停止），不能跑交易程式")
            notify = bool(body.get("notify"))
            if notify and cfg.notify is None:
                raise JobError("這個設定檔沒有 [notify]，不能發手機通知")
            dsn = str(body.get("dsn_env", ""))
            req = RunRequest(
                config=config,
                strategy=RUN_STRATEGY,
                ms_repo=self.ms_repo,
                source=source,
                dsn_env=dsn,
                notify=notify,
                release_stop=bool(body.get("release_stop")),
            )
            what = (
                f"今天的即時行情、指令中繼連{DB_TEXT.get(dsn, dsn)}資料庫、"
                f"{'發' if notify else '不發'}手機通知"
                + ("、解除今天稍早的遠端停止" if req.release_stop else "")
            )
        elif source == "play":
            name = str(body.get("ticks", ""))
            match = RECORDED_NAME.match(name)
            if match is None or self.recorded_dir is None:
                raise JobError(f"回放要選一天富邦錄好的逐筆：{name!r}")
            start = str(body.get("start", "08:44:50"))
            if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", start):
                raise JobError(f"開始時間要是 HH:MM:SS：{start!r}")
            try:
                speed = float(body.get("speed", 1))
            except (TypeError, ValueError) as exc:
                raise JobError("倍速要是數字") from exc
            day = date(int(match[1]), int(match[2]), int(match[3]))
            req = RunRequest(
                config=config,
                strategy=RUN_STRATEGY,
                ms_repo=self.ms_repo,
                source=source,
                play_file=self.recorded_dir / name,
                play_day=day,
                start=start,
                speed=speed,
            )
            what = f"回放 {day} {start} 起、{speed:g} 倍速（只在這台電腦看，不上網站、不發通知）"
        else:
            raise JobError(f"行情來源要選「今天的即時行情」或「回放」：{source!r}")
        with self._lock:
            job = self.jobs.start_run(req, self.clock())
        self.console.say(f"啟動交易程式（模擬）：{config.name}、{what}")
        for name, args in job.commands.items():
            pid = getattr(job.procs[name], "pid", None)
            self.console.say(f"\u3000{PROC_TEXT[name]}（程序編號 {pid}）：{' '.join(args)}")
        self.console.say(f"\u3000輸出與紀錄在 {job.folder}")
        return {"ok": True, "run": job.state()}

    def stop_run(self) -> dict[str, Any]:
        """請交易程式結束。"""
        with self._lock:
            was = self.jobs.stop_run()
            job = self.jobs.run
            if was and job is not None:
                job.ended_at = job.ended_at or self.clock()
        self.console.say("交易程式已結束" if was else "按了結束，但交易程式沒有在跑")
        return {"ok": True, "was_running": was}

    def save(self, body: dict[str, Any]) -> dict[str, Any]:
        """存設定。"""
        changes = body.get("changes")
        if not isinstance(changes, dict) or not all(isinstance(k, str) for k in changes):
            raise SettingsError('要帶 changes：{"區塊.鍵": "值"}')
        for value in changes.values():
            if value is not None and not isinstance(value, str | int | float | bool):
                raise SettingsError("值要是文字或數字")
        path = self.config_path(str(body.get("config", "")))
        done = apply_changes(
            path,
            {
                k: None if v is None else str(v).lower() if isinstance(v, bool) else str(v)
                for k, v in changes.items()
            },
        )
        shown = "、".join(done) if done else "沒有變動"
        self.console.say(f"存設定 {path.name}：{shown}")
        return {"ok": True, "changed": done}

    def start_drill(self, body: dict[str, Any]) -> dict[str, Any]:
        """開一場演練。"""
        name = str(body.get("ticks", ""))
        folder, feed, match = self.recorded_dir, "recorded", RECORDED_NAME.match(name)
        if match is None or folder is None:
            folder, feed, match = self.ticks_dir, "taifex", TICKS_NAME.match(name)
        if match is None or folder is None:
            raise JobError(f"逐筆檔名不對：{name!r}")
        start = str(body.get("start", "09:00:00"))
        if not re.fullmatch(r"\d{2}:\d{2}:\d{2}", start):
            raise JobError(f"開始時間要是 HH:MM:SS：{start!r}")
        dsn = body.get("dsn_env")
        try:
            minutes, speed = float(body.get("minutes", 10)), float(body.get("speed", 1))
        except (TypeError, ValueError) as exc:
            raise JobError("分鐘與倍速要是數字") from exc
        req = DrillRequest(
            config=self.config_path(str(body.get("config", ""))),
            ticks=folder / name,
            day=date(int(match[1]), int(match[2]), int(match[3])),
            start=start,
            minutes=minutes,
            speed=speed,
            dsn_env=None if dsn in (None, "") else str(dsn),
            feed=feed,
        )
        if validate(req.config.read_text(encoding="utf-8")).control is None:
            raise JobError("這個設定檔沒有 [control]（遠端緊急停止），不能演練")
        with self._lock:
            job = self.jobs.start_drill(req)
            self._ended = None
        relay = f"，遠端叫停經 {req.dsn_env}" if req.dsn_env else "，不接遠端"
        self.console.say(
            f"開始演練：{req.config.name}、{SOURCES[feed]} {req.day} {req.start} 起 "
            f"{minutes:g} 分鐘、{speed:g} 倍速{relay}"
        )
        for name, title in (("relay", "中繼程式"), ("drill", "演練程式")):
            if name in job.commands:
                pid = getattr(job.procs[name], "pid", None)
                self.console.say(f"　{title}（程序編號 {pid}）：{' '.join(job.commands[name])}")
        self.console.say(f"　兩個程式的輸出在 {job.folder}")
        return {"ok": True, "drill": job.state()}

    def stop_drill(self) -> dict[str, Any]:
        """停掉演練。"""
        with self._lock:
            was = self.jobs.stop_drill()
            if was:
                self._ended = self.jobs.drill
        self.console.say("停掉演練" if was else "按了停止，但沒有演練在跑")
        return {"ok": True, "was_running": was}


def make_handler(admin: Admin, port: int) -> type[BaseHTTPRequestHandler]:
    """產生 HTTP 處理器（綁定這個後台與埠）。"""
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{h}" for h in hosts}

    class Handler(BaseHTTPRequestHandler):
        server_version = "autotrade-admin"

        def log_message(self, format: str, *args: Any) -> None:
            """不把每個請求印在畫面上。"""

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            self._send(
                status,
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in hosts

        def do_GET(self) -> None:
            """頁面與現況。"""
            if not self._host_ok():
                return self._json(HTTPStatus.FORBIDDEN, {"detail": "只接受本機（127.0.0.1）的連線"})
            if self.path == "/":
                page = PAGE.replace("__TOKEN__", admin.token).encode("utf-8")
                return self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
            if self.path == "/api/state":
                return self._json(HTTPStatus.OK, admin.state())
            return self._json(HTTPStatus.NOT_FOUND, {"detail": "沒有這一頁"})

        def do_POST(self) -> None:
            """會改東西的請求：存設定、開演練、停演練。"""
            if not self._host_ok():
                return self._json(HTTPStatus.FORBIDDEN, {"detail": "只接受本機（127.0.0.1）的連線"})
            origin = self.headers.get("Origin")
            if origin is not None and origin not in origins:
                return self._json(HTTPStatus.FORBIDDEN, {"detail": "只接受這個後台自己的頁面"})
            if not secrets.compare_digest(self.headers.get(TOKEN_HEADER, ""), admin.token):
                return self._json(
                    HTTPStatus.FORBIDDEN, {"detail": "通行碼不對：請重新整理後台頁面"}
                )
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"detail": "只收 JSON"})
            length = int(self.headers.get("Content-Length") or 0)
            if length > BODY_MAX:
                return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"detail": "內容太大"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._json(HTTPStatus.BAD_REQUEST, {"detail": "內容不是 JSON"})
            if not isinstance(body, dict):
                return self._json(HTTPStatus.BAD_REQUEST, {"detail": "內容要是一個物件"})
            routes = {
                "/api/settings": lambda: admin.save(body),
                "/api/drill/start": lambda: admin.start_drill(body),
                "/api/drill/stop": admin.stop_drill,
                "/api/run/start": lambda: admin.start_run(body),
                "/api/run/stop": admin.stop_run,
            }
            route = routes.get(self.path)
            if route is None:
                return self._json(HTTPStatus.NOT_FOUND, {"detail": "沒有這個動作"})
            try:
                return self._json(HTTPStatus.OK, route())
            except (SettingsError, JobError) as exc:
                admin.console.say(f"被擋下（{self.path}）：{exc}", "warn")
                return self._json(HTTPStatus.BAD_REQUEST, {"detail": str(exc)})

    return Handler


def serve(admin: Admin, port: int) -> ThreadingHTTPServer:
    """在 127.0.0.1 開後台（還沒開始處理請求；呼叫端 serve_forever）。port 0＝隨便挑一個空的。"""
    server = ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
    real = int(server.server_address[1])
    server.RequestHandlerClass = make_handler(admin, real)
    return server
