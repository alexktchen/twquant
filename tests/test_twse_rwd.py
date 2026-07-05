"""T-007 / SPEC-001：TWSE rwd 轉接器（離線，用錄好的 HTTP fixture）。

fixture 由實盤抓取 2026-06-30 精簡而來（tests/fixtures/http/twse_rwd/）。
測試不連網（CLAUDE.md §4）：以注入 transport 回放 fixture。
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from twquant.config import SourceRate
from twquant.data.http import HttpClient
from twquant.data.sources.twse_rwd import TwseRwdSource
from twquant.errors import DataError

_FIX = Path(__file__).resolve().parent / "fixtures" / "http" / "twse_rwd"
_DATE = dt.date(2026, 6, 30)
_RATE = SourceRate(max_requests=3, per_seconds=5.0)


@dataclass
class FakeResponse:
    status_code: int
    text: str
    content: bytes = b""


def _source_returning(text: str) -> TwseRwdSource:
    def transport(method: str, url: str, *, params: object, timeout: float) -> FakeResponse:
        return FakeResponse(200, text)

    return TwseRwdSource(client=HttpClient(source="twse_rwd", rate=_RATE, transport=transport))


def _source_for(table: str) -> TwseRwdSource:
    return _source_returning((_FIX / f"{table}.json").read_text(encoding="utf-8"))


def test_prices_daily_parses_tsmc() -> None:
    df = _source_for("prices_daily").fetch("prices_daily", _DATE)
    assert df.columns[0] == "證券代號"
    row = df.filter(df["證券代號"] == "2330").to_dicts()[0]
    assert row["收盤價"] == "2,410.00"
    assert row["成交股數"] == "49,540,227"


def test_institutional_daily_parses_tsmc() -> None:
    df = _source_for("institutional_daily").fetch("institutional_daily", _DATE)
    row = df.filter(df["證券代號"] == "2330").to_dicts()[0]
    assert row["投信買賣超股數"] == "-89,595"
    assert row["三大法人買賣超股數"] == "-862,914"


def test_margin_daily_dedupes_columns() -> None:
    df = _source_for("margin_daily").fetch("margin_daily", _DATE)
    # 資/券同名欄位應被去重（買進 / 買進_2 …）
    assert "買進" in df.columns
    assert "買進_2" in df.columns
    row = df.filter(df["代號"] == "2330").to_dicts()[0]
    assert row["今日餘額"] == "33,305"  # 融資
    assert row["今日餘額_2"] == "22"  # 融券


def test_raw_all_string_no_transform() -> None:
    df = _source_for("prices_daily").fetch("prices_daily", _DATE)
    # 原始值：全字串、不轉型、保留千分位逗號（清洗是 T-011 的事）
    assert all(str(t) == "String" for t in df.dtypes)


def test_holiday_stat_raises() -> None:
    payload = json.dumps({"stat": "很抱歉，沒有符合條件的資料!"})
    with pytest.raises(DataError, match="非交易日或無資料"):
        _source_returning(payload).fetch("prices_daily", _DATE)


def test_empty_data_raises() -> None:
    payload = json.dumps({"stat": "OK", "fields": ["證券代號"], "data": []})
    with pytest.raises(DataError, match="空"):
        _source_returning(payload).fetch("institutional_daily", _DATE)


def test_unsupported_table_raises() -> None:
    with pytest.raises(DataError, match="不支援"):
        _source_returning("{}").fetch("broker_trades", _DATE)


def test_source_metadata() -> None:
    src = _source_returning("{}")
    assert src.name == "twse_rwd"
    assert set(src.tables) == {"prices_daily", "institutional_daily", "margin_daily"}
