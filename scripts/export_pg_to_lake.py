"""把 PG serving 鏡射表匯出成資料湖分區格式（一次性，供快速因子物化用）。

為什麼需要：`serving materialize` 從 PG 讀，實測 **~100 秒/交易日**（1,619 天 ≈ 45 小時）；
`scripts/backfill_factors.py` 從本機資料湖讀，實測 **~10 秒/交易日**（≈ 4.5 小時）。
但現有資料湖只剩 773/1,619 天（已被輪替），且不含 T-079 修復的 43 個日期
——那些修復只寫進了 PG。故先把 PG 匯出成湖格式，再走快速路徑。

**非破壞性**：寫進獨立的 root（預設 `data/lake_rebuild`），不動現有 `data/clean`。

用法：
    python scripts/export_pg_to_lake.py [root] [--tables a,b,c]
"""

from __future__ import annotations

import argparse
import os
import pathlib
import time

import polars as pl
import psycopg

_DSN = "postgresql://twquant:twquant@localhost:5433/twquant"

# 因子實際會讀的表（見各 @factor 的 tables=）
_DEFAULT_TABLES = (
    "prices_daily",
    "index_daily",
    "monthly_revenue",
    "institutional_daily",
    "margin_daily",
)


def _export(conn: psycopg.Connection, schema: str, table: str, root: pathlib.Path) -> int:
    cols = [
        r[0]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
            (schema, table),
        ).fetchall()
    ]
    if not cols:
        raise SystemExit(f"{schema}.{table} 不存在")
    rows = conn.execute(f'SELECT {",".join(cols)} FROM "{schema}"."{table}"').fetchall()
    if not rows:
        print(f"  {table}: 0 列，略過", flush=True)
        return 0
    df = pl.DataFrame(rows, schema=cols, orient="row")
    # psycopg 把 timestamptz 讀成 UTC，但本專案 schema 的 TW_DT 是 Asia/Taipei；
    # 時區不一致會讓 PitView 的 available_at 比較在 polars 拋錯，整個因子被略過。
    tz_cols = [
        c
        for c, t in zip(df.columns, df.dtypes, strict=True)
        if isinstance(t, pl.Datetime) and t.time_zone is not None
    ]
    if tz_cols:
        df = df.with_columns([pl.col(c).dt.convert_time_zone("Asia/Taipei") for c in tz_cols])
    out = root / "clean" / table
    n = 0
    for (day,), part in df.partition_by("date", as_dict=True).items():
        d = out / f"date={day}"
        d.mkdir(parents=True, exist_ok=True)
        part.write_parquet(d / "part-pg.parquet")
        n += 1
    print(f"  {table}: {df.height:,} 列 → {n:,} 個分區", flush=True)
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default="D:/project/twquant/data/lake_rebuild")
    ap.add_argument("--schema", default="serving")
    ap.add_argument("--tables", default=",".join(_DEFAULT_TABLES))
    args = ap.parse_args()

    root = pathlib.Path(args.root)
    tables = [t.strip() for t in args.tables.split(",") if t.strip()]
    print(f"匯出 {args.schema} → {root}", flush=True)
    t0 = time.perf_counter()
    with psycopg.connect(os.environ.get("TWQUANT_PG_DSN", _DSN)) as conn:
        for table in tables:
            _export(conn, args.schema, table, root)
    print(f"完成，耗時 {(time.perf_counter() - t0) / 60:.1f} 分鐘", flush=True)


if __name__ == "__main__":
    main()
