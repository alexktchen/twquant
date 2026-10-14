"""歷史同區間參考（D-179）的 PG 讀寫：整表覆蓋寫入 `score_reference`、讀出給報告或網站。

只在腳本／CLI 邊界呼叫；連線由呼叫端提供（本機或雲端都用同一個函數）。
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence
from typing import Any

from twquant.data.serving.schema import SCORE_REFERENCE_SPEC, ensure_tables
from twquant.errors import DataError
from twquant.reporting.score_reference import RefRow


def write_reference(
    conn: Any,
    schema: str,
    rows: Sequence[RefRow],
    labels: Mapping[str, str],
    registration: str,
    generated_at: dt.datetime,
) -> None:
    """整表覆蓋（同一個交易）。一列都沒有、NaN／無限大、期間沒有說明即 raise。"""
    if generated_at.tzinfo is None:
        raise ValueError("generated_at 必須帶時區")
    if not rows:
        raise DataError(SCORE_REFERENCE_SPEC.name, None, "沒有任何一列，不覆蓋")
    for r in rows:
        nums = (r.mean, r.median, r.win_rate, r.big_loss_rate, r.p10, r.p90, r.best, r.worst)
        if any(x is not None and not math.isfinite(x) for x in nums):
            raise DataError(
                SCORE_REFERENCE_SPEC.name, None, f"{r.line} {r.period} {r.bucket} 有 NaN／無限大"
            )
        if r.period not in labels:
            raise DataError(SCORE_REFERENCE_SPEC.name, None, f"期間 {r.period!r} 沒有說明")
    ensure_tables(conn, (SCORE_REFERENCE_SPEC,), schema)
    table = f'"{schema}"."{SCORE_REFERENCE_SPEC.name}"'
    cols = ", ".join(SCORE_REFERENCE_SPEC.column_names)
    marks = ", ".join(["%s"] * len(SCORE_REFERENCE_SPEC.column_names))
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {table}")
        cur.executemany(
            f"INSERT INTO {table} ({cols}) VALUES ({marks})",
            [
                (
                    r.line,
                    r.period,
                    r.bucket,
                    r.lo,
                    r.hi,
                    r.n,
                    r.mean,
                    r.median,
                    r.win_rate,
                    r.big_loss_rate,
                    r.p10,
                    r.p90,
                    r.best,
                    r.worst,
                    r.signals_from,
                    r.signals_to,
                    labels[r.period],
                    registration,
                    generated_at,
                )
                for r in rows
            ],
        )
    conn.commit()


def read_reference(conn: Any, schema: str) -> list[RefRow]:
    """整表讀出（表不存在回空）。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT to_regclass(%s) IS NOT NULL", (f"{schema}.{SCORE_REFERENCE_SPEC.name}",)
        )
        row = cur.fetchone()
        if not row or not row[0]:
            return []
        cur.execute(
            "SELECT line, period, bucket, lo, hi, n, mean, median, win_rate, big_loss_rate, p10, p90, "
            f'best, worst, signals_from, signals_to FROM "{schema}"."{SCORE_REFERENCE_SPEC.name}" '
            "ORDER BY line, period, lo NULLS FIRST"
        )
        return [RefRow(*r) for r in cur.fetchall()]
