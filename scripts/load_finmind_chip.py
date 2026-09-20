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
import dataclasses
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

_HERE = pathlib.Path(__file__).resolve().parent


@dataclasses.dataclass(frozen=True)
class WriteTargets:
    """要寫進湖、要寫進 PG 的日期（**各自算**）與要做一致性比對的日期。

    2026-09-20 的教訓：兩個儲存體的內容可以不同步。只用湖判斷「新不新」，
    湖有而 PG 沒有的日期就會被跳過，而且回報成功——正是黃金規則 5 要消滅的失敗模式。
    """

    to_lake: list[dt.date]
    to_pg: list[dt.date]
    overlap: list[dt.date]  # 湖已有 → 與官方資料逐列比對

    @property
    def nothing_to_write(self) -> bool:
        return not self.to_lake and not self.to_pg


def write_targets(
    *, days: list[dt.date], lake_has: set[dt.date], pg_has: set[dt.date]
) -> WriteTargets:
    """由「來源有哪些日期」與「兩個儲存體各有哪些」算出要寫的日期。純函數。"""
    return WriteTargets(
        to_lake=[d for d in days if d not in lake_has],
        to_pg=[d for d in days if d not in pg_has],
        overlap=[d for d in days if d in lake_has],
    )


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


def _pg_dates(cfg: ServingConfig, table: str, start: dt.date, end: dt.date) -> set[dt.date]:
    """PG 裡該表在 [start, end] 已有的日期。表不存在＝一天都沒有（而不是「全部都有」）。"""
    with store.connect(cfg) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT to_regclass(%s)",
            (f"{cfg.pg_schema}.{table}",),
        )
        if cur.fetchone()[0] is None:
            return set()
        cur.execute(
            f'SELECT DISTINCT date FROM "{cfg.pg_schema}"."{table}" WHERE date BETWEEN %s AND %s',
            (start, end),
        )
        return {r[0] for r in cur.fetchall()}


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
    cfg = ServingConfig(pg_schema="serving")
    lake_has = set(wh.dates_in_range(table, days[0], days[-1]))
    pg_has = _pg_dates(cfg, table, days[0], days[-1])
    targets = write_targets(days=days, lake_has=lake_has, pg_has=pg_has)
    overlap = targets.overlap
    print(
        f"湖已有 {len(lake_has):,} 日、PG 已有 {len(pg_has):,} 日；"
        f"要寫湖 {len(targets.to_lake):,} 日、要寫 PG {len(targets.to_pg):,} 日"
        f"（重疊比對 {len(overlap):,} 日）",
        flush=True,
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

    if targets.nothing_to_write:
        print("湖與 PG 都已涵蓋，無須寫入。", flush=True)
        return
    spec = sch.spec_of(table)
    n_lake = n_pg = 0
    if targets.to_lake:
        lake_rows = df.filter(pl.col("date").is_in(targets.to_lake))
        wh.write("clean", table, lake_rows, source="finmind")
        n_lake = lake_rows.height
    if targets.to_pg:
        pg_rows = df.filter(pl.col("date").is_in(targets.to_pg))
        with store.connect(cfg) as conn, conn.cursor() as cur:
            upsert(cur, cfg.pg_schema, spec, pg_rows.select([c for c, _ in spec.columns]))
            conn.commit()
        n_pg = pg_rows.height
    print(
        f"已寫入：湖 {n_lake:,} 列／{len(targets.to_lake):,} 日、"
        f"PG {n_pg:,} 列／{len(targets.to_pg):,} 日；"
        f"耗時 {(time.perf_counter() - t0) / 60:.1f} 分鐘",
        flush=True,
    )


if __name__ == "__main__":
    main()
