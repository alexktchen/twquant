"""SPEC-007：擷取後台（monitoring.snapshot 的 ingest_matrix / ingest_gaps）測試。

覆蓋涵蓋矩陣的四態分類（present/missing/failed/none）、以交易日曆判真缺口（假日不算）、
失敗紀錄浮現、連續區段歸併、建議補抓指令與 D-015 ban 警示。純顯示查詢 + append-only
ingest_log，無需完整策略配置。
"""

from __future__ import annotations

import datetime as dt
import sys
import time
from pathlib import Path

import polars as pl
import pytest
from fastapi import HTTPException

from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.monitoring import api
from twquant.monitoring import jobs as jobmod
from twquant.monitoring import snapshot as snap
from twquant.reporting.ingest_log import record

# 三個連續開市日（週一～週三）
_D1 = dt.date(2026, 6, 29)
_D2 = dt.date(2026, 6, 30)
_D3 = dt.date(2026, 7, 1)


def _prices(rows: list[tuple[dt.date, str, float]]) -> pl.DataFrame:
    """(date, stock_id, close) → 最小合法 prices_daily 批（OHLC 設為 close）。"""
    dates = [r[0] for r in rows]
    close = [r[2] for r in rows]
    return pl.DataFrame(
        {
            "date": dates,
            "stock_id": [r[1] for r in rows],
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": [1000 for _ in rows],
            "amount": [1e8 for _ in rows],
            "limit_up_price": pl.Series([None] * len(rows), dtype=pl.Float64),
            "limit_down_price": pl.Series([None] * len(rows), dtype=pl.Float64),
            "available_at": pl.Series(
                [dt.datetime(d.year, d.month, d.day, 18, 0, tzinfo=TZ) for d in dates],
                dtype=schemas.TW_DT,
            ),
        }
    )


# ── 涵蓋矩陣（熱力圖）─────────────────────────────────────────────────
def test_matrix_present_and_not_yet_covered(snapshot_wh: Warehouse) -> None:
    m = snap.ingest_matrix(snapshot_wh)
    cells = m["cells"]
    assert isinstance(cells, dict)
    twse = cells["prices_daily:twse"]
    assert isinstance(twse, dict)
    assert twse["2026-06-29"] == "present"
    assert twse["2026-06-30"] == "present"
    # 上櫃整條無資料 → none（尚未涵蓋，非漏抓）
    assert set(cells["prices_daily:tpex"].values()) == {"none"}
    # 三大法人只有 06-30；06-29 在其首日之前 → none（不是漏抓）
    inst = cells["institutional_daily:twse"]
    assert isinstance(inst, dict)
    assert inst["2026-06-29"] == "none"
    assert inst["2026-06-30"] == "present"


def test_matrix_marks_failure(snapshot_wh: Warehouse) -> None:
    # 融資券 06-30 擷取失敗（該表尚無任何資料）→ 該格為 failed，非 none。
    record(snapshot_wh.root, "twse", "margin_daily", _D2, "failed", detail="TWSE 回空白")
    m = snap.ingest_matrix(snapshot_wh)
    cells = m["cells"]
    assert isinstance(cells, dict)
    margin = cells["margin_daily:twse"]
    assert isinstance(margin, dict)
    assert margin["2026-06-30"] == "failed"
    assert margin["2026-06-29"] == "none"


def test_matrix_missing_within_span(tmp_path: Path) -> None:
    # 上櫃三日齊全 → 交易日曆含 06-30；上市缺 06-30 → 該格 missing（真漏抓）。
    wh = Warehouse(tmp_path)
    wh.write(
        "clean",
        "prices_daily",
        _prices([(_D1, "2330", 100.0), (_D2, "2330", 100.0), (_D3, "2330", 100.0)]),
        source="tpex",
    )
    wh.write(
        "clean",
        "prices_daily",
        _prices([(_D1, "2330", 100.0), (_D3, "2330", 100.0)]),
        source="twse",
    )
    m = snap.ingest_matrix(wh)
    cells = m["cells"]
    assert isinstance(cells, dict)
    twse = cells["prices_daily:twse"]
    assert isinstance(twse, dict)
    assert twse["2026-06-30"] == "missing"
    assert twse["2026-07-01"] == "present"


# ── 漏抓清單（歸併 + 指令）────────────────────────────────────────────
def test_gaps_empty_on_clean_fixture(snapshot_wh: Warehouse) -> None:
    g = snap.ingest_gaps(snapshot_wh)
    assert g["gaps"] == []
    assert g["total_missing"] == 0


def test_gaps_failure_builds_ingest_command(snapshot_wh: Warehouse) -> None:
    record(snapshot_wh.root, "twse", "margin_daily", _D2, "failed", detail="TWSE 回空白")
    g = snap.ingest_gaps(snapshot_wh)
    gaps = g["gaps"]
    assert isinstance(gaps, list)
    margin = [x for x in gaps if x["table"] == "margin_daily"]
    assert len(margin) == 1
    run = margin[0]
    assert run["kind"] == "failed"
    assert run["from"] == "2026-06-30"
    assert run["days"] == 1
    assert run["command"] == "twquant ingest --date 2026-06-30 --tables margin_daily"
    assert "TWSE 回空白" in run["detail"]


def test_gaps_missing_run_builds_command(tmp_path: Path) -> None:
    wh = Warehouse(tmp_path)
    wh.write(
        "clean",
        "prices_daily",
        _prices([(_D1, "2330", 100.0), (_D2, "2330", 100.0), (_D3, "2330", 100.0)]),
        source="tpex",
    )
    wh.write(
        "clean",
        "prices_daily",
        _prices([(_D1, "2330", 100.0), (_D3, "2330", 100.0)]),
        source="twse",
    )
    g = snap.ingest_gaps(wh)
    gaps = g["gaps"]
    assert isinstance(gaps, list)
    twse = [x for x in gaps if x["source"] == "twse" and x["table"] == "prices_daily"]
    assert len(twse) == 1
    assert twse[0]["kind"] == "missing"
    assert twse[0]["from"] == "2026-06-30"
    assert twse[0]["to"] == "2026-06-30"
    assert twse[0]["command"] == "twquant ingest --date 2026-06-30 --tables prices_daily"


# ── 純輔助：指令 / ban 警示 / 月份 ────────────────────────────────────
def test_run_command_and_ban_warning() -> None:
    run = [_D1 + dt.timedelta(days=i) for i in range(25)]  # 25 日連續區段
    entry = snap._daily_run_entry("prices_daily", "twse", "上市行情", run, "missing", {})
    assert str(entry["command"]).startswith("twquant backfill --from")
    assert entry["warn_ban"] is True  # >20 日 TWSE 免費回補 → D-015 警示
    small = snap._daily_run_entry("prices_daily", "twse", "上市行情", run[:3], "missing", {})
    assert small["warn_ban"] is False
    single = snap._daily_run_entry("institutional_daily", "twse", "三大法人", [_D1], "missing", {})
    assert single["command"] == "twquant ingest --date 2026-06-29 --tables institutional_daily"


def test_month_iter_and_collapse() -> None:
    assert snap._month_iter("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]
    assert snap._collapse_months(["2025-11", "2025-12", "2026-03"]) == [
        ["2025-11", "2025-12"],
        ["2026-03"],
    ]


# ── API 端點（直接呼叫路由處理函數）──────────────────────────────────
def test_api_matrix_gaps_heartbeat(snapshot_wh: Warehouse) -> None:
    m = api.get_ingest_matrix(snapshot_wh)
    assert "datasets" in m
    assert "cells" in m
    g = api.get_ingest_gaps(snapshot_wh)
    assert g["gaps"] == []
    hb = api.get_heartbeat(snapshot_wh)
    assert hb["latest"] == "2026-06-30"


def test_api_matrix_bad_date(snapshot_wh: Warehouse) -> None:
    with pytest.raises(HTTPException) as ei:
        api.get_ingest_matrix(snapshot_wh, start="nope")
    assert ei.value.status_code == 400


# ── 一鍵補抓（D-018）：白名單 argv ────────────────────────────────────
def test_build_args_daily_single_and_range() -> None:
    assert jobmod.build_args("margin_daily", "twse", "2026-06-30", "2026-06-30") == [
        "ingest",
        "--date",
        "2026-06-30",
        "--tables",
        "margin_daily",
    ]
    # 區間補非行情表必須 --no-skip-existing：backfill 的續傳判斷以 prices_daily 分區
    # 為準，「行情已在、法人缺」的日子否則會被整天跳過。
    assert jobmod.build_args("institutional_daily", "twse", "2026-06-29", "2026-07-01") == [
        "backfill",
        "--from",
        "2026-06-29",
        "--to",
        "2026-07-01",
        "--tables",
        "institutional_daily",
        "--no-skip-existing",
    ]


def test_build_args_month_batched_tables() -> None:
    # 指數為月批：同月 → ingest-index；跨月 → backfill-index（以月為端點）。
    assert jobmod.build_args("index_daily", "tpex", "2026-06-05", "2026-06-30") == [
        "ingest-index",
        "--month",
        "2026-06",
    ]
    assert jobmod.build_args("index_daily", "twse", "2026-05-05", "2026-06-30") == [
        "backfill-index",
        "--from",
        "2026-05",
        "--to",
        "2026-06",
    ]
    # 月營收的 from/to 本來就是 YYYY-MM。
    assert jobmod.build_args("monthly_revenue", "mops", "2026-05", "2026-05") == [
        "ingest-revenue",
        "--month",
        "2026-05",
    ]
    assert jobmod.build_args("monthly_revenue", "mops", "2026-04", "2026-06") == [
        "backfill-revenue",
        "--from",
        "2026-04",
        "--to",
        "2026-06",
    ]


def test_build_args_market_holidays_one_click() -> None:
    # 官方開休市日曆：端點無年度參數，整年一抓 → ingest-holidays（from/to 忽略）。
    assert jobmod.build_args("market_holidays", "twse", "-", "-") == ["ingest-holidays"]
    assert jobmod.job_label("market_holidays", "twse", "-", "-") == "官方開休市日曆"


def test_build_args_rejects_bad_input() -> None:
    with pytest.raises(ValueError, match="白名單"):
        jobmod.build_args("prices_daily", "finmind", "2026-06-29", "2026-06-30")
    with pytest.raises(ValueError, match="白名單"):
        jobmod.build_args("prices_daily; rm -rf /", "twse", "2026-06-29", "2026-06-30")
    with pytest.raises(ValueError, match="日期格式"):
        jobmod.build_args("prices_daily", "twse", "nope", "2026-06-30")
    with pytest.raises(ValueError, match="早於"):
        jobmod.build_args("prices_daily", "twse", "2026-07-01", "2026-06-30")
    with pytest.raises(ValueError, match="月份格式"):
        jobmod.build_args("monthly_revenue", "mops", "2026-06-01", "2026-06-01")


def test_fetchable_tracks_whitelist() -> None:
    tracks = jobmod.fetchable_tracks()
    keys = {(t["table"], t["source"]) for t in tracks}
    # 每日行情軌與月營收都在可手動抓取清單。
    assert ("prices_daily", "twse") in keys
    assert ("prices_daily", "tpex") in keys
    assert ("monthly_revenue", "mops") in keys
    units = {(t["table"], t["source"]): t["unit"] for t in tracks}
    assert units[("prices_daily", "twse")] == "date"  # 日軌收 YYYY-MM-DD
    assert units[("monthly_revenue", "mops")] == "month"  # 月營收收 YYYY-MM
    # 清單即白名單：每個回傳的 (table, source) 都能通過 build_args（不 raise）。
    for t in tracks:
        span = "2026-05" if t["unit"] == "month" else "2026-06-30"
        jobmod.build_args(t["table"], t["source"], span, span)


def test_api_ingest_tracks_endpoint() -> None:
    payload = api.get_ingest_tracks()
    tracks = payload["tracks"]
    assert isinstance(tracks, list)
    assert tracks  # 非空
    assert all({"table", "source", "label", "unit"} <= set(t) for t in tracks)


def test_needs_force_empty_lake_uses_weekday_estimate(tmp_path: Path) -> None:
    # 空資料湖尚無 prices_daily / 交易日曆：needs_force 不得因無日曆而拋錯（否則手動
    # 抓取表單初次建置會 500）。單日 → 免 force；大區間（>20 平日）→ 仍套 D-015 護欄。
    wh = Warehouse(tmp_path)
    assert wh.latest_date("prices_daily") is None
    assert jobmod.needs_force(wh, "prices_daily", "twse", "2026-07-08", "2026-07-08") is False
    assert jobmod.needs_force(wh, "prices_daily", "twse", "2026-06-01", "2026-07-08") is True
    # 非行情表不套護欄（即便空湖）。
    assert (
        jobmod.needs_force(wh, "institutional_daily", "twse", "2026-06-01", "2026-07-08") is False
    )


def test_needs_force_only_for_large_twse_price_ranges(tmp_path: Path) -> None:
    wh = Warehouse(tmp_path)
    days: list[dt.date] = []
    d = _D1
    while len(days) < snap.TWSE_BACKFILL_WARN + 5:  # 25 個平日 → 交易日曆超過警戒
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    wh.write("clean", "prices_daily", _prices([(x, "2330", 100.0) for x in days]), source="tpex")
    a, b = days[0].isoformat(), days[-1].isoformat()
    assert jobmod.needs_force(wh, "prices_daily", "twse", a, b) is True
    assert jobmod.needs_force(wh, "prices_daily", "twse", a, days[3].isoformat()) is False
    assert jobmod.needs_force(wh, "institutional_daily", "twse", a, b) is False  # 護欄只針對行情
    # API 層：未帶 force → 400 並點名 D-015（護欄失效才會走到 start，故先斷開真實指令）。
    req = api.IngestJobRequest.model_validate(
        {"table": "prices_daily", "source": "twse", "from": a, "to": b}
    )
    with pytest.raises(HTTPException) as ei:
        api.post_ingest_job(req, wh)
    assert ei.value.status_code == 400
    assert "D-015" in str(ei.value.detail)


# ── 一鍵補抓（D-018）：工作管理與 API 端點 ────────────────────────────
def _wait_done(mgr: jobmod.JobManager, timeout: float = 30.0) -> dict[str, object]:
    """輪詢到最新工作離開 running（子行程結束由背景執行緒收斂）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        entries = mgr.jobs()
        if entries and entries[0]["status"] != "running":
            return entries[0]
        time.sleep(0.05)
    raise AssertionError("補抓工作逾時未結束")


def test_job_manager_success_records_tail() -> None:
    mgr = jobmod.JobManager()
    job = mgr.start("測試", "echo", [sys.executable, "-c", "print('hello 補抓')"])
    assert job["status"] == "running"
    done = _wait_done(mgr)
    assert done["status"] == "success"
    assert done["exit_code"] == 0
    tail = done["tail"]
    assert isinstance(tail, list)
    assert any("hello" in str(x) for x in tail)


def test_job_manager_failure_and_single_concurrency() -> None:
    mgr = jobmod.JobManager()
    mgr.start(
        "慢工作",
        "sleep",
        [sys.executable, "-c", "import time; time.sleep(2); raise SystemExit(3)"],
    )
    with pytest.raises(jobmod.JobBusyError):
        mgr.start("第二個", "echo", [sys.executable, "-c", "print('x')"])
    done = _wait_done(mgr)
    assert done["status"] == "failed"
    assert done["exit_code"] == 3
    assert mgr.running() is False


def test_api_post_rejects_non_whitelist(snapshot_wh: Warehouse) -> None:
    req = api.IngestJobRequest.model_validate(
        {"table": "nope", "source": "twse", "from": "2026-06-30", "to": "2026-06-30"}
    )
    with pytest.raises(HTTPException) as ei:
        api.post_ingest_job(req, snapshot_wh)
    assert ei.value.status_code == 400


def test_api_post_busy_returns_409(snapshot_wh: Warehouse, monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = jobmod.JobManager()
    monkeypatch.setattr(jobmod, "MANAGER", mgr)
    mgr.start("占用", "sleep", [sys.executable, "-c", "import time; time.sleep(2)"])
    req = api.IngestJobRequest.model_validate(
        {"table": "margin_daily", "source": "twse", "from": "2026-06-30", "to": "2026-06-30"}
    )
    with pytest.raises(HTTPException) as ei:
        api.post_ingest_job(req, snapshot_wh)
    assert ei.value.status_code == 409
    _wait_done(mgr)  # 收斂背景工作，避免洩漏到其他測試


def test_api_job_endpoints_list_and_get() -> None:
    mgr = jobmod.JobManager()
    started = mgr.start("查詢", "echo", [sys.executable, "-c", "print('q')"])
    jid = str(started["id"])
    listing = mgr.jobs()
    assert listing[0]["id"] == jid
    got = mgr.get(jid)
    assert got is not None
    assert mgr.get("j999") is None
    with pytest.raises(HTTPException) as ei:
        api.get_ingest_job("j999")
    assert ei.value.status_code == 404
    _wait_done(mgr)


# ── 手動補跑每日 pipeline（排程錯過時的介面補跑）─────────────────────────
def test_pipeline_argv_and_label() -> None:
    # 已解析交易日 → `pipeline --date <day>`（與 scheduler 16:40 同一指令）。
    assert jobmod.pipeline_args("2026-06-30") == ["pipeline", "--date", "2026-06-30"]
    assert jobmod.pipeline_label("2026-06-30") == "每日 pipeline 2026-06-30"
    assert jobmod.full_argv(jobmod.pipeline_args("2026-06-30")) == [
        sys.executable,
        "-m",
        "twquant",
        "pipeline",
        "--date",
        "2026-06-30",
    ]
    assert jobmod.display_command(jobmod.pipeline_args("2026-06-30")) == (
        "twquant pipeline --date 2026-06-30"
    )


def test_resolve_trading_day_default_today_and_validate() -> None:
    from twquant.conventions import now_tw

    today = now_tw().date().isoformat()
    # 省略／空字串 → 今日台北（不看伺服器本地時區）。
    assert jobmod.resolve_trading_day(None) == today
    assert jobmod.resolve_trading_day("") == today
    # 顯式合法日期原樣回傳；非法格式 → ValueError（API 轉 400）。
    assert jobmod.resolve_trading_day("2026-06-30") == "2026-06-30"
    with pytest.raises(ValueError, match="日期格式"):
        jobmod.resolve_trading_day("nope")


def test_api_post_pipeline_job_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    # 不真的 spawn（會打網路擷取）：以假 MANAGER 攔截 start，斷言 argv／label 正確。
    captured: dict[str, object] = {}

    class _FakeManager:
        def start(self, label: str, command: str, argv: list[str]) -> dict[str, object]:
            captured["label"] = label
            captured["command"] = command
            captured["argv"] = argv
            return {"id": "j1", "status": "running"}

    monkeypatch.setattr(jobmod, "MANAGER", _FakeManager())
    res = api.post_pipeline_job(api.PipelineJobRequest(date="2026-06-30"))
    assert res["id"] == "j1"
    assert captured["label"] == "每日 pipeline 2026-06-30"
    assert captured["command"] == "twquant pipeline --date 2026-06-30"
    assert captured["argv"] == [
        sys.executable,
        "-m",
        "twquant",
        "pipeline",
        "--date",
        "2026-06-30",
    ]


def test_api_post_pipeline_job_bad_date() -> None:
    with pytest.raises(HTTPException) as ei:
        api.post_pipeline_job(api.PipelineJobRequest(date="nope"))
    assert ei.value.status_code == 400


def test_api_post_pipeline_job_busy_409(monkeypatch: pytest.MonkeyPatch) -> None:
    # pipeline 與補抓共用單一併發：有工作進行中 → 409（pipeline 內含擷取，不得並跑）。
    mgr = jobmod.JobManager()
    monkeypatch.setattr(jobmod, "MANAGER", mgr)
    mgr.start("占用", "sleep", [sys.executable, "-c", "import time; time.sleep(2)"])
    with pytest.raises(HTTPException) as ei:
        api.post_pipeline_job(api.PipelineJobRequest(date="2026-06-30"))
    assert ei.value.status_code == 409
    _wait_done(mgr)  # 收斂背景工作，避免洩漏到其他測試
