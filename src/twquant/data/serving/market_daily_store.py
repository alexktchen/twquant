"""大盤體檢（T-374）的 PG 讀寫：讀算一天要的視窗、把一列 upsert 進 `market_daily`。

只在 CLI／pipeline 邊界呼叫；連線由呼叫端提供（沿 group_flow_store.py 慣例）。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict
from typing import Any

import polars as pl

from twquant.data.serving.schema import MARKET_DAILY_SPEC, ensure_tables
from twquant.data.serving.sync import upsert
from twquant.reporting.market_daily import MarketDaily


def _sessions(cur: Any, schema: str, day: dt.date, n: int) -> list[dt.date]:
    cur.execute(
        f'SELECT DISTINCT date FROM "{schema}"."prices_daily" WHERE date <= %s ORDER BY date DESC LIMIT %s',
        (day, n),
    )
    return [r[0] for r in cur.fetchall()]


def read_inputs(conn: Any, schema: str, day: dt.date, sessions: int) -> dict[str, pl.DataFrame]:
    """`day`（含）往前 `sessions` 個交易日的行情、指數、法人、融資。"""
    with conn.cursor() as cur:
        dates = _sessions(cur, schema, day, sessions)
        cur.execute(
            f'SELECT date, stock_id, close, amount, limit_up_price, limit_down_price FROM "{schema}"."prices_daily" '
            "WHERE date = ANY(%s)",
            (dates,),
        )
        prices = pl.DataFrame(
            cur.fetchall(),
            schema={
                "date": pl.Date,
                "stock_id": pl.String,
                "close": pl.Float64,
                "amount": pl.Float64,
                "limit_up_price": pl.Float64,
                "limit_down_price": pl.Float64,
            },
            orient="row",
        )
        cur.execute(
            f'SELECT date, index_id, close FROM "{schema}"."index_daily" WHERE date = ANY(%s)',
            (dates,),
        )
        index_rows = pl.DataFrame(
            cur.fetchall(),
            schema={"date": pl.Date, "index_id": pl.String, "close": pl.Float64},
            orient="row",
        )
        cur.execute(
            f'SELECT date, stock_id, foreign_net, trust_net, dealer_net FROM "{schema}"."institutional_daily" '
            "WHERE date = ANY(%s)",
            (dates,),
        )
        inst = pl.DataFrame(
            cur.fetchall(),
            schema={
                "date": pl.Date,
                "stock_id": pl.String,
                "foreign_net": pl.Float64,
                "trust_net": pl.Float64,
                "dealer_net": pl.Float64,
            },
            orient="row",
        )
        cur.execute(
            f'SELECT date, stock_id, margin_balance, short_balance FROM "{schema}"."margin_daily" '
            "WHERE date = ANY(%s)",
            (dates[:2],),
        )
        margin = pl.DataFrame(
            cur.fetchall(),
            schema={
                "date": pl.Date,
                "stock_id": pl.String,
                "margin_balance": pl.Float64,
                "short_balance": pl.Float64,
            },
            orient="row",
        )
    return {"prices": prices, "index_rows": index_rows, "institutional": inst, "margin": margin}


def write_market_daily(conn: Any, schema: str, row: MarketDaily, created_at: dt.datetime) -> None:
    """upsert 一列（重跑冪等）。"""
    if created_at.tzinfo is None:
        raise ValueError("created_at 必須帶時區")
    ensure_tables(conn, (MARKET_DAILY_SPEC,), schema)
    data = {**asdict(row), "created_at": created_at}
    df = pl.DataFrame([data]).select(list(MARKET_DAILY_SPEC.column_names))
    with conn.cursor() as cur:
        upsert(cur, schema, MARKET_DAILY_SPEC, df)
    conn.commit()
