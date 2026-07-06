"""T-023：每日 pipeline 編排 + heartbeat/watchdog。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from twquant.pipeline import (
    PipelineStep,
    read_heartbeat,
    run_pipeline,
    watchdog_check,
    write_heartbeat,
)

_DAY = dt.date(2026, 6, 30)
_NOW = dt.datetime(2026, 6, 30, 20, 0)


def _step(name: str, detail: str, *, fail: bool = False, **kw: object) -> PipelineStep:
    def fn() -> str:
        if fail:
            raise RuntimeError("boom")
        return detail

    return PipelineStep(name=name, fn=fn, **kw)  # type: ignore[arg-type]


def test_pipeline_all_ok_writes_heartbeat(tmp_path: Path) -> None:
    steps = [
        _step("ingest", "3 表"),
        _step("gate", "pass"),
        _step("signal", "40 檔", skip_if_degraded=True),
    ]
    res = run_pipeline(_DAY, steps, data_dir=tmp_path, git_hash="abc123", now=_NOW)
    assert [s.status for s in res.steps] == ["ok", "ok", "ok"]
    assert res.signals_produced is True and res.degraded is False
    hb = read_heartbeat(tmp_path)
    assert hb is not None and hb.day == "2026-06-30" and hb.git_hash == "abc123"


def test_pipeline_critical_fail_degrades_and_skips_signal(tmp_path: Path) -> None:
    steps = [
        _step("ingest", "ok"),
        _step("gate", "", fail=True, critical=True),  # 品質閘門失敗
        _step("signal", "40 檔", skip_if_degraded=True),
    ]
    res = run_pipeline(_DAY, steps, data_dir=tmp_path, git_hash="x", now=_NOW)
    status = {s.name: s.status for s in res.steps}
    assert status["gate"] == "failed"
    assert status["signal"] == "skipped"  # 降級 → 不產生新訊號
    assert res.degraded is True and res.signals_produced is False
    assert read_heartbeat(tmp_path) is not None  # heartbeat 仍寫（pipeline 有跑）


def test_watchdog_missing_and_stale(tmp_path: Path) -> None:
    assert watchdog_check(tmp_path, _DAY).ok is False  # 無 heartbeat
    write_heartbeat(tmp_path, dt.date(2026, 6, 29), "x", _NOW)
    stale = watchdog_check(tmp_path, _DAY)
    assert stale.ok is False and "2026-06-29" in stale.reason  # 停在昨日
    write_heartbeat(tmp_path, _DAY, "x", _NOW)
    assert watchdog_check(tmp_path, _DAY).ok is True
