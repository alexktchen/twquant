"""「為什麼選它」（D-180）的 PG 讀寫：當天的名單、因子原始值與全市場百分位、模型百分位，整天覆蓋寫回 `pick_reasons`。

只在 CLI／pipeline 邊界呼叫；連線由呼叫端提供（沿 group_flow_store.py 慣例）。
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from twquant.data.serving.schema import PICK_REASONS_SPEC, ensure_tables
from twquant.errors import DataError


@dataclass(frozen=True)
class DayPick:
    """當天名單的一檔。"""

    line: str
    rank: int
    stock_id: str
    score: float | None
    model_id: str | None


def read_day_picks(conn: Any, schema: str, day: dt.date) -> list[DayPick]:
    """當天每條線的名單（依線、名次）。"""
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT line, rank, stock_id, score, model_id FROM "{schema}"."daily_picks" '
            "WHERE date = %s ORDER BY line, rank",
            (day,),
        )
        return [DayPick(str(a), int(b), str(c), d, e) for a, b, c, d, e in cur.fetchall()]


def read_factor_raw(
    conn: Any, schema: str, day: dt.date, factors: Sequence[str], sids: Sequence[str]
) -> dict[tuple[str, str], tuple[float | None, float | None]]:
    """(代號, 因子) → (原始值, 全市場百分位)。沒有那一列＝不在字典裡（呼叫端當缺值，不補 0）。"""
    if not factors or not sids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT stock_id, factor, value, pct FROM "{schema}"."factor_values" '
            "WHERE date = %s AND factor = ANY(%s) AND stock_id = ANY(%s)",
            (day, list(factors), list(sids)),
        )
        return {(str(s), str(f)): (v, p) for s, f, v, p in cur.fetchall()}


def read_rank_pct(
    conn: Any, schema: str, day: dt.date, model_id: str, sids: Sequence[str]
) -> dict[str, float]:
    """模型當天對這幾檔的全市場百分位（ml_scores.rank_pct）。"""
    if not sids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT stock_id, rank_pct FROM "{schema}"."ml_scores" '
            "WHERE date = %s AND model_id = %s AND stock_id = ANY(%s)",
            (day, model_id, list(sids)),
        )
        return {str(s): float(p) for s, p in cur.fetchall() if p is not None}


def write_reasons(
    conn: Any,
    schema: str,
    day: dt.date,
    rows: Sequence[tuple[str, str, str | None, list[dict[str, object]]]],
    created_at: dt.datetime,
) -> None:
    """整天覆蓋（重跑冪等）。rows：(線, 代號, model_id, 理由)。理由是空的列 raise（寫不出理由要說，不是存空的）。"""
    if created_at.tzinfo is None:
        raise ValueError("created_at 必須帶時區")
    for line, sid, _, reasons in rows:
        if not reasons:
            raise DataError(PICK_REASONS_SPEC.name, day, f"{line} {sid} 寫不出任何理由")
    ensure_tables(conn, (PICK_REASONS_SPEC,), schema)
    table = f'"{schema}"."{PICK_REASONS_SPEC.name}"'
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {table} WHERE date = %s", (day,))
        cur.executemany(
            f"INSERT INTO {table} (date, line, stock_id, model_id, reasons, created_at) "
            "VALUES (%s, %s, %s, %s, %s::jsonb, %s)",
            [
                (day, line, sid, mid, json.dumps(reasons, ensure_ascii=False), created_at)
                for line, sid, mid, reasons in rows
            ],
        )
    conn.commit()
