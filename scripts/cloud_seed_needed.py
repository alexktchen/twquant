"""用地端 PG 的「需要的切片」覆蓋雲端 Supabase 的 serving schema（使用者 2026-10-09）。

切片定義在 configs/cloud_seed.yaml（每段都寫了誰讀它、為什麼是這個長度）。

模式：
  --dry-run            只在地端計數各切片列數，不連雲端、不寫任何東西
  （預設）             DROP 雲端 serving 全部表 → 以現行程式的 schema 重建 → COPY 切片
  --tables a,b         只對列出的表 TRUNCATE 後重灌（不動其他表）

保險：雲端 DSN 指向 localhost／127.0.0.1，或與地端 DSN 相同時拒絕執行——這支腳本會刪表。

用法（在 repo 根目錄）：
  uv run --all-groups python scripts/cloud_seed_needed.py --env-file D:/project/twquant/.env --dry-run
  uv run --all-groups python scripts/cloud_seed_needed.py --env-file D:/project/twquant/.env
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import sys
import time
from typing import Any
from urllib.parse import urlparse

import psycopg
import yaml
from psycopg import sql

from twquant.data.serving.schema import ensure_schema, spec_of

ROOT = pathlib.Path(__file__).resolve().parents[1]
SEED_CFG = ROOT / "configs" / "cloud_seed.yaml"
SERVING_CFG = ROOT / "configs" / "serving.yaml"
_SLICE_KEYS = {"all", "since", "last_sessions", "stock_pattern", "industry_members", "in"}


def _load_env(path: pathlib.Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _host(dsn: str) -> str:
    if "://" in dsn:
        return (urlparse(dsn).hostname or "").lower()
    for part in dsn.split():
        if part.startswith("host="):
            return part.split("=", 1)[1].lower()
    return ""


def _check_targets(local_dsn: str, cloud_dsn: str) -> None:
    """這支腳本會刪雲端的表：雲端 DSN 不能是本機，也不能和地端相同。"""
    host = _host(cloud_dsn)
    if host in {"", "localhost", "127.0.0.1", "::1", "host.docker.internal"}:
        raise SystemExit(f"拒絕執行：雲端 DSN 的主機是 {host!r}，看起來不是 Supabase")
    if cloud_dsn == local_dsn or host == _host(local_dsn):
        raise SystemExit("拒絕執行：雲端 DSN 與地端 DSN 指向同一台主機")


def _conditions(
    table: str, piece: dict[str, Any], stock_pattern: str, schema: str
) -> list[sql.Composable]:
    """一段切片的 WHERE 條件（不含 last_sessions，那個要先查日期）。"""
    unknown = set(piece) - _SLICE_KEYS
    if unknown:
        raise SystemExit(f"{table}：未知的切片鍵 {sorted(unknown)}（允許 {sorted(_SLICE_KEYS)}）")
    if piece.get("all") and len(piece) > 1:
        raise SystemExit(f"{table}：all: true 不能和其他條件並列")
    if piece.get("stock_pattern") and piece.get("industry_members"):
        raise SystemExit(f"{table}：stock_pattern 與 industry_members 互斥（請拆成兩段）")
    conds: list[sql.Composable] = []
    if "since" in piece:
        since = dt.date.fromisoformat(str(piece["since"]))
        conds.append(sql.SQL("date >= {}").format(sql.Literal(since)))
    if piece.get("stock_pattern"):
        conds.append(sql.SQL("stock_id ~ {}").format(sql.Literal(stock_pattern)))
    if piece.get("industry_members"):
        # 與 cloud.prune 的 industry_member_tables 同一條規則：不是四碼、但有產業別
        conds.append(sql.SQL("stock_id !~ {}").format(sql.Literal(stock_pattern)))
        conds.append(
            sql.SQL(
                "stock_id IN (SELECT stock_id FROM {}.stock_meta WHERE coalesce(industry, '') <> '')"
            ).format(sql.Identifier(schema))
        )
    for col, values in (piece.get("in") or {}).items():
        if not values:
            raise SystemExit(f"{table}：in.{col} 是空清單")
        conds.append(
            sql.SQL("{} = ANY({})").format(sql.Identifier(col), sql.Literal([str(v) for v in values]))
        )
    return conds


def _where(conds: list[sql.Composable]) -> sql.Composable:
    return sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conds) if conds else sql.SQL("")


def _slice_where(
    cur: Any, schema: str, table: str, piece: dict[str, Any], stock_pattern: str
) -> sql.Composable:
    conds = _conditions(table, piece, stock_pattern, schema)
    if "last_sessions" in piece:
        n = int(piece["last_sessions"])
        if n < 1:
            raise SystemExit(f"{table}：last_sessions 必須 ≥ 1，拿到 {n}")
        q = sql.SQL("SELECT DISTINCT date FROM {}.{}{} ORDER BY date DESC LIMIT 1 OFFSET {}").format(
            sql.Identifier(schema), sql.Identifier(table), _where(conds), sql.Literal(n - 1)
        )
        cur.execute(q)
        row = cur.fetchone()
        if row is None:
            raise SystemExit(f"{table}：地端只有不到 {n} 個日期，切片要求 last_sessions={n}")
        conds = [*conds, sql.SQL("date >= {}").format(sql.Literal(row[0]))]
    return _where(conds)


def _plan(local: Any, schema: str, tables: dict[str, list[dict[str, Any]]], stock_pattern: str):
    """每張表的 (spec, [where...])；同時在地端計數。"""
    plan = []
    with local.cursor() as cur:
        for table, pieces in tables.items():
            spec = spec_of(table)  # 未知表名 raise（黃金規則 5）
            wheres = [_slice_where(cur, schema, table, p, stock_pattern) for p in pieces]
            total = 0
            spans = []
            for w in wheres:
                cur.execute(
                    sql.SQL("SELECT count(*), min(date), max(date) FROM {}.{}{}").format(
                        sql.Identifier(schema), sql.Identifier(table), w
                    )
                    if not spec.snapshot
                    else sql.SQL("SELECT count(*), NULL, NULL FROM {}.{}{}").format(
                        sql.Identifier(schema), sql.Identifier(table), w
                    )
                )
                n, lo, hi = cur.fetchone()
                total += int(n)
                spans.append(f"{lo}~{hi}" if lo else "snapshot")
            plan.append((spec, wheres, total, spans))
    return plan


def _copy(local: Any, cloud: Any, schema: str, spec: Any, wheres: list[sql.Composable]) -> int:
    cols = sql.SQL(", ").join(sql.Identifier(c) for c in spec.column_names)
    target = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(spec.name))
    with local.cursor() as scur, cloud.cursor() as dcur:
        for w in wheres:
            src = sql.SQL("COPY (SELECT {} FROM {}{}) TO STDOUT").format(cols, target, w)
            dst = sql.SQL("COPY {} ({}) FROM STDIN").format(target, cols)
            with scur.copy(src) as out, dcur.copy(dst) as into:
                for data in out:
                    into.write(bytes(data))
        dcur.execute(sql.SQL("ANALYZE {}").format(target))
        dcur.execute(sql.SQL("SELECT count(*) FROM {}").format(target))
        n = int(dcur.fetchone()[0])
    cloud.commit()
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env-file", required=True, help="含 TWQUANT_SUPABASE_DSN 的 .env（主 checkout 那份）")
    ap.add_argument("--dry-run", action="store_true", help="只在地端計數，不連雲端")
    ap.add_argument("--tables", default="", help="只重灌這些表（逗號分隔）；空＝整個 schema 覆蓋")
    args = ap.parse_args()

    env = _load_env(pathlib.Path(args.env_file))
    local_dsn = os.environ.get("TWQUANT_PG_DSN") or env.get("TWQUANT_PG_DSN") or (
        "postgresql://twquant:twquant@localhost:5433/twquant"
    )
    cloud_dsn = os.environ.get("TWQUANT_SUPABASE_DSN") or env.get("TWQUANT_SUPABASE_DSN", "")

    serving = yaml.safe_load(SERVING_CFG.read_text(encoding="utf-8"))
    schema = serving["pg_schema"]
    stock_pattern = serving["cloud"]["stock_pattern"]
    tables: dict[str, list[dict[str, Any]]] = yaml.safe_load(SEED_CFG.read_text(encoding="utf-8"))["tables"]
    only = [t for t in args.tables.split(",") if t]
    if only:
        missing = sorted(set(only) - set(tables))
        if missing:
            raise SystemExit(f"--tables 有未在 cloud_seed.yaml 定義的表：{missing}")
        tables = {t: tables[t] for t in only}

    local = psycopg.connect(local_dsn, connect_timeout=10)
    plan = _plan(local, schema, tables, stock_pattern)
    print(f"{'table':24} {'rows':>10}  span")
    for spec, _, n, spans in plan:
        print(f"{spec.name:24} {n:10d}  {'; '.join(spans)}")
    print(f"{'total':24} {sum(p[2] for p in plan):10d}")
    if args.dry_run:
        return

    if not cloud_dsn:
        raise SystemExit("找不到 TWQUANT_SUPABASE_DSN")
    _check_targets(local_dsn, cloud_dsn)
    cloud = psycopg.connect(cloud_dsn, prepare_threshold=None, connect_timeout=20)
    with cloud.cursor() as cur:
        # 超過 Free 額度時 Supabase 會把預設交易設成唯讀；本 session 解除才能刪表（記憶：T-055）
        cur.execute("SET default_transaction_read_only = off")
        if only:
            for spec, *_ in plan:
                cur.execute(sql.SQL("TRUNCATE {}.{}").format(sql.Identifier(schema), sql.Identifier(spec.name)))
        else:
            cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY 1", (schema,))
            existing = [r[0] for r in cur.fetchall()]
            for t in existing:
                cur.execute(sql.SQL("DROP TABLE {}.{} CASCADE").format(sql.Identifier(schema), sql.Identifier(t)))
            print(f"[雲端] 已刪除 {len(existing)} 張表")
    cloud.commit()
    if not only:
        ensure_schema(cloud, schema)
        cloud.commit()
        print("[雲端] 已依現行 schema 重建")

    for spec, wheres, expected, _ in plan:
        t0 = time.monotonic()
        n = _copy(local, cloud, schema, spec, wheres)
        if n != expected:
            raise SystemExit(f"{spec.name}：雲端 {n} 列 ≠ 地端切片 {expected} 列")
        print(f"[OK] {spec.name:24} {n:10d} 列  {time.monotonic() - t0:6.1f}s")

    with cloud.cursor() as cur:
        cur.execute("SELECT pg_database_size(current_database())")
        print(f"[雲端] pg_database_size = {cur.fetchone()[0] / 1e6:.1f} MB")
        cur.execute(
            "SELECT c.relname, pg_total_relation_size(c.oid) FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relkind = 'r' ORDER BY 2 DESC LIMIT 12",
            (schema,),
        )
        for name, size in cur.fetchall():
            print(f"    {name:24} {size / 1e6:7.1f} MB")
    cloud.close()
    local.close()


if __name__ == "__main__":
    sys.exit(main())
