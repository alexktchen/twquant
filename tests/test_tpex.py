"""T-008 / SPEC-001：TPEX 上櫃轉接器與清洗（離線 fixture）。"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import polars as pl
import pytest

from twquant.config import SourceRate
from twquant.conventions import TZ
from twquant.data.clean import clean_tpex_prices
from twquant.data.http import HttpClient
from twquant.data.sources.tpex import TpexSource
from twquant.errors import DataError

_FIX = Path(__file__).resolve().parent / "fixtures" / "http" / "tpex"
_DATE = dt.date(2026, 6, 30)
_RATE = SourceRate(max_requests=3, per_seconds=5.0)


@dataclass
class FakeResponse:
    status_code: int
    text: str
    content: bytes = b""


def _source(text: str) -> TpexSource:
    def transport(method: str, url: str, *, params: object, timeout: float) -> FakeResponse:
        return FakeResponse(200, text)

    return TpexSource(client=HttpClient(source="tpex", rate=_RATE, transport=transport))


def _fixture_source() -> TpexSource:
    return _source((_FIX / "prices_daily.json").read_text(encoding="utf-8"))


def test_tpex_prices_parses() -> None:
    df = _fixture_source().fetch("prices_daily", _DATE)
    assert "代號" in df.columns
    row = df.filter(df["代號"] == "5483").to_dicts()[0]
    assert row["收盤"] == "180.00"


def test_tpex_clean_prices() -> None:
    raw = _fixture_source().fetch("prices_daily", _DATE)
    clean = clean_tpex_prices(raw, _DATE)
    assert clean.schema["close"] == pl.Float64
    row = clean.filter(pl.col("stock_id") == "5483").to_dicts()[0]
    assert row["close"] == 180.0
    assert row["open"] == 169.5
    assert row["available_at"] == dt.datetime(2026, 6, 30, 18, 0, tzinfo=TZ)


def test_tpex_unsupported_table_raises() -> None:
    # D-189：上櫃法人與融資改由櫃買新網站每日抓（tests/test_tpex_chips.py），不再丟「走 FinMind」；不支援的表照樣 raise
    src = _source("{}")
    assert {"prices_daily", "stock_valuation", "institutional_daily", "margin_daily"} <= set(
        src.tables
    )
    with pytest.raises(DataError, match="tpex 只支援"):
        src.fetch("broker_trades", _DATE)


def test_tpex_antibot_non_json_raises() -> None:
    with pytest.raises(DataError, match="anti-bot"):
        _source("<html>error</html>").fetch("prices_daily", _DATE)
