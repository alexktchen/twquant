"""A-017：從網站啟動／結束交易程式（D-229）——由本機後台執行。

使用者 2026-11-14 要網站也能啟動、結束交易程式。後台自己不連資料庫：另外開一支中繼程式，
把網站來的指令寫成本機的檔案；這裡測後台讀那個檔、執行、回報。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from autotrade import cli as cli_module
from autotrade.cli import main
from autotrade.config import ConfigError
from autotrade.control import INBOX_NAME
from autotrade.jobs import JobError
from autotrade.model import (
    ACTION_FLATTEN,
    ACTION_QUIT,
    ACTION_START,
    RESULT_ACCEPTED,
    RESULT_IGNORED,
    RESULT_QUIT,
    RESULT_REFUSED,
    RESULT_STARTED,
    STAGE_ACK,
    STAGE_DONE,
    Command,
)
from test_a011_drill import results, write_inbox
from test_a014_admin import LIVE, MADE, NOW, close_jobs, make, texts, value

__all__ = ["close_jobs"]  # 每個測試結束把開出來的（假）程式與紀錄檔收乾淨

DAY = NOW.date()
CLOUD = "TWQUANT_SUPABASE_DSN"


def remote_dir(tmp_path: Path) -> Path:
    return tmp_path / "data" / "remote"


def ask(command_id: int, action: str, target: str = "sim", seconds: float = 0.0) -> Command:
    pressed = NOW + timedelta(seconds=seconds)
    return Command(id=command_id, action=action, day=DAY, created_at=pressed, target=target)


def brief(tmp_path: Path) -> list[tuple[int, str, str]]:
    return [
        (int(str(r["command_id"])), str(r["stage"]), str(r["code"]))
        for r in results(remote_dir(tmp_path), DAY)
    ]


def notes(tmp_path: Path) -> list[str]:
    return [str(r["note"]) for r in results(remote_dir(tmp_path), DAY)]


def remote_admin(tmp_path: Path, **kw: Any) -> tuple[Any, Any, Any]:
    admin, spawn, clock = make(tmp_path, **kw)
    admin.enable_remote("tmf_sim.toml", CLOUD)
    return admin, spawn, clock


# ---- AC1 遙控中繼


def test_ac1_the_admin_opens_its_own_relay_and_closes_it(tmp_path: Path) -> None:
    admin, spawn, _ = remote_admin(tmp_path)
    (relay,) = spawn.calls
    assert relay[:4] == ["py", "-m", "autotrade", "relay"]
    assert value(relay, "--consumer") == "admin" and value(relay, "--dsn-env") == CLOUD
    assert value(relay, "--dir") == str(remote_dir(tmp_path))
    assert value(relay, "--config") == str(tmp_path / "configs" / "tmf_sim.toml")
    assert value(relay, "--env-file") == str(tmp_path / ".env") and "--bell" in relay
    assert not any("postgres" in part for part in relay)  # 連線字串不經過後台
    said = texts(admin)
    assert said[0] == (
        "遙控開了：網站可以啟動／結束交易程式（只能啟動 tmf_sim.toml、連雲端資料庫）"
    )
    assert said[1].startswith("　遙控中繼（程序編號 4321）：py -m autotrade relay ")
    with pytest.raises(JobError, match="已經在跑"):
        admin.enable_remote("tmf_sim.toml", CLOUD)
    admin.jobs.close()
    assert spawn.procs[0].terminated  # 關後台時一起收掉


def test_ac1_remote_needs_a_config_with_control_and_a_known_database(tmp_path: Path) -> None:
    admin, spawn, _ = make(tmp_path)
    with pytest.raises(JobError, match="資料庫只能選"):
        admin.enable_remote("tmf_sim.toml", "SOMETHING_ELSE")
    path = tmp_path / "configs" / "tmf_sim.toml"
    path.write_text(path.read_text(encoding="utf-8").split("[control]")[0], encoding="utf-8")
    with pytest.raises(ConfigError, match=r"沒有 \[control\]"):
        admin.enable_remote("tmf_sim.toml", CLOUD)
    assert spawn.calls == [] and admin.remote is None
    admin.watch()  # 沒開遙控：跟以前一樣


def test_ac1_admin_command_wants_both_remote_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    got: list[dict[str, Any]] = []

    def fake_admin(*args: Any, **kwargs: Any) -> int:
        got.append(kwargs)
        return 0

    monkeypatch.setattr(cli_module, "run_admin", fake_admin)
    base = ["admin", "--config-dir", str(tmp_path), "--data-dir", str(tmp_path), "--no-open"]
    assert main(base) == 0 and got[-1]["remote"] is None
    both = [*base, "--remote-dsn-env", CLOUD, "--remote-config", "tmf_sim.toml"]
    assert main(both) == 0 and got[-1]["remote"] == (CLOUD, "tmf_sim.toml")
    for half in (["--remote-dsn-env", CLOUD], ["--remote-config", "tmf_sim.toml"]):
        with pytest.raises(SystemExit):
            main([*base, *half])
    with pytest.raises(SystemExit):
        main([*base, "--remote-dsn-env", "NOPE", "--remote-config", "tmf_sim.toml"])


def test_ac1_a_remote_relay_that_dies_is_reported_once(tmp_path: Path) -> None:
    admin, spawn, _ = remote_admin(tmp_path)
    write_inbox(remote_dir(tmp_path), NOW, [])
    spawn.procs[0].code = 1
    admin.watch()
    admin.watch()
    lost = [t for t in texts(admin) if "遙控中繼自己結束了" in t]
    assert len(lost) == 1 and "到不了" in lost[0]


# ---- AC2 啟動


def test_ac2_start_from_the_website_starts_only_the_named_config(tmp_path: Path) -> None:
    admin, spawn, _ = remote_admin(tmp_path)
    write_inbox(remote_dir(tmp_path), NOW, [ask(31, ACTION_START)])
    admin.watch()
    _, ticks, relay, run = spawn.calls  # 第一支是遙控中繼；接著行情中繼、指令中繼、交易程式
    assert [c[3] for c in (ticks, relay, run)] == ["ticks", "relay", "run"]
    assert value(run, "--config") == str(tmp_path / "configs" / "tmf_sim.toml")
    assert value(relay, "--dsn-env") == CLOUD and value(relay, "--consumer") == "sim"
    assert "--ticks-file" in run and value(run, "--notify") == "send"  # 設定檔有通知就發
    assert "--release-stop" not in run and "--play" not in run  # 不能順便解除今天的停止
    assert brief(tmp_path) == [(31, STAGE_ACK, RESULT_ACCEPTED), (31, STAGE_DONE, RESULT_STARTED)]
    first = results(remote_dir(tmp_path), DAY)[0]
    assert first["action"] == ACTION_START and first["created_at"] == NOW.isoformat()
    said = texts(admin)
    assert "遠端：網站要求啟動模擬的交易程式（08:30:00 按下）" in said
    assert "遠端：已啟動交易程式（tmf_sim.toml、今天的即時行情）" in said
    assert admin.jobs.run is not None and admin.jobs.run.running()

    admin.watch()  # 回報還沒送回資料庫之前，同一筆會再出現在指令檔：不重做
    assert len(spawn.calls) == 4 and len(brief(tmp_path)) == 2


def test_ac2_start_is_refused_with_the_reason(tmp_path: Path) -> None:
    admin, spawn, _ = remote_admin(tmp_path)
    admin.start_run(LIVE)  # 已經在跑
    write_inbox(remote_dir(tmp_path), NOW, [ask(32, ACTION_START), ask(33, ACTION_START, "live")])
    admin.watch()
    assert brief(tmp_path) == [
        (32, STAGE_ACK, RESULT_ACCEPTED),
        (32, STAGE_DONE, RESULT_REFUSED),
        (33, STAGE_ACK, RESULT_ACCEPTED),
        (33, STAGE_DONE, RESULT_REFUSED),
    ]
    said = notes(tmp_path)
    assert "沒有執行：交易程式已經在跑" in said[1]
    assert said[3] == "沒有執行：這台後台可以遙控的設定（tmf_sim.toml）是模擬，不是真單"
    assert len(spawn.calls) == 4  # 遙控中繼＋原本那三支，沒有多開


# ---- AC3 結束


def test_ac3_quit_from_the_website_ends_the_program(tmp_path: Path) -> None:
    admin, spawn, _ = remote_admin(tmp_path)
    admin.start_run(LIVE)
    write_inbox(remote_dir(tmp_path), NOW, [ask(34, ACTION_QUIT)])
    admin.watch()
    assert brief(tmp_path) == [(34, STAGE_ACK, RESULT_ACCEPTED), (34, STAGE_DONE, RESULT_QUIT)]
    assert notes(tmp_path)[1] == "已結束交易程式"
    assert admin.jobs.run is not None and not admin.jobs.run.running()
    assert all(p.terminated for p in spawn.procs[1:])  # 交易程式與兩支中繼都收掉
    assert not spawn.procs[0].terminated  # 遙控中繼留著：之後還要能再啟動

    write_inbox(remote_dir(tmp_path), NOW, [ask(35, ACTION_QUIT), ask(36, ACTION_QUIT, "live")])
    admin.watch()
    assert brief(tmp_path)[3] == (35, STAGE_DONE, RESULT_QUIT)
    assert notes(tmp_path)[3] == "交易程式本來就沒有在跑"
    assert brief(tmp_path)[5] == (36, STAGE_DONE, RESULT_REFUSED)


# ---- AC4 回報


def test_ac4_other_days_and_other_actions_are_not_run(tmp_path: Path) -> None:
    admin, spawn, _ = remote_admin(tmp_path)
    yesterday = Command(41, ACTION_START, DAY - timedelta(days=1), NOW, "sim")
    write_inbox(remote_dir(tmp_path), NOW, [yesterday, ask(42, ACTION_FLATTEN)])
    admin.watch()
    assert brief(tmp_path) == [(41, STAGE_ACK, RESULT_IGNORED), (42, STAGE_ACK, RESULT_IGNORED)]
    assert "不是今天的" in notes(tmp_path)[0] and "不認得的動作" in notes(tmp_path)[1]
    assert len(spawn.calls) == 1 and admin.jobs.run is None


def test_ac4_reading_trouble_is_said_once_and_not_at_startup(tmp_path: Path) -> None:
    admin, _, clock = remote_admin(tmp_path)
    before = len(texts(admin))
    admin.watch()  # 剛開：中繼程式還沒寫出第一份指令檔，不算問題
    assert len(texts(admin)) == before
    clock.sleep(20)
    admin.watch()
    admin.watch()
    trouble = [t for t in texts(admin) if t.startswith("遠端：讀不到遙控指令")]
    assert len(trouble) == 1 and INBOX_NAME in trouble[0] and "暫時到不了" in trouble[0]
    write_inbox(remote_dir(tmp_path), clock.now, [])
    admin.watch()
    assert texts(admin)[-1] == "遠端：遙控指令恢復可以讀"


def test_ac5_the_admin_leaves_a_heartbeat_for_the_website(tmp_path: Path) -> None:
    from autotrade.control import HEARTBEAT_NAME

    admin, _, clock = remote_admin(tmp_path)
    write_inbox(remote_dir(tmp_path), NOW, [])
    admin.watch()
    beat = (remote_dir(tmp_path) / HEARTBEAT_NAME).read_text(encoding="utf-8")
    assert datetime.fromisoformat(beat) == clock.now  # 中繼程式拿它算「後台多久前讀過指令」
    assert MADE  # close_jobs 會把這個後台開的東西收掉
