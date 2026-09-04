"""把已抓好的除權息結果載入 `corporate_events`（T-098／D-054）。

用法（在 worktree 目錄）：
    python scripts/load_corporate_events.py --check     # 只比對與報告，不寫
    python scripts/load_corporate_events.py --write     # 比對通過才寫湖與 PG

為什麼需要：回測與 ML 標籤都用未還原價，除息當天的機械跌幅被當成虧損。實測
2022-07 之後 77.7% 的事件落在 6~8 月，h=20 標籤在那三個月有 38.2% 的樣本換分位、
原「最差一成」有 17.9% 是除息假象（D-054）。

資料來源：`data/staging/finmind_dividend_result/`（2026-09-03 22:20 已抓完，不需再呼叫 API）。
還原因子一律 `after_price / before_price`；型別對映與「未知類別即 raise」見
`sources/finmind.py:dividend_rows`。
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from twquant.config import ServingConfig
from twquant.data import schemas
from twquant.data.serving import schema as sch
from twquant.data.serving import store
from twquant.data.serving.sync import upsert
from twquant.data.sources.finmind import dividend_rows
from twquant.data.warehouse import Warehouse

TABLE = "corporate_events"
KEY = ["date", "stock_id"]
_FULL_WIDTH = 10  # 有資料的檔案有 10 欄；width < 10 是 backfill 寫的「無資料」佔位檔


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staging", default="D:/project/twquant/data/staging/finmind_dividend_result")
    ap.add_argument("--lake", default="D:/project/twquant/data/lake_rebuild")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    if not (args.check or args.write):
        raise SystemExit("請指定 --check 或 --write")

    staging = pathlib.Path(args.staging)
    if not staging.exists():
        raise SystemExit(f"staging 不存在：{staging}")

    raw: list[dict[str, object]] = []
    placeholders = 0
    for f in sorted(staging.glob("*.parquet")):
        df = pl.read_parquet(f)
        if df.width < _FULL_WIDTH:
            placeholders += 1
            continue
        raw.extend(df.to_dicts())
    print(f"staging：{len(raw):,} 列（另有 {placeholders} 個無資料佔位檔）", flush=True)

    mapped = dividend_rows(raw)  # 未知類別／價格 ≤ 0 會在這裡 raise
    out = schemas.validate(TABLE, pl.DataFrame(mapped))
    dup = out.height - out.select(KEY).unique().height
    if dup:
        raise SystemExit(f"(date, stock_id) 有 {dup} 筆重複——主鍵不成立，停止。")

    by_type = out.group_by("event_type").len().sort("len", descending=True)
    print(
        f"對映後 {out.height:,} 筆、{out['stock_id'].n_unique():,} 檔、"
        f"{out['date'].min()} ~ {out['date'].max()}",
        flush=True,
    )
    print(by_type)
    print(
        f"adj_factor {out['adj_factor'].min():.4f} ~ {out['adj_factor'].max():.4f}；"
        f">1（增資價高於市價）{out.filter(pl.col('adj_factor') > 1.0).height} 筆",
        flush=True,
    )

    if args.check:
        print("\n--check 完成，未寫入。", flush=True)
        return

    wh = Warehouse(args.lake)
    wh.write("clean", TABLE, out, source="finmind")
    cfg = ServingConfig(pg_schema="serving")
    spec = sch.spec_of(TABLE)
    with store.connect(cfg) as conn:
        sch.ensure_schema(conn, cfg.pg_schema)
        with conn.cursor() as cur:
            upsert(cur, cfg.pg_schema, spec, out.select([c for c, _ in spec.columns]))
        conn.commit()
    print(f"\n已寫入湖與 PG：{out.height:,} 筆", flush=True)


if __name__ == "__main__":
    main()
