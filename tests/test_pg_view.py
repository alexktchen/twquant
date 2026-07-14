"""D-026：PgTableSource 結構滿足 TableSource、餵給 PitView 後 PIT 截點行為與 parquet
路徑一致（available_at 時區正規化是本測試要守住的關鍵）。需真 PG，無 TWQUANT_PG_DSN 則 skip。
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Any

import polars as pl
import pytest

from twquant.conventions import TZ
from twquant.data import schemas
from twquant.errors import DataError

_SCHEMA = "serving_test_pgview"


def _connect_or_skip() -> Any:
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        pytest.skip("TWQUANT_PG_DSN 未設，跳過 PG 整合測試")
    try:
        conn = psycopg.connect(dsn)
    except psycopg.Error as exc:  # pragma: no cover - 依環境而定
        pytest.skip(f"PG 不可達，跳過：{exc}")
    # 不開 autocommit：upsert() 的 staging temp table 是 ON COMMIT DROP，
    # autocommit 會讓每個語句各自提交、COPY 前 staging 表就被砍掉。
    return conn


def _drop(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
    conn.commit()


def _prices(date: dt.date, hour: int, stock: str = "2330", close: float = 2410.0) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [date],
            "stock_id": [stock],
            "open": [close],
            "high": [close],
            "low": [close],
            "close": [close],
            "volume": [1000],
            "amount": [close * 1000],
            "limit_up_price": pl.Series([None], dtype=pl.Float64),
            "limit_down_price": pl.Series([None], dtype=pl.Float64),
            "available_at": pl.Series(
                [dt.datetime(date.year, date.month, date.day, hour, 0, tzinfo=TZ)],
                dtype=schemas.TW_DT,
            ),
        }
    )


def test_pg_view_pit_cutoff_and_missing_table() -> None:
    conn = _connect_or_skip()
    from twquant.data.serving import schema as sch
    from twquant.data.serving import sync as ssync
    from twquant.data.serving.pg_view import PgTableSource

    try:
        _drop(conn)
        sch.ensure_schema(conn, _SCHEMA)
        d = dt.date(2026, 6, 30)
        with conn.cursor() as cur:
            # 18:00 可見、21:00（融資截點外）不可見，比照 test_pit_cutoff_d008（parquet 版）。
            ssync.upsert(cur, _SCHEMA, sch.spec_of("prices_daily"), _prices(d, 18))
            margin = pl.DataFrame(
                {
                    "date": [d],
                    "stock_id": ["2330"],
                    "margin_balance": [1000],
                    "short_balance": [0],
                    "available_at": pl.Series(
                        [dt.datetime(2026, 6, 30, 21, 0, tzinfo=TZ)], dtype=schemas.TW_DT
                    ),
                }
            )
            ssync.upsert(cur, _SCHEMA, sch.spec_of("margin_daily"), margin)
        conn.commit()

        source = PgTableSource(conn, _SCHEMA)
        view = source.view(d)  # cutoff = 20:00（D-008）
        assert view.table("prices_daily").collect().height == 1  # 18:00 <= 20:00
        assert view.table("margin_daily").collect().height == 0  # 21:00 > 20:00

        view_next = source.view(dt.date(2026, 7, 1))  # cutoff 次日 20:00
        assert view_next.table("margin_daily").collect().height == 1

        assert source.latest_date("prices_daily") == d
        assert source.dates_in_range("prices_daily", d, d) == [d]
        assert source.dates_in_range("prices_daily", dt.date(2026, 7, 1), dt.date(2026, 7, 2)) == []

        with pytest.raises(DataError, match="無此表"):
            source.scan("no_such_table")
    finally:
        _drop(conn)
        conn.close()
