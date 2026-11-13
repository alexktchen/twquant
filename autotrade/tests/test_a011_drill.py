"""A-011 AC21：演練程式。用模擬券商、照時鐘播歷史價格、買 1 口抱著，等網站按停止。全程不碰券商。"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from autotrade.cli import main
from autotrade.config import ConfigError, load_config
from autotrade.control import INBOX_NAME, command_to_json, outbox_name, write_json_atomic
from autotrade.drill import DRILL_STRATEGY, Drill, HoldOne, drill_config, load_prices
from autotrade.model import ACTION_STOP, TAIPEI, Command, Side, Tick
from autotrade.risk import HALT_KILL
from test_a002_engine import ROWS, write_csv

CONFIG_PATH = Path(__file__).parents[1] / "configs" / "tmf_sim.toml"
NOW = datetime(2026, 11, 14, 22, 30, 0, tzinfo=TAIPEI)  # 晚上、週六：演練要能跑


@dataclass
class FakeClock:
    """假時鐘：sleep 只把時間往前推，不真的等。"""

    now: datetime = NOW

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def prices(n: int = 400, start: float = 27000.0) -> list[tuple[float, float, int]]:
    """每 0.5 秒一筆，價格慢慢往上。"""
    return [(i * 0.5, start + i, 1) for i in range(n)]


def make(tmp_path: Path, clock: FakeClock, said: list[str], **kw: object) -> Drill:
    cfg = drill_config(load_config(CONFIG_PATH))
    return Drill(
        cfg,
        prices(),
        tmp_path,
        clock=clock,
        sleep=clock.sleep,
        say=said.append,
        **kw,  # type: ignore[arg-type]
    )


def write_inbox(folder: Path, written: datetime, commands: list[Command]) -> None:
    write_json_atomic(
        folder / INBOX_NAME,
        {
            "written_at": written.isoformat(),
            "ok": True,
            "error": None,
            "commands": [command_to_json(c) for c in commands],
        },
    )


def results(folder: Path, day: date) -> list[dict[str, object]]:
    path = folder / outbox_name(day)
    if not path.exists():
        return []
    rows: list[dict[str, object]] = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    return rows


def test_ac21_config_is_sim_with_the_whole_day_open() -> None:
    base = load_config(CONFIG_PATH)
    cfg = drill_config(replace(base, mode="live"))
    assert cfg.mode == "sim"  # 演練一定是模擬
    assert (cfg.risk.entry_start, cfg.risk.entry_end) == (time(0, 0), time(23, 59, 58))
    assert cfg.risk.force_flat_time == time(23, 59, 59)
    assert base.risk.entry_end != cfg.risk.entry_end  # 原本的設定沒被改到
    with pytest.raises(ConfigError, match=r"\[control\]"):
        drill_config(replace(base, control=None))


def test_ac21_hold_one_buys_once() -> None:
    strategy = HoldOne("TMF")
    tick = Tick(ts=NOW, price=27000.0, qty=1)
    (req,) = strategy.on_tick(tick, 0)
    assert (req.side, req.qty, req.symbol) == (Side.BUY, 1, "TMF")
    assert strategy.on_tick(tick, 0) == [] and strategy.on_tick(tick, 1) == []


def test_ac21_stop_pressed_on_the_website_flattens_the_drill(tmp_path: Path) -> None:
    clock, said = FakeClock(), list[str]()
    status = tmp_path / "status.json"
    drill = make(tmp_path, clock, said, status=status)
    # 中繼程式還沒寫指令檔之前先放一份空的（中繼程式在跑）；10 秒時網站按了停止
    write_inbox(tmp_path, NOW, [])
    pressed = NOW + timedelta(seconds=10)

    original_sleep = clock.sleep

    def sleep_and_relay(seconds: float) -> None:
        original_sleep(seconds)
        commands = (
            [Command(id=1, action=ACTION_STOP, day=NOW.date(), created_at=pressed)]
            if clock.now >= pressed
            else []
        )
        write_inbox(tmp_path, clock.now, commands)  # 假的中繼程式：每一圈都更新指令檔

    drill.sleep = sleep_and_relay
    report = drill.run(30)
    assert report.halt == HALT_KILL and report.position == 0 and report.stops == 1
    rows = results(tmp_path, NOW.date())
    assert [(r["stage"], r["code"]) for r in rows] == [("ack", "flattening"), ("done", "flat")]
    assert all(r["created_at"] == pressed.isoformat() for r in rows)
    text = "\n".join(said)
    assert (
        "演練" in text
        and "收到遠端緊急停止（第 1 號，22:30:10 按下）" in text
        and "緊急停止" in text
    )
    snap = json.loads(status.read_text(encoding="utf-8"))
    assert snap["strategy"] == DRILL_STRATEGY and snap["control"]["listening"] is True
    assert snap["day"] == "2026-11-14"  # 狀態蓋的是今天的日期
    assert (tmp_path / "drill_journal.jsonl").exists()


def test_ac21_without_a_relay_it_keeps_holding_and_says_the_link_is_down(tmp_path: Path) -> None:
    clock, said = FakeClock(), list[str]()
    report = make(tmp_path, clock, said).run(20)
    assert report.position == 1 and report.halt is None and report.stops == 0
    downs = [line for line in said if "讀不到遠端指令" in line]
    assert len(downs) == 1 and "中繼程式沒在跑" in downs[0]


def test_ac21_prices_come_from_the_tick_file_after_the_start_time(tmp_path: Path) -> None:
    path = write_csv(tmp_path / "Daily_2026_11_13.csv", ROWS, encoding="utf-8-sig")
    cfg = drill_config(load_config(CONFIG_PATH))
    got = load_prices(cfg, path, date(2026, 11, 13), time(8, 45))
    assert got[0] == (0.0, 27001.0, 2) and [p for _, p, _ in got] == [27001.0, 27002.0, 27010.0]
    with pytest.raises(ValueError, match="之後沒有"):
        load_prices(cfg, path, date(2026, 11, 13), time(13, 0))


def test_ac21_cli_runs_a_short_drill(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_csv(tmp_path / "Daily_2026_11_13.csv", ROWS, encoding="utf-8-sig")
    folder = tmp_path / "share"
    folder.mkdir()
    args = ["drill", "--config", str(CONFIG_PATH), "--ticks", str(path), "--day", "2026-11-13"]
    assert main([*args, "--from", "08:45:00", "--dir", str(folder), "--minutes", "0.02"]) == 0
    out = capsys.readouterr().out
    assert "演練開始" in out and "演練結束" in out
    with pytest.raises(FileNotFoundError):
        main([*args, "--dir", str(tmp_path / "nope"), "--minutes", "0.01"])
