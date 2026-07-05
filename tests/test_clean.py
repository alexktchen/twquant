"""T-011 / SPEC-001 §3：清洗管線。"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from twquant.config import SourceRate
from twquant.conventions import SHARES_PER_LOT, TZ
from twquant.data.clean import clean_institutional, clean_margin, clean_prices
from twquant.data.http import HttpClient
from twquant.data.sources.twse_rwd import TwseRwdSource

_FIX = Path(__file__).resolve().parent / "fixtures" / "http" / "twse_rwd"
_DATE = dt.date(2026, 6, 30)


@dataclass
class FakeResponse:
    status_code: int
    text: str
    content: bytes = b""


def _raw(table: str) -> pl.DataFrame:
    text = (_FIX / f"{table}.json").read_text(encoding="utf-8")

    def transport(method: str, url: str, *, params: object, timeout: float) -> FakeResponse:
        return FakeResponse(200, text)

    src = TwseRwdSource(
        client=HttpClient(
            source="twse_rwd", rate=SourceRate(max_requests=3, per_seconds=5.0), transport=transport
        )
    )
    return src.fetch(table, _DATE)


def test_clean_prices_types_and_values() -> None:
    df = clean_prices(_raw("prices_daily"), _DATE)
    assert df.schema["close"] == pl.Float64
    assert df.schema["volume"] == pl.Int64
    row = df.filter(pl.col("stock_id") == "2330").to_dicts()[0]
    assert row["close"] == 2410.0
    assert row["volume"] == 49_540_227
    assert row["available_at"] == dt.datetime(2026, 6, 30, 18, 0, tzinfo=TZ)


def test_clean_institutional_foreign_sum_and_trust() -> None:
    df = clean_institutional(_raw("institutional_daily"), _DATE)
    row = df.filter(pl.col("stock_id") == "2330").to_dicts()[0]
    assert row["trust_net"] == -89_595
    # 外資 = 外陸資(不含自營) + 外資自營商 = -1,425,774 + 0
    assert row["foreign_net"] == -1_425_774


def test_clean_margin_lots_to_shares() -> None:
    df = clean_margin(_raw("margin_daily"), _DATE)
    row = df.filter(pl.col("stock_id") == "2330").to_dicts()[0]
    assert row["margin_balance"] == 33_305 * SHARES_PER_LOT  # 張→股
    assert row["short_balance"] == 22 * SHARES_PER_LOT
    assert row["available_at"] == dt.datetime(2026, 6, 30, 21, 0, tzinfo=TZ)
