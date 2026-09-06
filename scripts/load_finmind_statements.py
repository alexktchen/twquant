"""把 FinMind 三表 staging（每檔一 parquet）映射成 `financial_statements` 長表，寫進資料湖與 PG（T-097）。

用法：
    python scripts/load_finmind_statements.py --check     # 只映射與驗證，不寫
    python scripts/load_finmind_statements.py --write     # 驗證通過才寫

三個 staging 目錄固定為 data/staging/finmind_{income,balance,cashflow}（backfill_finmind.py 產出）。
規則：
- 映射用 `sources/finmind.py::statement_rows`（_per 百分比列剔除、缺 value 即 raise、available_at＝法定申報期限）。
- **重疊鍵不覆寫**：湖裡已有的 (date, stock_id, statement, type) 只比對值，任何不一致即 raise；只寫新鍵。
- 寫入走 `Warehouse.write("clean", …, source="finmind")`（pandera 驗證）與 `serving.sync.upsert`。
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from twquant.config import ServingConfig
from twquant.data.serving import schema as sch
from twquant.data.serving import store
from twquant.data.serving.sync import upsert
from twquant.data.sources.finmind import STATEMENT_DATASETS, statement_rows
from twquant.data.warehouse import Warehouse
from twquant.errors import DataError

TABLE = "financial_statements"
KEY = ["date", "stock_id", "statement", "type"]


def _load(staging: pathlib.Path, statement: str) -> pl.DataFrame:
    rows: list[dict] = []
    files = sorted(staging.glob("*.parquet"))
    for f in files:
        df = pl.read_parquet(f)
        if "date" not in df.columns:  # 佔位列（該檔無資料）
            continue
        rows.extend(df.to_dicts())
    mapped = statement_rows(rows, statement)
    print(
        f"  {statement:9s} staging {len(files):,} 檔 → {len(rows):,} 原始列 → {len(mapped):,} 列",
        flush=True,
    )
    return pl.DataFrame(mapped)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staging-root", default="D:/project/twquant/data/staging")
    # T-116 的長歷史 staging 叫 finmind_hist_{income,balance,cashflow}，
    # 與 2020+ 那批（finmind_{...}）並存；用前綴切換而不是把目錄改名，
    # 兩批才能各自重跑與比對。
    ap.add_argument("--prefix", default="finmind_", help="staging 目錄前綴")
    ap.add_argument("--lake", default="D:/project/twquant/data/lake_rebuild")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    if not (args.check or args.write):
        raise SystemExit("請指定 --check 或 --write")

    t0 = time.perf_counter()
    frames = []
    for statement in STATEMENT_DATASETS:
        staging = pathlib.Path(args.staging_root) / f"{args.prefix}{statement}"
        if not staging.exists():
            raise SystemExit(
                f"staging 不存在：{staging}（backfill_finmind.py 尚未跑 {STATEMENT_DATASETS[statement]}）"
            )
        frames.append(_load(staging, statement))
    df = pl.concat(frames, how="vertical_relaxed")
    days = sorted(df["date"].unique().to_list())
    print(
        f"合計 {df.height:,} 列、{df['stock_id'].n_unique():,} 檔、{len(days):,} 個季底（{days[0]} ~ {days[-1]}）",
        flush=True,
    )

    wh = Warehouse(args.lake)
    try:
        existing = wh.scan(TABLE).select([*KEY, "value"]).collect()
    except DataError:  # 湖裡尚無此表（scan 對無檔案的表 raise）→ 全部視為新鍵
        existing = pl.DataFrame(
            schema={
                "date": pl.Date,
                "stock_id": pl.String,
                "statement": pl.String,
                "type": pl.String,
                "value": pl.Float64,
            }
        )
    j = existing.join(df.select([*KEY, "value"]), on=KEY, how="inner", suffix="_fm")
    bad = j.filter(pl.col("value") != pl.col("value_fm"))
    print(f"湖已有 {existing.height:,} 列；重疊鍵 {j.height:,}，值不同 {bad.height:,}", flush=True)
    if bad.height:
        print(bad.head(10))
        raise SystemExit(f"重疊鍵有 {bad.height:,} 列值不一致——映射或來源有問題，停止。")
    fresh = df.join(existing.select(KEY), on=KEY, how="anti")
    print(f"新鍵 {fresh.height:,} 列", flush=True)
    if not args.write:
        print("--check 完成，未寫入。", flush=True)
        return
    if fresh.is_empty():
        print("無新鍵可寫。", flush=True)
        return
    wh.write("clean", TABLE, fresh, source="finmind")
    cfg = ServingConfig(pg_schema="serving")
    spec = sch.spec_of(TABLE)
    with store.connect(cfg) as conn:
        sch.ensure_schema(conn, cfg.pg_schema)
        with conn.cursor() as cur:
            upsert(cur, cfg.pg_schema, spec, fresh.select([c for c, _ in spec.columns]))
        conn.commit()
    print(
        f"已寫入湖與 PG：{fresh.height:,} 列；耗時 {(time.perf_counter() - t0) / 60:.1f} 分鐘",
        flush=True,
    )


if __name__ == "__main__":
    main()
