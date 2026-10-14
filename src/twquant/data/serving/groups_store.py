"""族群標籤（D-177）的 PG 讀寫：讀官方產業別、看族群表多久沒更新、整份換掉族群表。

只在 CLI／pipeline 邊界呼叫；連線由呼叫端提供（沿 scorecard_store.py 慣例）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Any

from twquant.data.serving.schema import STOCK_GROUP_SPEC, ensure_tables
from twquant.data.stock_groups import StockGroup
from twquant.errors import DataError


def read_industries(conn: Any, schema: str) -> dict[str, str | None]:
    """stock_meta 每檔的官方產業別（沒有＝None）。"""
    with conn.cursor() as cur:
        cur.execute(f'SELECT stock_id, industry FROM "{schema}"."stock_meta"')
        return {str(sid): (str(ind) if ind else None) for sid, ind in cur.fetchall()}


def groups_updated_at(conn: Any, schema: str) -> dt.datetime | None:
    """族群表上次整份寫入的時間；表還沒建或是空的 → None（要抓）。"""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f'"{schema}"."{STOCK_GROUP_SPEC.name}"',))
        row = cur.fetchone()
        if row is None or row[0] is None:
            return None
        cur.execute(f'SELECT max(updated_at) FROM "{schema}"."{STOCK_GROUP_SPEC.name}"')
        got = cur.fetchone()
    return got[0] if got else None


def replace_groups(
    conn: Any, schema: str, groups: Sequence[StockGroup], updated_at: dt.datetime
) -> None:
    """整份換掉族群表（同一個交易：網站不會讀到半份）。一檔都沒有即 raise——那是來源壞了，不是沒族群。"""
    if updated_at.tzinfo is None:
        raise ValueError("updated_at 必須帶時區")
    if not groups:
        raise DataError(
            STOCK_GROUP_SPEC.name, None, "算出 0 檔有族群——來源或官方產業別讀取有問題，不覆蓋舊表"
        )
    ensure_tables(conn, (STOCK_GROUP_SPEC,), schema)
    table = f'"{schema}"."{STOCK_GROUP_SPEC.name}"'
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {table}")
        cur.executemany(
            f"INSERT INTO {table} (stock_id, chain, segments, also, updated_at) VALUES (%s, %s, %s, %s, %s)",
            [(g.stock_id, g.group, list(g.segments), list(g.also), updated_at) for g in groups],
        )
    conn.commit()
