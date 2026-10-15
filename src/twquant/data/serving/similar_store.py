"""歷史相似情境（D-182）的 PG 讀寫：情境庫（壓縮切片，整表覆蓋）與每天名單的比對結果（整天覆蓋）。

只在 CLI／pipeline／腳本邊界呼叫；連線由呼叫端提供（沿 pick_reasons_store.py 慣例）。
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping, Sequence
from typing import Any

from twquant.data.serving.schema import PICK_SIMILAR_SPEC, SIMILAR_LIBRARY_SPEC, ensure_tables
from twquant.errors import DataError
from twquant.reporting.similar import SimilarRow


def write_library(
    conn: Any,
    schema: str,
    parts: Mapping[str, bytes],
    registration: str,
    created_at: dt.datetime,
) -> None:
    """整表覆蓋：先刪再寫，同一個交易裡（寫一半失敗就回到舊的庫，不會留半套）。"""
    if created_at.tzinfo is None:
        raise ValueError("created_at 必須帶時區")
    if "meta" not in parts:
        raise ValueError("情境庫少了 meta")
    ensure_tables(conn, (SIMILAR_LIBRARY_SPEC,), schema)
    table = f'"{schema}"."{SIMILAR_LIBRARY_SPEC.name}"'
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {table}")
        for part, payload in parts.items():
            cur.execute(
                f"INSERT INTO {table} (part, payload, registration, created_at) "
                "VALUES (%s, %s, %s, %s)",
                (part, payload, registration, created_at),
            )
    conn.commit()


def read_library(conn: Any, schema: str) -> dict[str, bytes] | None:
    """整個情境庫的切片；表還沒建（還沒發布）→ None。"""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f'"{schema}"."{SIMILAR_LIBRARY_SPEC.name}"',))
        exists = cur.fetchone()
        if not exists or exists[0] is None:
            return None
        cur.execute(f'SELECT part, payload FROM "{schema}"."{SIMILAR_LIBRARY_SPEC.name}"')
        return {str(p): bytes(b) for p, b in cur.fetchall()}


def write_similar(
    conn: Any,
    schema: str,
    day: dt.date,
    rows: Sequence[SimilarRow],
    registration: str,
    created_at: dt.datetime,
) -> None:
    """整天覆蓋（重跑冪等）。找不到任何鄰居的列 raise（要說出來，不是存空的）。"""
    if created_at.tzinfo is None:
        raise ValueError("created_at 必須帶時區")
    for r in rows:
        if int(r.stats["n"]) == 0:
            raise DataError(PICK_SIMILAR_SPEC.name, day, f"{r.line} {r.stock_id} 找不到可比的情境")
    ensure_tables(conn, (PICK_SIMILAR_SPEC,), schema)
    table = f'"{schema}"."{PICK_SIMILAR_SPEC.name}"'
    cols = (
        "date, line, stock_id, n, mean, median, win_rate, big_loss_rate, p10, p90, best, worst, "
        "span_from, span_to, examples, registration, created_at"
    )
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {table} WHERE date = %s", (day,))
        cur.executemany(
            f"INSERT INTO {table} ({cols}) VALUES "
            "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)",
            [
                (
                    day,
                    r.line,
                    r.stock_id,
                    int(r.stats["n"]),
                    r.stats["mean"],
                    r.stats["median"],
                    r.stats["win_rate"],
                    r.stats["big_loss_rate"],
                    r.stats["p10"],
                    r.stats["p90"],
                    r.stats["best"],
                    r.stats["worst"],
                    r.span[0] if r.span else None,
                    r.span[1] if r.span else None,
                    json.dumps(r.examples, ensure_ascii=False),
                    registration,
                    created_at,
                )
                for r in rows
            ],
        )
    conn.commit()
