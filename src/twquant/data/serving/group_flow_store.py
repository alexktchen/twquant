"""族群資金（D-177 修訂）的 PG 讀寫：讀算資金倍數要的行情窗口、整天覆蓋寫回 `group_flow_daily`。

只在 CLI／pipeline 邊界呼叫；連線由呼叫端提供（沿 scorecard_store.py 慣例）。
"""

from __future__ import annotations

import datetime as dt
import json
import math
from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

import polars as pl

from twquant.data.serving.schema import GROUP_FLOW_SPEC, ensure_tables
from twquant.errors import DataError
from twquant.reporting.group_flow import GroupFlow


def read_price_window(conn: Any, schema: str, day: dt.date, sessions: int) -> pl.DataFrame:
    """`day`（含）往前 `sessions` 個有行情的交易日，每檔的收盤與成交金額。"""
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT DISTINCT date FROM "{schema}"."prices_daily" WHERE date <= %s '
            "ORDER BY date DESC LIMIT %s",
            (day, sessions),
        )
        dates = [r[0] for r in cur.fetchall()]
        cur.execute(
            f'SELECT date, stock_id, close, amount FROM "{schema}"."prices_daily" WHERE date = ANY(%s)',
            (dates,),
        )
        rows = cur.fetchall()
    return pl.DataFrame(
        rows,
        schema={"date": pl.Date, "stock_id": pl.String, "close": pl.Float64, "amount": pl.Float64},
        orient="row",
    )


def write_group_flow(
    conn: Any, schema: str, day: dt.date, flows: Sequence[GroupFlow], created_at: dt.datetime
) -> None:
    """整天覆蓋這天的族群資金（重跑冪等）。一個族群都沒有即 raise；NaN／無限大 raise（網站讀不出來）。"""
    if created_at.tzinfo is None:
        raise ValueError("created_at 必須帶時區")
    if not flows:
        raise DataError(GROUP_FLOW_SPEC.name, day, "算出 0 個族群——族群表或行情有問題，不覆蓋")
    if any(f.date != day for f in flows):
        raise ValueError(f"族群資金的日期不是 {day}")
    for f in flows:
        nums = (f.ret, f.amount, f.share, f.share_avg, f.ratio)
        if any(x is not None and not math.isfinite(x) for x in nums):
            raise DataError(GROUP_FLOW_SPEC.name, day, f"族群「{f.group}」有 NaN／無限大：{nums}")
    ensure_tables(conn, (GROUP_FLOW_SPEC,), schema)
    table = f'"{schema}"."{GROUP_FLOW_SPEC.name}"'
    rows = [
        (
            f.date,
            f.group,
            f.n,
            f.up,
            f.down,
            f.ret,
            f.amount,
            f.share,
            f.share_avg,
            f.ratio,
            f.hot,
            json.dumps([asdict(m) for m in f.members], ensure_ascii=False, allow_nan=False),
            created_at,
        )
        for f in flows
    ]
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {table} WHERE date = %s", (day,))
        cur.executemany(
            f"INSERT INTO {table} (date, grp, n, up, down, ret, amount, share, share_avg, ratio, hot, "
            "members, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)",
            rows,
        )
    conn.commit()
