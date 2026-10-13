"""名單績效（D-176）的 PG 讀寫：讀公布的每日名單（含寫入時間），寫回每條線一份名單績效 JSON。

只在 CLI／pipeline 邊界呼叫；連線由呼叫端提供（沿 picks_store.py 慣例）。
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any

from twquant.data.serving.schema import DAILY_PICKS_SPEC, SCORECARD_SPEC, ensure_tables


@dataclass(frozen=True)
class PublishedPick:
    """`daily_picks` 的一列：哪天、哪條線、第幾名、哪檔，以及它什麼時候寫進資料庫。"""

    date: dt.date
    line: str
    rank: int
    stock_id: str
    created_at: dt.datetime


def read_published(conn: Any, schema: str, start: dt.date, end: dt.date) -> list[PublishedPick]:
    """[start, end] 每一份公布的名單，依日期、線別、名次排序。"""
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT date, line, rank, stock_id, created_at FROM "{schema}"."{DAILY_PICKS_SPEC.name}" '
            "WHERE date BETWEEN %s AND %s ORDER BY date, line, rank",
            (start, end),
        )
        return [PublishedPick(r[0], str(r[1]), int(r[2]), str(r[3]), r[4]) for r in cur.fetchall()]


def write_scorecard(
    conn: Any,
    schema: str,
    line: str,
    as_of: dt.date,
    payload: dict[str, Any],
    created_at: dt.datetime,
) -> None:
    """整列覆蓋這條線的名單績效（每天從頭重算，冪等）。NaN／無限大 raise：網站讀不出來，不是 0。"""
    if created_at.tzinfo is None:
        raise ValueError("created_at 必須帶時區")
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    ensure_tables(conn, (SCORECARD_SPEC,), schema)
    with conn.cursor() as cur:
        cur.execute(
            f'INSERT INTO "{schema}"."{SCORECARD_SPEC.name}" (line, as_of, payload, created_at) '
            "VALUES (%s, %s, %s::jsonb, %s) ON CONFLICT (line) DO UPDATE SET "
            "as_of = EXCLUDED.as_of, payload = EXCLUDED.payload, created_at = EXCLUDED.created_at",
            (line, as_of, body, created_at),
        )
    conn.commit()
