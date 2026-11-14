"""A-014 AC9：從後台啟動、結束模擬交易程式。使用者 2026-11-14：「從後台啟動交易程式」。"""

from __future__ import annotations

import json
import time as time_module
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from autotrade.admin import Admin, Console
from autotrade.admin_page import PAGE
from autotrade.config import load_config
from autotrade.jobs import JobError, Jobs, RunRequest, read_status
from autotrade.runner import QUIT_NAME
from test_a011_drill import FakeClock
from test_a012_admin import FakeSpawn, config_copy, recorded_dir, ticks_dir

NOW = datetime(2026, 11, 16, 8, 30, tzinfo=FakeClock().now.tzinfo)  # 星期一開盤前
LIVE = {"config": "tmf_sim.toml", "source": "live", "dsn_env": "TWQUANT_SUPABASE_DSN"}
PLAY = {"config": "tmf_sim.toml", "source": "play", "ticks": "TMF_2026-11-13.csv", "speed": "60"}


MADE: list[Admin] = []


@pytest.fixture(autouse=True)
def close_jobs() -> Iterator[None]:
    """每個測試結束把開出來的（假）程式與紀錄檔收乾淨。"""
    yield
    for admin in MADE:
        admin.jobs.close()
    MADE.clear()


def make(tmp_path: Path, **kw: Any) -> tuple[Admin, FakeSpawn, FakeClock]:
    config_copy(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    env = tmp_path / ".env"
    env.write_text("X=1\n", encoding="utf-8")
    ms = tmp_path / "ms"
    ms.mkdir(exist_ok=True)
    spawn, clock = FakeSpawn(), FakeClock(NOW)
    args: dict[str, Any] = {
        "recorded_dir": recorded_dir(tmp_path),
        "ms_repo": ms,
        "clock": clock,
        "helper_linger_s": 8.0,
    } | kw
    admin = Admin(
        tmp_path / "configs",
        ticks_dir(tmp_path),
        Jobs(tmp_path / "data", env_file=env, spawn=spawn, python="py"),
        Console(echo=False),
        **args,
    )
    MADE.append(admin)
    return admin, spawn, clock


def value(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


def texts(admin: Admin) -> list[str]:
    return [line["text"] for line in admin.console.lines()]


def test_ac9_live_start_runs_three_programs_with_the_right_arguments(tmp_path: Path) -> None:
    admin, spawn, _ = make(tmp_path)
    state = admin.start_run({**LIVE, "notify": True})["run"]
    folder = tmp_path / "data" / "run" / "2026-11-16"
    assert state["running"] and state["source"] == "live" and state["folder"] == str(folder)
    ticks, relay, run = spawn.calls
    assert [c[3] for c in spawn.calls] == ["ticks", "relay", "run"]  # 先行情、再指令、最後交易程式
    config = str(tmp_path / "configs" / "tmf_sim.toml")
    assert all(value(c, "--config") == config for c in spawn.calls)
    # 行情中繼只讀本機資料庫（富邦錄製程式寫的地方），不管指令中繼選哪個
    assert value(ticks, "--dsn-env") == "TWQUANT_PG_DSN"
    assert value(ticks, "--out") == value(run, "--ticks-file") == str(folder / "ticks.csv")
    assert (
        value(relay, "--dsn-env") == "TWQUANT_SUPABASE_DSN" and value(relay, "--consumer") == "sim"
    )
    assert value(relay, "--dir") == value(run, "--dir") == str(folder)
    assert value(relay, "--status") == value(run, "--status") == str(folder / "status.json")
    assert value(run, "--strategy") == "momentum" and value(run, "--ms-repo") == str(
        tmp_path / "ms"
    )
    assert value(run, "--notify") == "send" and value(run, "--env-file") == str(tmp_path / ".env")
    assert "--release-stop" not in run and "--play" not in run
    assert not any("postgres" in " ".join(c) for c in spawn.calls)  # 連線字串不經過後台
    said = texts(admin)
    assert said[0] == (
        "啟動交易程式（模擬）：tmf_sim.toml、今天的即時行情、指令中繼連雲端資料庫、發手機通知"
    )
    assert said[1].startswith("　行情中繼（程序編號 4321）：py -m autotrade ticks ")
    assert said[2].startswith("　指令中繼（") and said[3].startswith("　交易程式（")
    assert said[4] == f"　輸出與紀錄在 {folder}"


def test_ac9_live_options_release_stop_and_no_notify(tmp_path: Path) -> None:
    admin, spawn, _ = make(tmp_path)
    admin.start_run({**LIVE, "dsn_env": "TWQUANT_PG_DSN", "release_stop": True})
    run = spawn.calls[-1]
    assert "--release-stop" in run and "--notify" not in run
    assert value(spawn.calls[1], "--dsn-env") == "TWQUANT_PG_DSN"
    assert "指令中繼連本機資料庫、不發手機通知、解除今天稍早的遠端停止" in texts(admin)[0]


def test_ac9_playback_runs_only_the_trading_program(tmp_path: Path) -> None:
    admin, spawn, _ = make(tmp_path)
    state = admin.start_run({**PLAY, "start": "09:00:00"})["run"]
    (run,) = spawn.calls
    assert (
        state["source"] == "play" and state["play_day"] == "2026-11-13" and state["relay"] is None
    )
    assert Path(state["folder"]).name == "play_20261116_083000"
    assert value(run, "--play") == str(tmp_path / "recorded" / "TMF_2026-11-13.csv")
    assert (value(run, "--day"), value(run, "--from"), value(run, "--speed")) == (
        "2026-11-13",
        "09:00:00",
        "60",
    )
    assert "--ticks-file" not in run and "--notify" not in run
    assert (
        "回放 2026-11-13 09:00:00 起、60 倍速（只在這台電腦看，不上網站、不發通知）"
        in texts(admin)[0]
    )


def test_ac9_refuses_what_it_cannot_run(tmp_path: Path) -> None:
    admin, spawn, _ = make(tmp_path)
    path = tmp_path / "configs" / "tmf_sim.toml"
    original = path.read_text(encoding="utf-8")
    for body, why in (
        ({**LIVE, "source": "yuanta"}, "行情來源要選"),
        ({**LIVE, "dsn_env": "SOMETHING_ELSE"}, "資料庫只能選"),
        ({**LIVE, "config": "../x.toml"}, "沒有這個設定檔"),
        ({**PLAY, "ticks": "../TMF_2026-11-13.csv"}, "回放要選一天富邦錄好的逐筆"),
        ({**PLAY, "ticks": "TMF_2026-11-01.csv"}, "回放要選一天富邦錄好的逐筆"),
        ({**PLAY, "start": "9"}, "HH:MM:SS"),
        ({**PLAY, "speed": "很快"}, "倍速要是數字"),
        ({**PLAY, "speed": "0"}, "倍速要在"),
        ({**PLAY, "speed": "1000"}, "倍速要在"),
    ):
        with pytest.raises((JobError, ValueError), match=why):
            admin.start_run(body)
    assert spawn.calls == [] and admin.jobs.run is None
    # 沒有 [notify] 的設定檔不能勾通知；沒有 [control] 的不能跑盤中
    path.write_text(original.split("[notify]")[0], encoding="utf-8")
    with pytest.raises(JobError, match=r"沒有 \[notify\]"):
        admin.start_run({**LIVE, "notify": True})
    path.write_text(original.split("[control]")[0], encoding="utf-8")
    with pytest.raises(JobError, match=r"沒有 \[control\]"):
        admin.start_run(LIVE)
    path.write_text(original, encoding="utf-8")
    no_ms, spawn2, _ = make(tmp_path, ms_repo=None)
    with pytest.raises(JobError, match="--ms-repo"):
        no_ms.start_run(LIVE)
    assert spawn2.calls == []


def test_ac9_real_orders_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """真單的設定檔從後台啟動不了：真單還沒接上元大。"""
    admin, spawn, _ = make(tmp_path)
    live = replace(load_config(tmp_path / "configs" / "tmf_sim.toml"), mode="live")
    monkeypatch.setattr("autotrade.admin.validate", lambda text: live)
    with pytest.raises(JobError, match="真單還沒接上元大"):
        admin.start_run(LIVE)
    assert spawn.calls == []


def test_ac9_one_program_at_a_time_and_never_with_a_drill(tmp_path: Path) -> None:
    admin, _, _ = make(tmp_path)
    admin.start_run(LIVE)
    with pytest.raises(JobError, match="交易程式已經在跑"):
        admin.start_run(PLAY)
    with pytest.raises(JobError, match="交易程式正在跑；先按「結束」再演練"):
        admin.start_drill({"config": "tmf_sim.toml", "ticks": "TMF_2026-11-13.csv"})
    admin.stop_run()
    admin.start_drill({"config": "tmf_sim.toml", "ticks": "TMF_2026-11-13.csv", "dsn_env": ""})
    with pytest.raises(JobError, match="演練正在跑；先停掉演練"):
        admin.start_run(LIVE)
    admin.jobs.close()


def test_ac9_stop_asks_first_then_takes_everything_down(tmp_path: Path) -> None:
    admin, spawn, _ = make(tmp_path)
    admin.start_run(LIVE)
    folder = tmp_path / "data" / "run" / "2026-11-16"
    assert not (folder / QUIT_NAME).exists()
    assert admin.stop_run() == {"ok": True, "was_running": True}
    assert (folder / QUIT_NAME).read_text(encoding="utf-8") == "quit"  # 先請它自己收工
    assert all(p.terminated for p in spawn.procs)  # 等不到才強制；兩支中繼也收掉
    state = admin.state()["run"]
    assert state["running"] is False and state["stopped"] is True
    assert texts(admin)[-1] == "交易程式已結束"
    assert admin.stop_run()["was_running"] is False
    assert texts(admin)[-1] == "按了結束，但交易程式沒有在跑"
    # 同一天再啟動：同一個資料夾、上一次的「結束」不算、紀錄接在後面
    admin.start_run(LIVE)
    assert not (folder / QUIT_NAME).exists()
    assert (folder / "run.log").read_text(encoding="utf-8") == "run 開始\nrun 開始\n"
    admin.jobs.close()
    assert all(p.terminated for p in spawn.procs)


def test_ac9_a_program_that_ends_by_itself_is_reported_and_helpers_linger(tmp_path: Path) -> None:
    admin, spawn, clock = make(tmp_path)
    admin.start_run(LIVE)
    ticks, relay, run = spawn.procs
    job = admin.jobs.run
    assert job is not None
    (job.folder / "run.log").write_text(
        "…\n程式結束（收盤，今天結束）：成交 4 筆\n", encoding="utf-8"
    )
    run.code = 0
    admin.watch()
    assert texts(admin)[-1] == "交易程式結束了：程式結束（收盤，今天結束）：成交 4 筆"
    assert not ticks.terminated and not relay.terminated  # 指令中繼還要把最後一份狀態送上網站
    clock.sleep(5)
    admin.watch()
    assert not relay.terminated
    clock.sleep(4)
    admin.watch()
    assert ticks.terminated and relay.terminated
    assert texts(admin)[-1] == "行情中繼、指令中繼也收掉了"
    count = len(texts(admin))
    admin.watch()
    assert len(texts(admin)) == count  # 只報一次
    assert admin.state()["run"]["stopped"] is False


def test_ac9_a_relay_that_dies_while_trading_is_reported(tmp_path: Path) -> None:
    """交易程式還在跑，中繼程式卻自己結束了：要講出來（不然只會看到行情停住），而且只講一次。"""
    admin, spawn, _ = make(tmp_path)
    admin.start_run(LIVE)
    ticks, relay, _run = spawn.procs
    job = admin.jobs.run
    assert job is not None
    lines = ["…", "psycopg.OperationalError: 連不上", ""]
    (job.folder / "ticks.log").write_text(chr(10).join(lines), encoding="utf-8")
    ticks.code = 1
    admin.watch()
    admin.watch()
    warns = [line["text"] for line in admin.console.lines() if line["level"] == "warn"]
    assert warns == [
        "行情中繼自己結束了（結束碼 1）：psycopg.OperationalError: 連不上。"
        "交易程式還在跑，但收不到新的行情；請按「結束」再重新啟動"
    ]
    relay.code = 3
    admin.watch()
    assert "指令中繼自己結束了（結束碼 3）" in admin.console.lines()[-1]["text"]
    assert "聽不到遠端緊急停止、網站也不會更新" in admin.console.lines()[-1]["text"]
    assert admin.state()["run"]["running"] is True


def test_ac9_a_program_that_dies_says_why(tmp_path: Path) -> None:
    admin, spawn, _ = make(tmp_path)
    admin.start_run(PLAY)
    job = admin.jobs.run
    assert job is not None
    (job.folder / "run.log").write_text("Traceback…\nValueError: 沒有這一天\n", encoding="utf-8")
    spawn.procs[-1].code = 1
    admin.watch()
    last = admin.console.lines()[-1]
    assert (
        last["level"] == "warn"
        and last["text"] == "交易程式沒跑完（結束碼 1）：ValueError: 沒有這一天"
    )


def test_ac9_state_shows_the_position_and_pnl_from_the_status_file(tmp_path: Path) -> None:
    admin, _, _ = make(tmp_path)
    assert admin.state()["run"] is None and admin.state()["run_ready"] == {"ms_repo": True}
    admin.start_run(LIVE)
    folder = tmp_path / "data" / "run" / "2026-11-16"
    assert admin.state()["run"]["status"] is None  # 還沒有行情
    fills = [{"ts": f"2026-11-16T09:{i:02d}:00+08:00", "side": "BUY", "qty": 1} for i in range(10)]
    snap = {
        "day": "2026-11-16",
        "status": "live",
        "state": "ok",
        "halt": None,
        "position": 1,
        "avg_price": 44290.0,
        "last_price": 44300.0,
        "last_ts": "2026-11-16T09:10:00+08:00",
        "day_pnl_twd": 100.0,
        "realized_twd": 0.0,
        "open_pnl_twd": 100.0,
        "entries": 1,
        "updated_at": "2026-11-16T09:10:01+08:00",
        "fills": fills,
        "events": ["很長的東西不用給後台"],
    }
    (folder / "status.json").write_text(json.dumps(snap), encoding="utf-8")
    status = admin.state()["run"]["status"]
    assert status["position"] == 1 and status["day_pnl_twd"] == 100.0 and status["state"] == "ok"
    assert status["fill_count"] == 10 and status["fills"] == fills[-8:] and "events" not in status
    (folder / "status.json").write_text("{", encoding="utf-8")
    assert "狀態檔讀不懂" in admin.state()["run"]["status"]["error"]
    assert read_status(folder / "nope.json") is None
    admin.jobs.close()


def test_ac9_jobs_check_the_request_themselves(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    jobs = Jobs(tmp_path / "data", spawn=FakeSpawn(), python="py")
    config = config_copy(tmp_path)
    with pytest.raises(JobError, match="找不到設定檔"):
        jobs.start_run(RunRequest(tmp_path / "nope.toml", "momentum", None, "live"))
    with pytest.raises(JobError, match="行情來源只能是"):
        jobs.start_run(RunRequest(config, "momentum", None, "tape"))
    with pytest.raises(JobError, match="回放要選一天"):
        jobs.start_run(RunRequest(config, "momentum", None, "play"))
    job = jobs.start_run(RunRequest(config, "none", None, "live", dsn_env="TWQUANT_PG_DSN"), NOW)
    assert "--env-file" not in job.commands["relay"] and "--ms-repo" not in job.commands["run"]
    jobs.close()


def test_ac9_watcher_thread_keeps_watching_until_told_to_stop(tmp_path: Path) -> None:
    admin, _, _ = make(tmp_path)
    seen: list[int] = []
    admin.watch = lambda: seen.append(1)  # type: ignore[method-assign]
    stop = admin.start_watcher(every_s=0.01)
    deadline = time_module.monotonic() + 5
    while len(seen) < 3 and time_module.monotonic() < deadline:
        time_module.sleep(0.01)
    stop.set()
    assert len(seen) >= 3
    time_module.sleep(0.05)
    count = len(seen)
    time_module.sleep(0.05)
    assert len(seen) == count  # 停了就不再叫


def test_ac9_page_has_the_trading_program_tab() -> None:
    assert 'id="tab-run"' in PAGE and 'id="view-run"' in PAGE and "renderRun" in PAGE
    assert "啟動交易程式" in PAGE and "解除今天的停止" in PAGE and "回放錄好的一天" in PAGE
    assert "/api/run/start" in PAGE and "/api/run/stop" in PAGE
