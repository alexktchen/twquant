"""把 FinMind 籌碼 staging（每檔一 parquet）映射成 `institutional_daily`／`margin_daily`，寫進資料湖與 PG。

用法：
    python scripts/load_finmind_chip.py institutional <staging_dir> --check     # 只驗證，不寫
    python scripts/load_finmind_chip.py institutional <staging_dir> --write     # 驗證通過才寫
    python scripts/load_finmind_chip.py margin        <staging_dir> --write

規則（T-092 AC-2）：
- 映射用 `sources/finmind.py` 的 `institutional_net`／`margin_balances`（已對 2026-08-28 全市場 25/25 逐位元驗證）。
- **重疊日期不覆寫官方資料**：湖裡已有分區的日期只做一致性比對（逐 (date, stock_id) 比值），有任何不一致即 raise
  ——映射錯了不能靜默流進去（黃金規則 5）。只寫湖與 PG 都沒有的日期。
- available_at 沿用 clean.py 慣例：法人 18:00、融資 21:00（D-008：融資以 T-1 進訊號，因子端負責前移）。
- 寫入走 `Warehouse.write("clean", …, source="finmind")`（pandera 驗證）與 `serving.sync.upsert`。
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys
import time

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from twquant.config import ServingConfig
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.serving import schema as sch
from twquant.data.serving import store
from twquant.data.serving.sync import upsert
from twquant.data.sources.finmind import institutional_net, margin_balances
from twquant.data.warehouse import Warehouse

_SPEC = {
    "institutional": (
        "institutional_daily",
        institutional_net,
        18,
        ["foreign_net", "trust_net", "dealer_net"],
    ),
    "margin": ("margin_daily", margin_balances, 21, ["margin_balance", "short_balance"]),
}


def _load_staging(staging: pathlib.Path) -> list[dict]:
    rows: list[dict] = []
    files = sorted(staging.glob("*.parquet"))
    for f in files:
        df = pl.read_parquet(f)
        if "date" not in df.columns:  # 佔位列（該檔無資料）
            continue
        rows.extend(df.to_dicts())
    print(f"staging {len(files):,} 檔 → {len(rows):,} 原始列", flush=True)
    return rows


def _to_frame(mapped: list[dict], hour: int, value_cols: list[str]) -> pl.DataFrame:
    df = pl.DataFrame(mapped)
    return df.with_columns(
        pl.col("date")
        .map_elements(
            lambda d: dt.datetime(d.year, d.month, d.day, hour, 0, tzinfo=TZ),
            return_dtype=schemas.TW_DT,
        )
        .alias("available_at")
    ).select(["date", "stock_id", *value_cols, "available_at"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=list(_SPEC))
    ap.add_argument("staging")
    ap.add_argument("--lake", default="D:/project/twquant/data/lake_rebuild")
    ap.add_argument("--check", action="store_true", help="只驗證重疊日期一致性")
    ap.add_argument("--write", action="store_true", help="驗證通過後寫入非重疊日期")
    args = ap.parse_args()
    if not (args.check or args.write):
        raise SystemExit("請指定 --check 或 --write")

    table, mapper, hour, value_cols = _SPEC[args.kind]
    t0 = time.perf_counter()
    mapped = mapper(_load_staging(pathlib.Path(args.staging)))
    df = _to_frame(mapped, hour, value_cols)
    days = sorted(df["date"].unique().to_list())
    print(
        f"映射後 {df.height:,} 列、{len(days):,} 日、{df['stock_id'].n_unique():,} 檔（{days[0]} ~ {days[-1]}）",
        flush=True,
    )

    wh = Warehouse(args.lake)
    existing = set(wh.dates_in_range(table, days[0], days[-1]))
    overlap = [d for d in days if d in existing]
    fresh = [d for d in days if d not in existing]
    print(
        f"湖已有 {len(existing):,} 日；重疊 {len(overlap):,} 日、新增 {len(fresh):,} 日", flush=True
    )

    # 重疊日期：逐 (date, stock_id) 比對，任何不一致即 raise
    if overlap:
        official = (
            wh.scan(table)
            .filter(pl.col("date").is_in(overlap))
            .select(["date", "stock_id", *value_cols])
            .collect()
        )
        mine = df.filter(pl.col("date").is_in(overlap)).select(["date", "stock_id", *value_cols])
        j = official.join(mine, on=["date", "stock_id"], how="inner", suffix="_fm")
        bad = j.filter(pl.any_horizontal([pl.col(c) != pl.col(f"{c}_fm") for c in value_cols]))
        only_official = official.join(mine, on=["date", "stock_id"], how="anti").height
        only_mine = mine.join(official, on=["date", "stock_id"], how="anti").height
        print(
            f"重疊比對：共同 {j.height:,} 列，值不同 {bad.height:,}；只在官方 {only_official:,}、只在 FinMind {only_mine:,}",
            flush=True,
        )
        if bad.height:
            print(bad.head(10))
            raise SystemExit(f"重疊日期有 {bad.height:,} 列值不一致——映射或來源有問題，停止。")
    if not args.write:
        print("--check 完成，未寫入。", flush=True)
        return

    out = df.filter(pl.col("date").is_in(fresh))
    if out.is_empty():
        print("無新增日期可寫。", flush=True)
        return
    wh.write("clean", table, out, source="finmind")
    cfg = ServingConfig(pg_schema="serving")
    spec = sch.spec_of(table)
    with store.connect(cfg) as conn, conn.cursor() as cur:
        upsert(cur, cfg.pg_schema, spec, out.select([c for c, _ in spec.columns]))
        conn.commit()
    print(
        f"已寫入湖與 PG：{out.height:,} 列、{len(fresh):,} 日；耗時 {(time.perf_counter() - t0) / 60:.1f} 分鐘",
        flush=True,
    )


if __name__ == "__main__":
    main()
