"""每日名單（T-332／D-170）的 PG 讀寫：每條線每天一份前 n 名，永久保留。

只在 CLI／pipeline 邊界呼叫；連線由呼叫端提供（沿 cloud.py 慣例）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Any

from twquant.data.serving.schema import DAILY_PICKS_SPEC, ensure_tables


def replace_line(
    conn: Any,
    schema: str,
    day: dt.date,
    line: str,
    picks: Sequence[tuple[str, float | None]],
    model_id: str | None,
    created_at: dt.datetime,
) -> int:
    """把 `day` 這條線的名單整份換成 `picks`（依序即名次）。重跑同日＝覆蓋，冪等。

    `picks` 為空 raise：空名單代表上游沒算出來，寫進去會讓「那天沒推薦」與「那天壞了」無法分辨。
    """
    if not picks:
        raise ValueError(f"{day} {line}：名單是空的，拒絕寫入")
    ids = [sid for sid, _ in picks]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{day} {line}：名單有重複代號 {ids}")
    if created_at.tzinfo is None:
        raise ValueError("created_at 必須帶時區")
    ensure_tables(conn, (DAILY_PICKS_SPEC,), schema)
    table = f'"{schema}"."{DAILY_PICKS_SPEC.name}"'
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {table} WHERE date = %s AND line = %s", (day, line))
        cur.executemany(
            f"INSERT INTO {table} (date, line, rank, stock_id, score, model_id, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [
                (day, line, rank, sid, score, model_id, created_at)
                for rank, (sid, score) in enumerate(picks, start=1)
            ],
        )
    conn.commit()
    return len(picks)


def read_line(
    conn: Any, schema: str, day: dt.date, line: str
) -> list[tuple[int, str, float | None]]:
    """讀回某日某線的名單 (名次, 代號, 分數)，依名次排序。"""
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT rank, stock_id, score FROM "{schema}"."{DAILY_PICKS_SPEC.name}" '
            "WHERE date = %s AND line = %s ORDER BY rank",
            (day, line),
        )
        return [
            (int(r[0]), str(r[1]), None if r[2] is None else float(r[2])) for r in cur.fetchall()
        ]
