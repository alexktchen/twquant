"""族群法人（D-177 修訂三）的 PG 讀寫：交易日、上市法人、收盤、已算過的族群法人，與整天覆蓋寫回 `group_inst_daily`。

只在 CLI／pipeline 邊界呼叫；連線由呼叫端提供（沿 group_flow_store.py 慣例）。
"""

from __future__ import annotations

import datetime as dt
import json
import math
from collections.abc import Sequence
from typing import Any

import polars as pl

from twquant.data.serving.schema import GROUP_INST_SPEC, ensure_tables
from twquant.errors import DataError
from twquant.reporting.group_inst import GroupInst, InstDay


def read_sessions(conn: Any, schema: str, day: dt.date, n: int) -> list[dt.date]:
    """`day`（含）往前 n 個有行情的交易日，舊到新。"""
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT DISTINCT date FROM "{schema}"."prices_daily" WHERE date <= %s '
            "ORDER BY date DESC LIMIT %s",
            (day, n),
        )
        return sorted(r[0] for r in cur.fetchall())


def read_closes(conn: Any, schema: str, day: dt.date) -> pl.DataFrame:
    """這天每檔的收盤。"""
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT stock_id, close FROM "{schema}"."prices_daily" WHERE date = %s', (day,)
        )
        rows = cur.fetchall()
    return pl.DataFrame(rows, schema={"stock_id": pl.String, "close": pl.Float64}, orient="row")


def read_twse_inst(conn: Any, schema: str, day: dt.date) -> pl.DataFrame:
    """這天的上市三大法人買賣超（股，`institutional_daily`）。"""
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT stock_id, foreign_net, trust_net, dealer_net FROM "{schema}"."institutional_daily" '
            "WHERE date = %s",
            (day,),
        )
        rows = cur.fetchall()
    return pl.DataFrame(
        rows,
        schema={
            "stock_id": pl.String,
            "foreign_net": pl.Int64,
            "trust_net": pl.Int64,
            "dealer_net": pl.Int64,
        },
        orient="row",
    )


def read_inst_days(conn: Any, schema: str, days: Sequence[dt.date]) -> list[InstDay]:
    """這些日子已經算過的族群法人（每天的部分）；表還沒建回空。"""
    ensure_tables(conn, (GROUP_INST_SPEC,), schema)
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT date, grp, members, foreign_amt, trust_amt, dealer_amt FROM "{schema}"."{GROUP_INST_SPEC.name}" '
            "WHERE date = ANY(%s)",
            (list(days),),
        )
        rows = cur.fetchall()
    return [InstDay(d, g, int(m), float(f), float(t), float(x)) for d, g, m, f, t, x in rows]


def write_inst(
    conn: Any, schema: str, points: Sequence[GroupInst], created_at: dt.datetime
) -> None:
    """整天覆蓋 points 涵蓋的每一天（重跑冪等）。NaN／無限大 raise（網站讀不出來）。"""
    if created_at.tzinfo is None:
        raise ValueError("created_at 必須帶時區")
    for p in points:
        nums = (
            p.day.foreign,
            p.day.trust,
            p.day.dealer,
            p.net5,
            p.accel,
            p.foreign5,
            p.trust5,
            p.dealer5,
            *(v for _, x, y in p.trail for v in (x, y)),
        )
        if any(v is not None and not math.isfinite(v) for v in nums):
            raise DataError(
                GROUP_INST_SPEC.name, p.day.date, f"族群「{p.day.group}」有 NaN／無限大：{nums}"
            )
    ensure_tables(conn, (GROUP_INST_SPEC,), schema)
    table = f'"{schema}"."{GROUP_INST_SPEC.name}"'
    rows = [
        (
            p.day.date,
            p.day.group,
            p.day.members,
            p.day.net,
            p.day.foreign,
            p.day.trust,
            p.day.dealer,
            p.net5,
            p.accel,
            p.foreign5,
            p.trust5,
            p.dealer5,
            json.dumps(
                [{"d": d.isoformat(), "x": x, "y": y} for d, x, y in p.trail], allow_nan=False
            ),
            created_at,
        )
        for p in points
    ]
    days = sorted({p.day.date for p in points})
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {table} WHERE date = ANY(%s)", (days,))
        cur.executemany(
            f"INSERT INTO {table} (date, grp, members, net, foreign_amt, trust_amt, dealer_amt, net5, accel, "
            "foreign5, trust5, dealer5, trail, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)",
            rows,
        )
    conn.commit()
