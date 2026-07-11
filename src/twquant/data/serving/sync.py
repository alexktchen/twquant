"""供應層同步：clean Parquet 湖 → PG（SPEC-009 §3）。

增量：以 sync_state.last_date watermark 找出更新的 date= 分區 → 讀 parquet →
COPY 進 temp staging → INSERT … ON CONFLICT DO UPDATE（冪等，重跑安全）。
snapshot 表（stock_meta，無 date 分區）每次全量 upsert。rebuild：truncate + 重置
watermark 後全量重灌。舊分區的事後修訂由 rebuild 處理（daily sync 只追新日期）。

本模組不 import psycopg——連線由 store 提供，只在傳入的 conn/cursor 上操作。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import polars as pl

from twquant.config import ServingConfig
from twquant.data.serving import schema as sch
from twquant.data.serving import store
from twquant.data.warehouse import Warehouse


@dataclass(frozen=True)
class SyncResult:
    """單表一次同步的結果（供 CLI 顯示與測試）。"""

    table: str
    synced_dates: int  # 本次讀入的分區日數（0＝無新資料，watermark 未動）
    last_date: dt.date | None  # 同步後的 watermark（snapshot 表為 None）
    row_count: int  # 同步後 PG 表總列數


def partition_dates(wh: Warehouse, table: str, layer: str = "clean") -> list[dt.date]:
    base = wh.root / layer / table
    if not base.exists():
        return []
    return sorted(
        dt.date.fromisoformat(p.name[len("date=") :])
        for p in base.glob("date=*")
        if p.name != "date=_all"
    )


def _read_dates(
    wh: Warehouse, table: str, dates: list[dt.date], layer: str = "clean"
) -> pl.DataFrame:
    base = wh.root / layer / table
    files = [f for d in dates for f in sorted((base / f"date={d.isoformat()}").glob("*.parquet"))]
    if not files:
        return pl.DataFrame()
    return pl.scan_parquet(files, missing_columns="insert").collect()


def _read_snapshot(wh: Warehouse, table: str, layer: str = "clean") -> pl.DataFrame:
    files = sorted((wh.root / layer / table / "date=_all").glob("*.parquet"))
    if not files:
        return pl.DataFrame()
    return pl.scan_parquet(files, missing_columns="insert").collect()


def _pg_dates(cur: Any, schema: str, table: str) -> set[dt.date]:
    """該表在 PG 已有的所有 date。供比對資料湖分區、找出尚未同步的分區——不只看 watermark，
    連事後 backfill 的舊日期也會被納入，故補完舊歷史直接 sync 即可（不必 rebuild）。"""
    cur.execute(f'SELECT DISTINCT date FROM "{schema}"."{table}"')
    return {row[0] for row in cur.fetchall()}


def _last_synced_at(cur: Any, schema: str, table: str) -> dt.datetime | None:
    cur.execute(f'SELECT last_synced_at FROM "{schema}".sync_state WHERE table_name = %s', (table,))
    row = cur.fetchone()
    return row[0] if row else None


def _touched_dates(
    wh: Warehouse, table: str, since: dt.datetime | None, layer: str = "clean"
) -> set[dt.date]:
    """分區內任一檔案 mtime 晚於 since 的日期——抓「日期已在 PG、但事後又補了新來源檔」
    的情況（如 TWSE 檔晚於 TPEX 補進同一天）。since=None 回空（首次同步走 _pg_dates 差集）。"""
    if since is None:
        return set()
    ts = since.timestamp()
    base = wh.root / layer / table
    if not base.exists():
        return set()
    out: set[dt.date] = set()
    for p in base.glob("date=*"):
        if p.name == "date=_all":
            continue
        if any(f.stat().st_mtime > ts for f in p.glob("*.parquet")):
            out.add(dt.date.fromisoformat(p.name[len("date=") :]))
    return out


def _row_count(cur: Any, schema: str, table: str) -> int:
    cur.execute(f'SELECT count(*) FROM "{schema}"."{table}"')
    return int(cur.fetchone()[0])


def upsert(cur: Any, schema: str, spec: sch.TableSpec, df: pl.DataFrame) -> None:
    """把 df 以 COPY→staging→ON CONFLICT upsert 進 spec 表。staging 名帶表名，故同一
    交易可連續 upsert 多張表（materialize 一次寫三表）不撞名。

    先對 spec.pk 去重（keep=last）：來源多檔（上市/上櫃分檔）或因子 join 展開可能產生同批
    重複 PK，否則 ON CONFLICT DO UPDATE 會二次命中同列而 CardinalityViolation。
    """
    df = df.unique(subset=list(spec.pk), keep="last")
    cols = spec.column_names
    collist = ", ".join(f'"{c}"' for c in cols)
    stg = f"_stg_{spec.name}"
    cur.execute(f'CREATE TEMP TABLE {stg} (LIKE "{schema}"."{spec.name}") ON COMMIT DROP')
    with cur.copy(f"COPY {stg} ({collist}) FROM STDIN") as cp:
        for row in df.select(cols).iter_rows():
            cp.write_row(row)
    pklist = ", ".join(f'"{c}"' for c in spec.pk)
    setcols = [c for c in cols if c not in spec.pk]
    if setcols:
        setclause = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in setcols)
        conflict = f"DO UPDATE SET {setclause}"
    else:
        conflict = "DO NOTHING"
    cur.execute(
        f'INSERT INTO "{schema}"."{spec.name}" ({collist}) SELECT {collist} FROM {stg} '
        f"ON CONFLICT ({pklist}) {conflict}"
    )


def _set_state(
    cur: Any, schema: str, table: str, last_date: dt.date | None, row_count: int
) -> None:
    cur.execute(
        f'INSERT INTO "{schema}".sync_state (table_name, last_date, last_synced_at, row_count) '
        f"VALUES (%s, %s, now(), %s) "
        f"ON CONFLICT (table_name) DO UPDATE SET "
        f"last_date = EXCLUDED.last_date, last_synced_at = EXCLUDED.last_synced_at, "
        f"row_count = EXCLUDED.row_count",
        (table, last_date, row_count),
    )


def _sync_spec(wh: Warehouse, conn: Any, spec: sch.TableSpec, schema: str) -> SyncResult:
    with conn.cursor() as cur:
        if spec.snapshot:
            df = _read_snapshot(wh, spec.name)
            synced = 0 if df.is_empty() else 1
            new_last = None
        else:
            pg_dates = _pg_dates(cur, schema, spec.name)
            touched = _touched_dates(wh, spec.name, _last_synced_at(cur, schema, spec.name))
            dates = [d for d in partition_dates(wh, spec.name) if d not in pg_dates or d in touched]
            df = _read_dates(wh, spec.name, dates)
            synced = len(dates)
            all_dates = pg_dates | set(dates)
            new_last = max(all_dates) if all_dates else None

        if not df.is_empty():
            upsert(cur, schema, spec, df)  # upsert 內已對 spec.pk 去重
        count = _row_count(cur, schema, spec.name)
        _set_state(cur, schema, spec.name, new_last, count)
    conn.commit()
    return SyncResult(spec.name, synced, new_last, count)


def sync_all(wh: Warehouse, cfg: ServingConfig) -> list[SyncResult]:
    """同步全部鏡射表 → PG：補齊資料湖裡所有尚未進 PG 的分區（新日期＋backfill 的舊日期），冪等。"""
    with store.connect(cfg) as conn:
        sch.ensure_schema(conn, cfg.pg_schema)
        return [_sync_spec(wh, conn, spec, cfg.pg_schema) for spec in sch.MIRROR_SPECS]


def rebuild(wh: Warehouse, cfg: ServingConfig, tables: list[str] | None = None) -> list[SyncResult]:
    """truncate 指定鏡射表（預設全部）+ 重置 watermark 後全量重灌。

    只處理鏡射表。因子表（factor_values / factor_cross_stats / composite_daily）或未知
    表名由 sch.rebuild_targets raise，不靜默略過（黃金規則 5）——那些表請用 serving
    materialize 逐日重物化。
    """
    targets = sch.rebuild_targets(tables)
    with store.connect(cfg) as conn:
        sch.ensure_schema(conn, cfg.pg_schema)
        with conn.cursor() as cur:
            for spec in targets:
                cur.execute(f'TRUNCATE "{cfg.pg_schema}"."{spec.name}"')
                cur.execute(
                    f'DELETE FROM "{cfg.pg_schema}".sync_state WHERE table_name = %s', (spec.name,)
                )
        conn.commit()
        return [_sync_spec(wh, conn, spec, cfg.pg_schema) for spec in targets]


def status(cfg: ServingConfig) -> list[dict[str, Any]]:
    """回傳各表 sync_state（last_date / last_synced_at / row_count），供 CLI 顯示。"""
    with store.connect(cfg) as conn:
        sch.ensure_schema(conn, cfg.pg_schema)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT table_name, last_date, last_synced_at, row_count "
                f'FROM "{cfg.pg_schema}".sync_state ORDER BY table_name'
            )
            rows = cur.fetchall()
    return [
        {"table": r[0], "last_date": r[1], "last_synced_at": r[2], "row_count": r[3]} for r in rows
    ]
