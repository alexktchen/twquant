"""ml_scores 直寫（SPEC-010 §5）：`ml score` 推論結果寫入 serving.ml_scores。

寫入前過 pandera 驗證（AC-7，壞批整批 raise 不落地）；ON CONFLICT
(model_id, stock_id, date) DO NOTHING——分數決定性，重跑冪等。
"""

from __future__ import annotations

from typing import Any

import polars as pl

from twquant.data import schemas

_COLS = ("date", "stock_id", "model_id", "score", "rank_pct", "available_at")


def upsert_ml_scores(conn: Any, schema: str, frame: pl.DataFrame) -> int:
    """驗證後批次寫入；回傳實際新增列數（衝突略過不計）。"""
    df = schemas.validate("ml_scores", frame.select(_COLS))
    sql = (
        f'INSERT INTO "{schema}"."ml_scores" ({", ".join(_COLS)}) '
        f"VALUES ({', '.join(['%s'] * len(_COLS))}) "
        "ON CONFLICT (model_id, stock_id, date) DO NOTHING"
    )
    with conn.cursor() as cur:
        cur.executemany(sql, df.rows())
        inserted = cur.rowcount
    conn.commit()
    return int(inserted)
