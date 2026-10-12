"""T-023：每日 pipeline 編排 + heartbeat/watchdog。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from twquant.pipeline import (
    PipelineStep,
    read_heartbeat,
    run_pipeline,
    watchdog_check,
    with_skips,
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


def test_t332_ac1_skipped_steps_are_recorded_and_not_run(tmp_path: Path) -> None:
    """雲端略過的步驟記為 skipped、附原因，而且函式完全不會被呼叫。"""
    called: list[str] = []

    def boom() -> str:
        called.append("board")
        raise AssertionError("略過的步驟不該被執行")

    steps = with_skips(
        [_step("ingest", "ok"), PipelineStep(name="board", fn=boom), _step("picks", "30 檔")],
        ["board"],
        "雲端不跑",
    )
    res = run_pipeline(_DAY, steps, data_dir=tmp_path, git_hash="x", now=_NOW)
    assert [(s.name, s.status) for s in res.steps] == [
        ("ingest", "ok"),
        ("board", "skipped"),
        ("picks", "ok"),
    ]
    assert res.steps[1].detail == "雲端不跑"
    assert called == []


def test_t332_ac2_unknown_skip_name_raises() -> None:
    """設定打錯字不得靜默變成「全部照跑」。"""
    with pytest.raises(ValueError, match="看板"):
        with_skips([_step("ingest", "ok")], ["看板"], "x")


def test_t332_ac3_cloud_skip_names_exist_in_daily_pipeline() -> None:
    """serving.yaml 的 cloud.skip_pipeline_steps 每個名字都是每日 pipeline 真的有的步驟（靜態）。"""
    from twquant.config import ServingConfig, load_config

    names = load_config(Path("configs/serving.yaml"), ServingConfig).cloud.skip_pipeline_steps
    assert names, "雲端應該至少略過回測重播等步驟"
    source = Path("src/twquant/cli.py").read_text(encoding="utf-8")
    for name in names:
        assert f'PipelineStep("{name}"' in source, f"每日 pipeline 沒有「{name}」這個步驟"
    assert 'PipelineStep("每日名單"' in source
    assert "每日名單" not in names  # 每日名單正是雲端要做的事


def test_watchdog_missing_and_stale(tmp_path: Path) -> None:
    assert watchdog_check(tmp_path, _DAY).ok is False  # 無 heartbeat
    write_heartbeat(tmp_path, dt.date(2026, 6, 29), "x", _NOW)
    stale = watchdog_check(tmp_path, _DAY)
    assert stale.ok is False and "2026-06-29" in stale.reason  # 停在昨日
    write_heartbeat(tmp_path, _DAY, "x", _NOW)
    assert watchdog_check(tmp_path, _DAY).ok is True


def test_d175_required_tables_missing_names_each_absent_table() -> None:
    """D-175：提早跑時，當天必要的表（上市、上櫃各一份）有沒抓到的就列出來；融資券不在必要清單。"""
    from twquant.pipeline import missing_required

    req = ("twse/prices_daily", "tpex/prices_daily", "tpex/stock_valuation")
    assert missing_required(req, ["twse/margin_daily"]) == []
    assert missing_required(req, ["tpex/stock_valuation", "twse/margin_daily"]) == [
        "tpex/stock_valuation"
    ]
    assert missing_required((), ["tpex/prices_daily"]) == []


def test_d175_system_yaml_requires_both_markets_but_not_margin() -> None:
    """D-175：必要清單含上市與上櫃的行情、估值與上市法人；21:00 才公布的融資券不在裡面（D-008 以 T-1 進訊號）。"""
    from twquant.config import SystemConfig, load_config

    req = set(load_config(Path("configs/system.yaml"), SystemConfig).quality_gate.required_tables)
    assert {"twse/prices_daily", "tpex/prices_daily", "twse/stock_valuation"} <= req
    assert {"tpex/stock_valuation", "twse/institutional_daily"} <= req
    assert not any(t.endswith("/margin_daily") for t in req)


def test_revenue_month_is_previous_calendar_month() -> None:
    """每日 pipeline 的月營收步驟抓「上個月」（M 月營收在 M+1 月 10 日前公布）；跨年要對。"""
    from twquant.pipeline import revenue_month

    assert revenue_month(dt.date(2026, 10, 12)) == "2026-09"
    assert revenue_month(dt.date(2026, 1, 5)) == "2025-12"


def test_local_daily_pipeline_fetches_monthly_revenue_before_serving() -> None:
    """本機每日排程原本沒抓月營收，9 月營收公布後本機名單與雲端分歧（2026-10-12）。
    「月營收」步驟要排在「供應層」之前（供應層才把它同步進 PG）；雲端由 workflow 先抓，所以略過。"""
    from twquant.config import ServingConfig, load_config

    source = Path("src/twquant/cli.py").read_text(encoding="utf-8")
    rev = source.index('PipelineStep("月營收"')
    assert rev < source.index('PipelineStep("供應層"')
    assert (
        "月營收"
        in load_config(Path("configs/serving.yaml"), ServingConfig).cloud.skip_pipeline_steps
    )
