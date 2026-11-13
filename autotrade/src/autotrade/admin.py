"""本機後台（A-012／D-226）：在跑交易程式的電腦上，用瀏覽器改設定、開演練。

只聽 127.0.0.1：別台電腦連不到。網站（雲端）仍然只能叫停，改設定與開程式只在這裡。
另外兩道防護，擋「別的網頁偷偷叫你的瀏覽器打這個後台」：
- Host 一定要是 127.0.0.1／localhost 加這個埠（擋 DNS rebinding）；
- 會改東西的請求一定要帶這次啟動才產生的通行碼（藏在頁面裡，別的網站拿不到），而且只收 JSON。
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from autotrade.admin_page import PAGE
from autotrade.jobs import DSN_ENVS, DrillRequest, JobError, Jobs
from autotrade.model import TAIPEI
from autotrade.settings import (
    FIELDS,
    SettingsError,
    apply_changes,
    read_values,
    shown_value,
    validate,
)

TICKS_NAME = re.compile(r"^Daily_(\d{4})_(\d{2})_(\d{2})\.csv$")
BODY_MAX = 20_000
TOKEN_HEADER = "X-Admin-Token"


def tick_files(folder: Path | None) -> list[dict[str, str]]:
    """逐筆檔清單（新到舊）：檔名與那一天。"""
    if folder is None or not folder.is_dir():
        return []
    out = []
    for path in folder.iterdir():
        match = TICKS_NAME.match(path.name)
        if match:
            out.append({"name": path.name, "day": f"{match[1]}-{match[2]}-{match[3]}"})
    return sorted(out, key=lambda r: r["day"], reverse=True)


class Admin:
    """後台要用到的東西：設定檔所在的資料夾、逐筆檔的資料夾、背景程式。"""

    def __init__(self, config_dir: Path, ticks_dir: Path | None, jobs: Jobs) -> None:
        self.config_dir = config_dir
        self.ticks_dir = ticks_dir
        self.jobs = jobs
        self.token = secrets.token_urlsafe(24)

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
                }
            except SettingsError as exc:
                entry |= {
                    "mode": None,
                    "ok": False,
                    "error": str(exc),
                    "values": {},
                    "has": [],
                    "control": False,
                }
            configs.append(entry)
        drill = self.jobs.drill
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
            "ticks": tick_files(self.ticks_dir),
            "dsn_envs": list(DSN_ENVS),
            "env_file": self.jobs.env_file is not None,
            "drill": None if drill is None else drill.state(),
            "now": datetime.now(TAIPEI).isoformat(timespec="seconds"),
        }

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
        return {"ok": True, "changed": done}

    def start_drill(self, body: dict[str, Any]) -> dict[str, Any]:
        """開一場演練。"""
        if self.ticks_dir is None:
            raise JobError("後台啟動時沒有給逐筆檔的資料夾（--ticks-dir）")
        name = str(body.get("ticks", ""))
        match = TICKS_NAME.match(name)
        if match is None:
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
            ticks=self.ticks_dir / name,
            day=date(int(match[1]), int(match[2]), int(match[3])),
            start=start,
            minutes=minutes,
            speed=speed,
            dsn_env=None if dsn in (None, "") else str(dsn),
        )
        if validate(req.config.read_text(encoding="utf-8")).control is None:
            raise JobError("這個設定檔沒有 [control]（遠端緊急停止），不能演練")
        job = self.jobs.start_drill(req)
        return {"ok": True, "drill": job.state()}

    def stop_drill(self) -> dict[str, Any]:
        """停掉演練。"""
        return {"ok": True, "was_running": self.jobs.stop_drill()}


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
            }
            route = routes.get(self.path)
            if route is None:
                return self._json(HTTPStatus.NOT_FOUND, {"detail": "沒有這個動作"})
            try:
                return self._json(HTTPStatus.OK, route())
            except (SettingsError, JobError) as exc:
                return self._json(HTTPStatus.BAD_REQUEST, {"detail": str(exc)})

    return Handler


def serve(admin: Admin, port: int) -> ThreadingHTTPServer:
    """在 127.0.0.1 開後台（還沒開始處理請求；呼叫端 serve_forever）。port 0＝隨便挑一個空的。"""
    server = ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
    real = int(server.server_address[1])
    server.RequestHandlerClass = make_handler(admin, real)
    return server
