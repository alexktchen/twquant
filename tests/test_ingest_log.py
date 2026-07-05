"""擷取狀態日誌 ingest_log 測試。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from twquant.reporting.ingest_log import (
    last_run,
    read_ingest_log,
    recent_failures,
    record,
)


def test_record_and_read(tmp_path: Path) -> None:
    record(tmp_path, "twse", "prices_daily", dt.date(2026, 6, 30), "success", rows=1361)
    record(tmp_path, "twse", "margin_daily", dt.date(2026, 2, 3), "failed", detail="回應非 JSON")
    log = read_ingest_log(tmp_path)
    assert log.height == 2
    ok = log.filter(log["status"] == "success").to_dicts()[0]
    assert ok["rows"] == 1361 and ok["date"] == "2026-06-30"


def test_recent_failures(tmp_path: Path) -> None:
    record(tmp_path, "twse", "prices_daily", dt.date(2026, 2, 3), "failed", detail="被擋")
    record(tmp_path, "tpex", "prices_daily", dt.date(2026, 6, 30), "success", rows=100)
    fails = recent_failures(tmp_path)
    assert fails.height == 1
    assert fails.to_dicts()[0]["detail"] == "被擋"


def test_last_run_and_empty(tmp_path: Path) -> None:
    assert last_run(tmp_path) is None
    assert read_ingest_log(tmp_path).is_empty()
    record(tmp_path, "mops_sii", "monthly_revenue", "2026-05", "success", rows=987)
    assert last_run(tmp_path) is not None
