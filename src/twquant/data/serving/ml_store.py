"""ml_scores 直寫（SPEC-010 §5）：`ml score` 推論結果寫入 serving.ml_scores。

寫入前過 pandera 驗證（AC-7，壞批整批 raise 不落地）；ON CONFLICT
(model_id, stock_id, date) DO NOTHING——分數決定性，重跑冪等。
"""

from __future__ import annotations

from typing import Any

import polars as pl

from twquant.data import schemas

_COLS = ("date", "stock_id", "model_id", "score", "rank_pct", "available_at")


def read_factor_frames(
    conn: Any, schema: str, features: list[str], start: object, end: object
) -> dict[str, pl.DataFrame]:
    """factor_values.z 逐因子讀取 → {因子: (date, stock_id, value)}。

    逐因子查詢＋server 端過濾＋只取三欄：factor_values 全史 2 千萬列級，整表拉回
    （PgTableSource.scan）曾把 GHA runner 記憶體打爆（ml-first-train run #1 遭 OOM
    殺 VM）。無資料因子回空表——缺值語意由 assemble 保留（禁填 0）。
    """
    empty = pl.DataFrame(schema={"date": pl.Date, "stock_id": pl.String, "value": pl.Float64})
    out: dict[str, pl.DataFrame] = {}
    with conn.cursor() as cur:
        for name in features:
            cur.execute(
                f'SELECT date, stock_id, z FROM "{schema}"."factor_values" '
                "WHERE factor = %s AND date BETWEEN %s AND %s",
                (name, start, end),
            )
            rows = cur.fetchall()
            out[name] = pl.DataFrame(rows, schema=empty.schema, orient="row") if rows else empty
    return out


def read_factor_long(
    conn: Any, schema: str, features: list[str], start: object, end: object
) -> pl.DataFrame:
    """factor_values 的**原始 value** 長表 (date, stock_id, factor, value)。

    基準組合（strategy/combine.zscore_weighted）自己做 winsorize+z-score，故餵原始值
    而非已標準化的 z——與策略層的真實路徑一致，對照才公平。逐因子查詢，同 OOM 教訓。
    """
    parts: list[pl.DataFrame] = []
    schema_ = {"date": pl.Date, "stock_id": pl.String, "value": pl.Float64}
    with conn.cursor() as cur:
        for name in features:
            cur.execute(
                f'SELECT date, stock_id, value FROM "{schema}"."factor_values" '
                "WHERE factor = %s AND date BETWEEN %s AND %s AND value IS NOT NULL",
                (name, start, end),
            )
            rows = cur.fetchall()
            if rows:
                parts.append(
                    pl.DataFrame(rows, schema=schema_, orient="row").with_columns(
                        pl.lit(name).alias("factor")
                    )
                )
    if not parts:
        raise ValueError(f"factor_values 於 {start}~{end} 無任何指定因子資料")
    return pl.concat(parts).select("date", "stock_id", "factor", "value")


def read_prices_open(conn: Any, schema: str, start: object, end: object) -> pl.DataFrame:
    """prices_daily 的 (date, stock_id, open) 切片（server 端過濾，標籤計算用）。"""
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT date, stock_id, open FROM "{schema}"."prices_daily" '
            "WHERE date BETWEEN %s AND %s AND open IS NOT NULL",
            (start, end),
        )
        rows = cur.fetchall()
    schema_ = {"date": pl.Date, "stock_id": pl.String, "open": pl.Float64}
    return pl.DataFrame(rows, schema=schema_, orient="row")


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
