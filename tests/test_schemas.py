"""T-004 / SPEC-001 §1：資料合約 pandera schema。"""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from twquant.conventions import TZ
from twquant.data import schemas
from twquant.errors import QualityGateError

_AT = dt.datetime(2026, 7, 3, 18, 0, tzinfo=TZ)


def _prices_row(volume: object = 1_000_000) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [dt.date(2026, 7, 3)],
            "stock_id": ["2330"],
            "open": [1000.0],
            "high": [1010.0],
            "low": [995.0],
            "close": [1005.0],
            "volume": [volume],
            "amount": [1.005e9],
            "limit_up_price": [1100.0],
            "limit_down_price": [900.0],
            "available_at": pl.Series([_AT], dtype=schemas.TW_DT),
        }
    )


def test_prices_daily_valid() -> None:
    out = schemas.validate("prices_daily", _prices_row())
    assert out.shape[0] == 1


def test_ac3_volume_must_be_int_shares() -> None:
    # 成交量單位是股（int64）；傳 float 應被 schema 擋下（REVIEW/SPEC-000 單位）
    bad = _prices_row(volume=1000.5)
    with pytest.raises(QualityGateError, match="prices_daily"):
        schemas.validate("prices_daily", bad)


def test_strict_rejects_extra_column() -> None:
    df = _prices_row().with_columns(pl.lit(1).alias("bogus"))
    with pytest.raises(QualityGateError):
        schemas.validate("prices_daily", df)


def test_available_at_must_be_tz_aware() -> None:
    naive = dt.datetime(2026, 7, 3, 18, 0)  # naive
    df = _prices_row().with_columns(pl.Series("available_at", [naive], dtype=pl.Datetime("us")))
    with pytest.raises(QualityGateError):
        schemas.validate("prices_daily", df)


def test_negative_price_rejected() -> None:
    df = _prices_row().with_columns(pl.lit(-1.0).alias("close"))
    with pytest.raises(QualityGateError):
        schemas.validate("prices_daily", df)


def test_unknown_table_raises() -> None:
    with pytest.raises(QualityGateError, match="未知資料表"):
        schemas.validate("nope", pl.DataFrame())


def test_all_tables_registered_with_version() -> None:
    # 17 張 clean 層表都要有 schema 與版本（含 index_daily、market_holidays、
    # ml_scores——T-068/SPEC-010 §5 新增）
    assert len(schemas.SCHEMAS) == 17
    for name in schemas.SCHEMAS:
        assert schemas.schema_version(name) >= 1


def test_monthly_revenue_valid() -> None:
    df = pl.DataFrame(
        {
            "date": [dt.date(2026, 6, 30)],
            "year_month": ["2026-06"],
            "stock_id": ["2330"],
            "period_end": [dt.date(2026, 6, 30)],
            "revenue": [2.5e11],
            "yoy": [0.15],
            "available_at": pl.Series([_AT], dtype=schemas.TW_DT),
            "available_at_estimated": [False],
        }
    )
    assert schemas.validate("monthly_revenue", df).shape[0] == 1
