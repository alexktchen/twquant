"""T-054 每日增量歸檔：dump_day / restore_day 對真 PG 的整合測試（無 DSN 則 skip）。

AC-1 連跑兩日產生兩個日檔目錄；AC-2 重跑同日冪等（資料檔 bit-identical 覆蓋）；
AC-3 單日檔可還原進空表（且重複還原冪等）。
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import os
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from twquant.conventions import TZ
from twquant.data import schemas

_SCHEMA = "serving_test_archive"
_SCHEMA_R = "serving_test_archive_r"


def _connect_or_skip() -> Any:
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        pytest.skip("TWQUANT_PG_DSN 未設，跳過 PG 整合測試")
    try:
        conn = psycopg.connect(dsn)
    except psycopg.Error as exc:  # pragma: no cover - 依環境而定
        pytest.skip(f"PG 不可達，跳過：{exc}")
    return conn


def _drop(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
        cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA_R}" CASCADE')
    conn.commit()


def _prices(date: dt.date, stock: str = "2330", close: float = 2410.0) -> pl.DataFrame:
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
                [dt.datetime(date.year, date.month, date.day, 18, 0, tzinfo=TZ)],
                dtype=schemas.TW_DT,
            ),
        }
    )


def _brokers(date: dt.date) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [date, date],
            "stock_id": ["2330", "2330"],
            "broker_id": ["甲券商", "乙券商"],
            "buy_shares": [5000, 0],
            "sell_shares": [0, 3000],
            "avg_price": pl.Series([None, None], dtype=pl.Float64),
            "available_at": pl.Series(
                [dt.datetime(date.year, date.month, date.day, 18, 0, tzinfo=TZ)] * 2,
                dtype=schemas.TW_DT,
            ),
        }
    )


def _meta() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "stock_id": ["2330"],
            "name": ["台積電"],
            "market": ["twse"],
            "industry": ["半導體"],
            "list_date": pl.Series([None], dtype=pl.Date),
            "delist_date": pl.Series([None], dtype=pl.Date),
        }
    )


def _seed(conn: Any, days: list[dt.date]) -> None:
    from twquant.data.serving import schema as sch
    from twquant.data.serving import sync as ssync

    sch.ensure_schema(conn, _SCHEMA)
    # upsert 的 staging temp 表 ON COMMIT DROP → 同表多日必須逐日 commit，否則撞名
    for d in days:
        with conn.cursor() as cur:
            ssync.upsert(cur, _SCHEMA, sch.spec_of("prices_daily"), _prices(d))
            ssync.upsert(cur, _SCHEMA, sch.spec_of("broker_trades"), _brokers(d))
        conn.commit()
    with conn.cursor() as cur:
        ssync.upsert(cur, _SCHEMA, sch.spec_of("stock_meta"), _meta())
    conn.commit()


def _read_gz(p: Path) -> bytes:
    with gzip.open(p, "rb") as f:
        return f.read()


def test_ac1_two_days_two_dirs(tmp_path: Path) -> None:
    conn = _connect_or_skip()
    from twquant.data.serving.archive import day_dir, dump_day

    d1, d2 = dt.date(2026, 6, 29), dt.date(2026, 6, 30)
    try:
        _drop(conn)
        _seed(conn, [d1, d2])
        dump_day(conn, _SCHEMA, d1, tmp_path)
        dump_day(conn, _SCHEMA, d2, tmp_path)
        for d in (d1, d2):
            out = day_dir(tmp_path, d)
            assert (out / "manifest.json").is_file()
            assert (out / "prices_daily.csv.gz").is_file()
            assert (out / "broker_trades.csv.gz").is_file()
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            assert manifest["day"] == d.isoformat()
            assert manifest["tables"]["prices_daily"] == 1
            assert manifest["tables"]["broker_trades"] == 2
            assert manifest["tables"]["stock_meta"] == 1  # snapshot 表每日全量
    finally:
        _drop(conn)
        conn.close()


def test_ac2_rerun_idempotent(tmp_path: Path) -> None:
    conn = _connect_or_skip()
    from twquant.data.serving.archive import day_dir, dump_day

    d = dt.date(2026, 6, 30)
    try:
        _drop(conn)
        _seed(conn, [d])
        dump_day(conn, _SCHEMA, d, tmp_path)
        out = day_dir(tmp_path, d)
        first = {p.name: p.read_bytes() for p in out.glob("*.csv.gz")}
        dump_day(conn, _SCHEMA, d, tmp_path)  # 重跑同日：覆蓋同檔
        second = {p.name: p.read_bytes() for p in out.glob("*.csv.gz")}
        assert first.keys() == second.keys()
        for name in first:  # gzip mtime 固定 → 整檔 bit-identical（黃金規則冪等）
            assert first[name] == second[name], f"{name} 重跑後位元組不一致"
    finally:
        _drop(conn)
        conn.close()


def test_ac3_restore_into_empty(tmp_path: Path) -> None:
    conn = _connect_or_skip()
    from twquant.data.serving.archive import dump_day, restore_day

    d = dt.date(2026, 6, 30)
    try:
        _drop(conn)
        _seed(conn, [d])
        dump_day(conn, _SCHEMA, d, tmp_path)

        restore_day(conn, _SCHEMA_R, d, tmp_path)  # 還原進空 schema（內部 ensure_schema）
        with conn.cursor() as cur:
            cur.execute(f'SELECT count(*) FROM "{_SCHEMA_R}".prices_daily')
            assert cur.fetchone() == (1,)
            cur.execute(f'SELECT count(*) FROM "{_SCHEMA_R}".broker_trades')
            assert cur.fetchone() == (2,)
            cur.execute(
                f'SELECT sum(buy_shares), sum(sell_shares) FROM "{_SCHEMA_R}".broker_trades'
            )
            assert cur.fetchone() == (5000, 3000)  # 內容抽查：買賣股數不失真
            cur.execute(
                f'SELECT available_at FROM "{_SCHEMA_R}".prices_daily WHERE stock_id = %s',
                ("2330",),
            )
            row = cur.fetchone()
            assert row is not None
            # timestamptz 走線為 UTC；轉回台北應是 18:00（時區不失真）
            assert row[0].astimezone(TZ).hour == 18

        restore_day(conn, _SCHEMA_R, d, tmp_path)  # 重複還原：ON CONFLICT 冪等
        with conn.cursor() as cur:
            cur.execute(f'SELECT count(*) FROM "{_SCHEMA_R}".broker_trades')
            assert cur.fetchone() == (2,)
    finally:
        _drop(conn)
        conn.close()
