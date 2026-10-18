"""D-194（2026-10-18）：本機只訓練、雲端是每日資料正本——發現本機落後就從雲端拉回來，不用問。

    TWQUANT_PG_DSN=postgresql://twquant:twquant@localhost:5433/twquant TWQUANT_SUPABASE_DSN=<雲端 DSN> \
        uv run --all-groups python scripts/pull_cloud_to_local.py [--tables prices_daily,daily_picks] [--dry-run]

每張表：本機最新日期之後、雲端有的列 → upsert 進本機（同表名、同主鍵；本機沒有的表先建）。
只拉「每天抓的」表（清單寫死在 TABLES），**不拉 factor_values**——雲端因子的物化窗與定義和本機不同（D-049 §、2026-10-09 探查），
訓練要的因子仍由本機物化。雲端沒有的表（例：本機才有的權證摘要）跳過並列出。不靜默：每張表印出拉了幾列、哪幾天。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from typing import Any

import psycopg
from psycopg.types.json import Json

from twquant.config import ServingConfig, load_config
from twquant.data.serving import schema as sch
from twquant.data.serving import store
from twquant.errors import DataError

# 每天抓的表（雲端是正本）。順序無所謂；都以 date 欄當進度。
TABLES: tuple[str, ...] = (
    "prices_daily",
    "institutional_daily",
    "margin_daily",
    "index_daily",
    "stock_valuation",
    "monthly_revenue",
    "daytrade_daily",
    "sbl_short_daily",
    "attention_stocks",
    "disposition_stocks",
    "daily_picks",
    "pick_reasons",
    "pick_similar",
    "ml_scores",
    "market_daily",
    "taifex_daily",
    "mops_announcements",
    "conference_calendar",
    "dividend_calendar",
    "group_flow_daily",
    # D-191～D-193 的新表（雲端每日步驟產生）
    "insider_transfer_notices",
    "warrant_summary_daily",
    "pick_down_risk",
    "pick_blurbs",  # T-393
    "pick_reviews",  # T-394
)


def _exists(cur: Any, schema: str, table: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
        (schema, table),
    )
    return cur.fetchone() is not None


def _max_date(cur: Any, schema: str, table: str) -> dt.date | None:
    cur.execute(f'SELECT max(date) FROM "{schema}"."{table}"')
    row = cur.fetchone()
    return row[0] if row and row[0] is not None else None


def _adapt(v: object) -> object:
    return Json(v) if isinstance(v, dict | list) else v


def _upsert_rows(
    cur: Any, schema: str, spec: sch.TableSpec, rows: list[tuple[object, ...]]
) -> None:
    """同 serving.sync.upsert，但吃原生列（jsonb 欄用 Json 包）。"""
    cols = spec.column_names
    collist = ", ".join(f'"{c}"' for c in cols)
    stg = f"_stg_{spec.name}"
    cur.execute(f'CREATE TEMP TABLE {stg} (LIKE "{schema}"."{spec.name}") ON COMMIT DROP')
    with cur.copy(f"COPY {stg} ({collist}) FROM STDIN") as cp:
        for row in rows:
            cp.write_row(tuple(_adapt(v) for v in row))
    pklist = ", ".join(f'"{c}"' for c in spec.pk)
    setcols = [c for c in cols if c not in spec.pk]
    conflict = (
        "DO UPDATE SET " + ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in setcols)
        if setcols
        else "DO NOTHING"
    )
    cur.execute(
        f'INSERT INTO "{schema}"."{spec.name}" ({collist}) SELECT {collist} FROM {stg} ON CONFLICT ({pklist}) {conflict}'
    )


def pull(table: str, local: Any, cloud: Any, schema: str, dry_run: bool) -> dict[str, object]:
    spec = sch.spec_of(table)
    if "date" not in spec.column_names:
        return {"table": table, "status": "skip", "why": "沒有 date 欄"}
    with cloud.cursor() as ccur:
        if not _exists(ccur, schema, table):
            return {"table": table, "status": "skip", "why": "雲端沒有這張表"}
    with local.cursor() as lcur:
        sch.ensure_tables(local, (spec,), schema)
        sch.assert_columns(local, spec, schema)
        since = _max_date(lcur, schema, table)
    cols = ", ".join(f'"{c}"' for c in spec.column_names)
    with cloud.cursor() as ccur:
        if since is None:
            ccur.execute(f'SELECT {cols} FROM "{schema}"."{table}"')
        else:
            ccur.execute(f'SELECT {cols} FROM "{schema}"."{table}" WHERE date > %s', (since,))
        rows = ccur.fetchall()
    if not rows:
        return {"table": table, "status": "ok", "local_max": str(since), "pulled": 0, "days": []}
    didx = spec.column_names.index("date")
    days = sorted({str(r[didx]) for r in rows})
    if not dry_run:
        with local.cursor() as lcur:
            _upsert_rows(lcur, schema, spec, rows)
        local.commit()
    return {
        "table": table,
        "status": "ok",
        "local_max": str(since),
        "pulled": len(rows),
        "days": days,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", default="", help="逗號分隔；空＝全部登記的表")
    ap.add_argument("--dry-run", action="store_true", help="只算不寫")
    args = ap.parse_args()
    scfg = load_config("configs/serving.yaml", ServingConfig)
    wanted = [t for t in args.tables.split(",") if t] or list(TABLES)
    unknown = [t for t in wanted if t not in TABLES]
    if unknown:
        print(
            f"不在登記清單裡的表：{unknown}（factor_values 等衍生表不拉，見檔頭）", file=sys.stderr
        )
        return 1
    local = store.connect(scfg)
    cloud = store.connect_cloud(scfg)
    results = []
    try:
        for t in wanted:
            try:
                r = pull(t, local, cloud, scfg.pg_schema, args.dry_run)
            except (psycopg.Error, DataError) as exc:
                # 例：本機某張表欄位比 spec 少（舊版建的）——這張記錯誤、繼續拉別張，不整批中斷
                local.rollback()
                cloud.rollback()
                r = {"table": t, "status": "error", "why": str(exc).splitlines()[0][:160]}
            results.append(r)
            print(json.dumps(r, ensure_ascii=False, default=str))
    finally:
        local.close()
        cloud.close()
    errors = [r for r in results if r["status"] == "error"]
    total = sum(p for r in results if isinstance(p := r.get("pulled"), int))
    print(f"{'（試算）' if args.dry_run else ''}共拉 {total:,} 列；錯誤 {len(errors)} 張")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
