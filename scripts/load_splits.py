"""T-151：把分割／面額變更／反分割載入 `corporate_events`。

用法（在 worktree 目錄）：
    python scripts/load_splits.py --check    # 只比對與報告，不寫
    python scripts/load_splits.py --write    # 比對通過才寫 PG

為什麼需要：`corporate_events` 原本只有除權息，於是 0050 在 2025-06 的 1:4 分割
（188.65 → 停牌 5 天 → 復牌 47.16）會被算成 **−74.8% 的單日報酬**——而 0050 正是
「有沒有打敗大盤」的主基準。既有的三道防護都攔不住：`_entry > 0` 只擋零價
（分割兩端都是正價）、T-144 的跨距防護不攔（停牌 5 個開市日遠在容忍值 42 內）、
`safe_ratio` 只防零分母。

資料來源：`data/staging/finmind_splits.json`（FinMind `TaiwanStockSplitPrice`，
不帶 `data_id` 即全市場，2019 起共 34 筆）。

**主鍵衝突是這裡唯一的真風險**：`corporate_events` 的鍵是 (date, stock_id)，若某檔
在同一天既除息又分割，upsert 會讓後寫的蓋掉先寫的，而正確做法是兩個因子相乘。
故載入前一定先比對，有衝突就停下來（不靜默覆蓋）。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from twquant.config import ServingConfig
from twquant.data import schemas
from twquant.data.serving import schema as sch
from twquant.data.serving import store
from twquant.data.serving.sync import upsert
from twquant.data.sources.finmind import split_rows

SRC = pathlib.Path("D:/project/twquant/data/staging/finmind_splits.json")
TABLE = "corporate_events"
KEY = ["date", "stock_id"]


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--write", action="store_true")
    args = ap.parse_args()

    raw = json.loads(SRC.read_text(encoding="utf-8"))
    rows = split_rows(raw)
    df = schemas.validate(TABLE, pl.DataFrame(rows))
    print(f"來源 {len(raw)} 筆 → 可載入 {df.height} 筆")
    print(df.group_by("event_type").len().sort("len", descending=True).to_dicts())

    cfg = ServingConfig(pg_schema="serving")
    conn = store.connect(cfg)
    try:
        existing = pl.read_database(
            f'SELECT date, stock_id, event_type, adj_factor FROM "{cfg.pg_schema}".{TABLE}', conn
        )
        keys_new = set(zip(df["date"].to_list(), df["stock_id"].to_list(), strict=True))
        keys_old = set(zip(existing["date"].to_list(), existing["stock_id"].to_list(), strict=True))
        clash = sorted(keys_new & keys_old)
        if clash:
            # 同日既除息又分割：upsert 會蓋掉，但正確做法是兩個因子相乘。停下來由人決定。
            print(f"\n!! 主鍵衝突 {len(clash)} 筆——同一天已有除權息事件：")
            for d, s in clash[:10]:
                old = existing.filter((pl.col("date") == d) & (pl.col("stock_id") == s)).row(
                    0, named=True
                )
                new = df.filter((pl.col("date") == d) & (pl.col("stock_id") == s)).row(
                    0, named=True
                )
                print(
                    f"   {d} {s}：既有 {old['event_type']} {old['adj_factor']:.6f}"
                    f" vs 新 {new['event_type']} {new['adj_factor']:.6f}"
                )
            raise SystemExit(
                "不得靜默覆蓋：同日兩個事件的正確處理是因子相乘，須先裁決（DECISIONS）。"
            )
        print(f"\n主鍵無衝突（既有 {existing.height:,} 筆，新增 {df.height} 筆）✓")

        if args.check:
            print("--check，未寫入。")
            return
        sch.ensure_schema(conn, cfg.pg_schema)
        spec = sch.spec_of(TABLE)
        with conn.cursor() as cur:
            upsert(cur, cfg.pg_schema, spec, df.select([c for c, _ in spec.columns]))
        conn.commit()
        print(f"已寫入 {df.height} 筆 → PG {cfg.pg_schema}.{TABLE}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
