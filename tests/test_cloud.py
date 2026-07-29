"""T-055 雲端滾動窗口：prune / broker_flow 聚合 / seed 的整合測試（無 DSN 則 skip）。

AC-窗口：configs 滾動窗 ≥ 因子最大回看（靜態，不需 PG）。
AC-prune：修剪到 N 個交易日、重跑冪等。
AC-seed：window ＋四碼過濾正確落到目標 schema。
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Any

import polars as pl
import pytest

from twquant.conventions import TZ
from twquant.data import schemas

_SRC = "serving_test_cloud_src"
_DST = "serving_test_cloud_dst"


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
        cur.execute(f'DROP SCHEMA IF EXISTS "{_SRC}" CASCADE')
        cur.execute(f'DROP SCHEMA IF EXISTS "{_DST}" CASCADE')
    conn.commit()


def _prices(date: dt.date, stock: str) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [date],
            "stock_id": [stock],
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.5],
            "volume": [1000],
            "amount": [100500.0],
            "limit_up_price": pl.Series([None], dtype=pl.Float64),
            "limit_down_price": pl.Series([None], dtype=pl.Float64),
            "available_at": pl.Series(
                [dt.datetime(date.year, date.month, date.day, 18, 0, tzinfo=TZ)],
                dtype=schemas.TW_DT,
            ),
        }
    )


def _brokers(date: dt.date, stock: str = "2330") -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [date, date],
            "stock_id": [stock, stock],
            "broker_id": ["甲", "乙"],
            "buy_shares": [7000, 1000],
            "sell_shares": [2000, 4000],
            "avg_price": pl.Series([None, None], dtype=pl.Float64),
            "available_at": pl.Series(
                [dt.datetime(date.year, date.month, date.day, 18, 0, tzinfo=TZ)] * 2,
                dtype=schemas.TW_DT,
            ),
        }
    )


def _seed_prices(conn: Any, schema: str, dates: list[dt.date], stocks: list[str]) -> None:
    from twquant.data.serving import schema as sch
    from twquant.data.serving import sync as ssync

    sch.ensure_schema(conn, schema)
    # 同表同交易只能 upsert 一次（staging temp 撞名）→ 每日把全部股票併成一個 df
    for d in dates:
        df = pl.concat([_prices(d, s) for s in stocks])
        with conn.cursor() as cur:
            ssync.upsert(cur, schema, sch.spec_of("prices_daily"), df)
        conn.commit()


def test_ac_windows_cover_max_factor_lookback() -> None:
    """滾動窗（configs/serving.yaml cloud.windows）≥ 吃該表的因子最大回看。"""
    from pathlib import Path

    import twquant.factors.builtins  # noqa: F401  # 副作用註冊全部因子
    from twquant.config import ServingConfig, load_config
    from twquant.factors import registry

    cfg = load_config(Path("configs/serving.yaml"), ServingConfig)
    win = cfg.cloud.windows
    assert "prices_daily" in win and "factor_values" in win
    for name in registry.list_factors():
        fdef = registry.get(name)
        tables = set(fdef.tables)
        if not tables <= set(win):
            # 混用「未修剪表」（如 monthly_revenue 全史）的因子：lookback 由未修剪表驅動，
            # 修剪表僅作日頻 spine（fundamental.py join_asof），窗口不受其約束——人工審查已確認。
            continue
        for table in tables:
            assert fdef.lookback_days <= win[table], (
                f"{name} 回看 {fdef.lookback_days} 超過 {table} 窗 {win[table]}"
            )


def test_ac_prune_window_and_idempotent() -> None:
    conn = _connect_or_skip()
    from twquant.data.serving.cloud import prune

    days = [dt.date(2026, 6, d) for d in (22, 23, 24, 25, 26)]
    try:
        _drop(conn)
        _seed_prices(conn, _SRC, days, ["2330"])
        results = prune(conn, _SRC, {"prices_daily": 3})
        assert results[0].deleted == 2  # 5 個交易日 → 留最新 3 日
        with conn.cursor() as cur:
            cur.execute(f'SELECT min(date), count(DISTINCT date) FROM "{_SRC}".prices_daily')
            assert cur.fetchone() == (dt.date(2026, 6, 24), 3)
        again = prune(conn, _SRC, {"prices_daily": 3})
        assert again[0].deleted == 0  # 冪等
        wide = prune(conn, _SRC, {"prices_daily": 10})  # 窗大於現有日數 → 不刪
        assert wide[0].deleted == 0
    finally:
        _drop(conn)
        conn.close()


def test_broker_flow_aggregation_idempotent() -> None:
    conn = _connect_or_skip()
    from twquant.data.serving import schema as sch
    from twquant.data.serving import sync as ssync
    from twquant.data.serving.cloud import refresh_broker_flow

    d = dt.date(2026, 6, 30)
    try:
        _drop(conn)
        sch.ensure_schema(conn, _SRC)
        with conn.cursor() as cur:
            ssync.upsert(cur, _SRC, sch.spec_of("broker_trades"), _brokers(d))
        conn.commit()
        n = refresh_broker_flow(conn, _SRC, [d])
        assert n == 1  # 一檔一日一列
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT buy_shares, sell_shares, net_shares, n_brokers "
                f'FROM "{_SRC}".broker_flow_daily WHERE stock_id = %s AND date = %s',
                ("2330", d),
            )
            assert cur.fetchone() == (8000, 6000, 2000, 2)
        assert refresh_broker_flow(conn, _SRC, [d]) == 1  # 重跑冪等（DO UPDATE）
    finally:
        _drop(conn)
        conn.close()


def test_ac_seed_window_and_stock_pattern() -> None:
    conn = _connect_or_skip()
    dst_conn = _connect_or_skip()  # COPY OUT 與 COPY IN 不能共用同一條連線
    from twquant.data.serving.cloud import seed_tables

    days = [dt.date(2026, 6, d) for d in (24, 25, 26)]
    try:
        _drop(conn)
        # 四碼普通股 2330 ＋ ETF 0050：pattern 過濾後者
        _seed_prices(conn, _SRC, days, ["2330", "0050"])
        results = seed_tables(
            conn,
            dst_conn,
            src_schema=_SRC,
            dst_schema=_DST,
            tables=["prices_daily"],
            windows={"prices_daily": 2},
            stock_pattern="^[1-9][0-9]{3}$",
            pattern_tables=("prices_daily",),
        )
        assert results[0].rows == 2  # 2 日 × 1 檔（0050 被過濾）
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT count(*), min(date), count(DISTINCT stock_id) FROM "{_DST}".prices_daily'
            )
            assert cur.fetchone() == (2, dt.date(2026, 6, 25), 1)
            cur.execute(f'SELECT DISTINCT stock_id FROM "{_DST}".prices_daily')
            assert cur.fetchall() == [("2330",)]
    finally:
        _drop(conn)
        conn.close()
        dst_conn.close()
