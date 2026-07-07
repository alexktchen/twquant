"""SPEC-007：擷取後台（monitoring.snapshot 的 ingest_matrix / ingest_gaps）測試。

覆蓋涵蓋矩陣的四態分類（present/missing/failed/none）、以交易日曆判真缺口（假日不算）、
失敗紀錄浮現、連續區段歸併、建議補抓指令與 D-015 ban 警示。純顯示查詢 + append-only
ingest_log，無需完整策略配置。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest
from fastapi import HTTPException

from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.monitoring import api
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
