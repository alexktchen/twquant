"""A-012：本機後台（D-226）。用瀏覽器改設定、開演練；只聽 127.0.0.1。"""

from __future__ import annotations

import http.client
import json
import shutil
import socket
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import IO, Any

import pytest

from autotrade.admin import TOKEN_HEADER, Admin, serve, tick_files
from autotrade.jobs import DrillRequest, JobError, Jobs
from autotrade.model import TAIPEI
from autotrade.settings import (
    FIELD_BY_KEY,
    FIELDS,
    SettingsError,
    apply_changes,
    read_values,
    set_value,
    shown_value,
    to_literal,
)

CONFIG_PATH = Path(__file__).parents[1] / "configs" / "tmf_sim.toml"
NOW = datetime(2026, 11, 14, 22, 30, tzinfo=TAIPEI)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """蓋掉 conftest 的同名設定：這一份只放行連到本機（127.0.0.1）——後台的測試要連到測試自己開的後台。
    其他一律照樣擋：測試仍然不得連網。"""
    real = socket.socket.connect

    def local_only(self: socket.socket, address: Any) -> None:
        if isinstance(address, tuple) and address[0] == "127.0.0.1":
            real(self, address)
            return
        self.close()
        raise AssertionError(f"測試不得連網：有程式想連到 {address!r}")

    monkeypatch.setattr(socket.socket, "connect", local_only)


def config_copy(tmp_path: Path, name: str = "tmf_sim.toml") -> Path:
    folder = tmp_path / "configs"
    folder.mkdir(exist_ok=True)
    path = folder / name
    shutil.copy(CONFIG_PATH, path)
    return path


# ---- AC1 改設定：只改值，註解與排版不動


def test_ac1_set_value_keeps_comments_and_layout() -> None:
    text = '[risk]\nmax_position = 1         # 最多 1 口\nentry_end = "13:00:00"   # 註解\n\n[sim]\nlatency_s = 0.5\n'
    out = set_value(text, "risk", "max_position", "2")
    assert out == text.replace("max_position = 1 ", "max_position = 2 ")
    out = set_value(text, "risk", "entry_end", '"12:30:00"')
    assert 'entry_end = "12:30:00"   # 註解' in out
    # 區塊裡沒有這個鍵：加在區塊最後（空行之前）
    out = set_value(text, "risk", "daily_loss_limit_twd", "3000.0")
    assert out.split("\n")[3] == "daily_loss_limit_twd = 3000.0" and out.split("\n")[4] == ""
    # 留空＝不設：那一行被註解掉，看得出是從後台改的
    out = set_value(text, "risk", "max_position", None)
    assert "# max_position = 1         # 最多 1 口（從後台改成不設）" in out
    assert set_value(text, "risk", "nothing_here", None) == text
    with pytest.raises(SettingsError, match=r"\[notify\]"):
        set_value(text, "notify", "x", "1")


def test_ac1_literals_are_checked_by_kind() -> None:
    f = FIELD_BY_KEY
    assert to_literal(f[("risk", "max_position")], " 2 ") == "2"
    assert to_literal(f[("risk", "order_timeout_s")], "10") == "10.0"
    assert to_literal(f[("risk", "entry_end")], "12:30:00") == '"12:30:00"'
    assert to_literal(f[("notify", "quiet_routine")], "false") == "false"
    assert to_literal(f[("risk", "daily_loss_limit_twd")], "") is None  # 可以不設
    for key, bad in (
        (("risk", "max_position"), "1.5"),
        (("risk", "max_position"), "一口"),
        (("risk", "order_timeout_s"), "nan"),
        (("risk", "order_timeout_s"), "inf"),
        (("risk", "entry_end"), "12:30"),
        (("risk", "entry_end"), "25:00:00"),
        (("notify", "quiet_routine"), "yes"),
        (("risk", "max_position"), ""),  # 不能不設
    ):
        with pytest.raises(SettingsError):
            to_literal(f[key], bad)
    assert ("", "mode") not in FIELD_BY_KEY  # 模擬／真單不能從後台改
    assert all(field.section != "" for field in FIELDS)


def test_ac1_apply_changes_validates_saves_and_keeps_a_backup(tmp_path: Path) -> None:
    path = config_copy(tmp_path)
    original = path.read_text(encoding="utf-8")
    done = apply_changes(
        path,
        {
            "risk.max_entries_per_day": "4",
            "risk.daily_loss_limit_twd": "3000",
            "sim.latency_s": "0.5",
        },
    )
    assert done == ["一天最多進場幾次：6 → 4", "每日虧損上限（元）：不設 → 3000"]  # 沒變的不算
    text = path.read_text(encoding="utf-8")
    values = read_values(text)
    assert (
        values["risk"]["max_entries_per_day"] == 4
        and values["risk"]["daily_loss_limit_twd"] == 3000.0
    )
    assert "# 系統的保險；動能順勢原版自己一天最多進場 2 次" in text  # 註解還在
    assert path.with_name(path.name + ".bak").read_text(encoding="utf-8") == original
    assert apply_changes(path, {"risk.max_entries_per_day": "4"}) == []  # 沒改就不寫


def test_ac1_invalid_settings_are_not_saved(tmp_path: Path) -> None:
    path = config_copy(tmp_path)
    original = path.read_text(encoding="utf-8")
    # 單一欄位格式都對，但組合起來不合格（跟交易程式同一套規則）
    with pytest.raises(SettingsError, match="強制平倉時間要在進場終點與收盤之間"):
        apply_changes(path, {"risk.force_flat_time": "12:00:00"})
    with pytest.raises(SettingsError, match=r"momentum\.contracts"):
        apply_changes(path, {"momentum.contracts": "3"})
    with pytest.raises(SettingsError, match="stale_s"):
        apply_changes(path, {"control.stale_s": "1"})
    with pytest.raises(SettingsError, match="不能從後台改"):
        apply_changes(path, {".mode": "live"})
    with pytest.raises(SettingsError, match="不能從後台改"):
        apply_changes(path, {"contract.symbol": "TX"})
    assert path.read_text(encoding="utf-8") == original  # 一字不動
    assert not path.with_name(path.name + ".bak").exists()


def test_ac1_every_field_round_trips_on_the_shipped_config(tmp_path: Path) -> None:
    path = config_copy(tmp_path)
    values = read_values(path.read_text(encoding="utf-8"))
    for f in FIELDS:
        shown = shown_value(values, f)
        if f.section in values and shown != "":
            assert to_literal(f, shown) is not None, f
    assert shown_value(values, FIELD_BY_KEY[("risk", "entry_end")]) == "13:00:00"
    assert shown_value(values, FIELD_BY_KEY[("notify", "quiet_routine")]) == "true"
    assert shown_value(values, FIELD_BY_KEY[("risk", "daily_loss_limit_twd")]) == ""


# ---- AC2 演練：開、看、停


@dataclass
class FakeProc:
    code: int | None = None
    terminated: bool = False

    def poll(self) -> int | None:
        return self.code

    def terminate(self) -> None:
        self.terminated = True
        self.code = -15

    def wait(self, timeout: float | None = None) -> int:
        return 0 if self.code is None else self.code

    def kill(self) -> None:
        self.code = -9


@dataclass
class FakeSpawn:
    calls: list[list[str]] = field(default_factory=list)
    procs: list[FakeProc] = field(default_factory=list)

    def __call__(self, args: Sequence[str], log: IO[str], cwd: Path) -> FakeProc:
        self.calls.append(list(args))
        log.write(f"{args[3]} 開始\n")
        log.flush()
        proc = FakeProc()
        self.procs.append(proc)
        return proc


def ticks_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "ticks"
    folder.mkdir(exist_ok=True)
    for name in ("Daily_2026_11_12.csv", "Daily_2026_11_11.csv", "notes.txt"):
        (folder / name).write_text("x", encoding="utf-8")
    return folder


def request(tmp_path: Path, **kw: object) -> DrillRequest:
    base: dict[str, Any] = {
        "config": config_copy(tmp_path),
        "ticks": ticks_dir(tmp_path) / "Daily_2026_11_12.csv",
        "day": date(2026, 11, 12),
        "start": "09:00:00",
        "minutes": 10.0,
        "speed": 1.0,
        "dsn_env": "TWQUANT_SUPABASE_DSN",
    }
    return DrillRequest(**{**base, **kw})


def test_ac2_start_runs_relay_and_drill_with_the_right_arguments(tmp_path: Path) -> None:
    spawn = FakeSpawn()
    jobs = Jobs(tmp_path / "data", env_file=Path("D:/x/.env"), spawn=spawn, python="py")
    (tmp_path / "data").mkdir()
    job = jobs.start_drill(request(tmp_path), now=NOW)
    relay, drill = spawn.calls
    assert relay[:4] == ["py", "-m", "autotrade", "relay"]
    assert relay[relay.index("--dsn-env") + 1] == "TWQUANT_SUPABASE_DSN"
    assert relay[relay.index("--consumer") + 1] == "sim"
    assert relay[relay.index("--env-file") + 1] == str(Path("D:/x/.env"))
    assert drill[:4] == ["py", "-m", "autotrade", "drill"]
    assert (
        drill[drill.index("--day") + 1] == "2026-11-12"
        and drill[drill.index("--from") + 1] == "09:00:00"
    )
    assert relay[relay.index("--dir") + 1] == drill[drill.index("--dir") + 1] == str(job.folder)
    assert job.folder.name == "drill_20261114_223000"
    state = job.state()
    assert state["running"] is True and state["logs"]["drill"] == ["drill 開始"]
    assert not any("postgresql" in " ".join(c) for c in spawn.calls)  # 連線字串不經過後台
    jobs.close()


def test_ac2_one_drill_at_a_time_and_stop_ends_both(tmp_path: Path) -> None:
    spawn = FakeSpawn()
    (tmp_path / "data").mkdir()
    jobs = Jobs(tmp_path / "data", spawn=spawn, python="py")
    jobs.start_drill(request(tmp_path), now=NOW)
    with pytest.raises(JobError, match="已經有一場演練在跑"):
        jobs.start_drill(request(tmp_path), now=datetime(2026, 11, 14, 22, 31, tzinfo=TAIPEI))
    assert jobs.stop_drill() is True and all(p.terminated for p in spawn.procs)
    assert jobs.stop_drill() is False
    # 停掉之後可以再開一場
    jobs.start_drill(request(tmp_path), now=datetime(2026, 11, 14, 22, 32, tzinfo=TAIPEI))
    assert len(spawn.calls) == 4
    jobs.close()


def test_ac2_bad_requests_are_refused(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    jobs = Jobs(tmp_path / "data", spawn=FakeSpawn(), python="py")
    for kw, why in (
        ({"dsn_env": "SOME_OTHER_SECRET"}, "資料庫只能選"),
        ({"minutes": 0.0}, "分鐘"),
        ({"speed": 100.0}, "倍速"),
        ({"ticks": tmp_path / "nope.csv"}, "找不到逐筆檔"),
    ):
        with pytest.raises(JobError, match=why):
            jobs.start_drill(request(tmp_path, **kw), now=NOW)
    job = jobs.start_drill(request(tmp_path, dsn_env=None), now=NOW)  # 不開中繼程式也可以
    assert list(job.procs) == ["drill"]
    jobs.close()


def test_ac2_tick_files_are_listed_newest_first(tmp_path: Path) -> None:
    assert tick_files(ticks_dir(tmp_path)) == [
        {"name": "Daily_2026_11_12.csv", "day": "2026-11-12"},
        {"name": "Daily_2026_11_11.csv", "day": "2026-11-11"},
    ]
    assert tick_files(None) == [] and tick_files(tmp_path / "nope") == []


# ---- AC3 只有本機、而且只有這個後台自己的頁面能改東西


@pytest.fixture
def running(tmp_path: Path) -> Iterator[tuple[Admin, int]]:
    config_copy(tmp_path)
    (tmp_path / "data").mkdir()
    admin = Admin(
        tmp_path / "configs",
        ticks_dir(tmp_path),
        Jobs(tmp_path / "data", spawn=FakeSpawn(), python="py"),
    )
    server = serve(admin, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield admin, int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        admin.jobs.close()


def call(
    port: int, method: str, path: str, body: object = None, headers: dict[str, str] | None = None
) -> tuple[int, Any, dict[str, str]]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    data = None if body is None else json.dumps(body).encode("utf-8")
    conn.request(method, path, body=data, headers={"Host": f"127.0.0.1:{port}", **(headers or {})})
    res = conn.getresponse()
    raw = res.read().decode("utf-8")
    head = {k.lower(): v for k, v in res.getheaders()}
    conn.close()
    return res.status, (json.loads(raw) if "json" in head.get("content-type", "") else raw), head


def write_headers(admin: Admin) -> dict[str, str]:
    return {TOKEN_HEADER: admin.token, "Content-Type": "application/json"}


def test_ac3_listens_only_on_localhost(running: tuple[Admin, int]) -> None:
    admin, port = running
    status, page, head = call(port, "GET", "/")
    assert status == 200 and admin.token in page and "__TOKEN__" not in page
    assert (
        head["x-frame-options"] == "DENY"
        and "frame-ancestors 'none'" in head["content-security-policy"]
    )
    # 別的主機名（DNS rebinding）一律擋
    status, body, _ = call(port, "GET", "/api/state", headers={"Host": f"evil.example:{port}"})
    assert status == 403 and "本機" in body["detail"]


def test_ac3_state_lists_configs_fields_and_ticks(running: tuple[Admin, int]) -> None:
    _, port = running
    status, state, _ = call(port, "GET", "/api/state")
    assert status == 200
    (cfg,) = state["configs"]
    assert cfg["name"] == "tmf_sim.toml" and cfg["mode"] == "sim" and cfg["ok"] and cfg["control"]
    assert cfg["values"]["risk.max_position"] == "1"
    assert [t["day"] for t in state["ticks"]] == ["2026-11-12", "2026-11-11"]
    assert state["drill"] is None and len(state["fields"]) == len(FIELDS)


def test_ac3_writes_need_the_token_json_and_same_origin(
    running: tuple[Admin, int], tmp_path: Path
) -> None:
    admin, port = running
    body = {"config": "tmf_sim.toml", "changes": {"risk.max_entries_per_day": "4"}}
    path = tmp_path / "configs" / "tmf_sim.toml"
    before = path.read_text(encoding="utf-8")
    assert (
        call(port, "POST", "/api/settings", body, {"Content-Type": "application/json"})[0] == 403
    )  # 沒有通行碼
    assert (
        call(
            port,
            "POST",
            "/api/settings",
            body,
            {TOKEN_HEADER: "guess", "Content-Type": "application/json"},
        )[0]
        == 403
    )
    assert (
        call(
            port,
            "POST",
            "/api/settings",
            body,
            {TOKEN_HEADER: admin.token, "Content-Type": "text/plain"},
        )[0]
        == 415
    )
    evil = {**write_headers(admin), "Origin": "https://evil.example"}
    assert call(port, "POST", "/api/settings", body, evil)[0] == 403
    assert path.read_text(encoding="utf-8") == before
    status, result, _ = call(
        port,
        "POST",
        "/api/settings",
        body,
        {**write_headers(admin), "Origin": f"http://127.0.0.1:{port}"},
    )
    assert status == 200 and result["changed"] == ["一天最多進場幾次：6 → 4"]


def test_ac3_bad_input_gets_a_plain_answer_not_a_crash(running: tuple[Admin, int]) -> None:
    admin, port = running
    h = write_headers(admin)
    status, body, _ = call(
        port, "POST", "/api/settings", {"config": "../../etc/passwd", "changes": {}}, h
    )
    assert status == 400 and "沒有這個設定檔" in body["detail"]
    status, body, _ = call(
        port,
        "POST",
        "/api/settings",
        {"config": "tmf_sim.toml", "changes": {"risk.force_flat_time": "12:00:00"}},
        h,
    )
    assert status == 400 and "強制平倉時間" in body["detail"]
    assert call(port, "POST", "/api/nothing", {}, h)[0] == 404
    assert call(port, "POST", "/api/settings", [1, 2], h)[0] == 400


def test_ac3_drill_start_and_stop_through_the_page(running: tuple[Admin, int]) -> None:
    admin, port = running
    h = write_headers(admin)
    body = {
        "config": "tmf_sim.toml",
        "ticks": "Daily_2026_11_12.csv",
        "start": "09:00:00",
        "minutes": "5",
        "speed": "2",
        "dsn_env": "",
    }
    status, result, _ = call(port, "POST", "/api/drill/start", body, h)
    assert status == 200 and result["drill"]["running"] is True and result["drill"]["relay"] is None
    status, result, _ = call(port, "POST", "/api/drill/start", body, h)
    assert status == 400 and "已經有一場演練在跑" in result["detail"]
    assert call(port, "GET", "/api/state")[1]["drill"]["day"] == "2026-11-12"
    status, result, _ = call(port, "POST", "/api/drill/stop", {}, h)
    assert status == 200 and result["was_running"] is True
    for bad, why in (
        ({"ticks": "../secret.csv"}, "逐筆檔名不對"),
        ({"start": "9"}, "HH:MM:SS"),
        ({"minutes": "很多"}, "數字"),
    ):
        status, result, _ = call(port, "POST", "/api/drill/start", {**body, **bad}, h)
        assert status == 400 and why in result["detail"]
