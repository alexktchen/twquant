"""一次性回補：對真實資料湖逐日物化全部因子 → PG（serving schema）。

本機原生 IO 跑，比 docker Windows bind-mount 快約 10 倍（~10s/交易日 vs ~108s）。
逐日推進 as_of，維持 PIT；upsert 冪等，中斷後重跑會續傳（已完成日重算但不重複）。

用法（在 worktree 目錄）：
  # PowerShell
  $env:TWQUANT_PG_DSN='postgresql://twquant:twquant@localhost:5433/twquant'
  uv run --group serving python scripts/backfill_factors.py D:/project/twquant/data

  # 指定範圍（省略則全歷史）
  uv run --group serving python scripts/backfill_factors.py D:/project/twquant/data 2021-01-04 2026-07-08
"""

from __future__ import annotations

import datetime as dt
import sys
import time

from twquant.config import ServingConfig
from twquant.data.serving import materialize as m
from twquant.data.serving.sync import partition_dates
from twquant.data.warehouse import Warehouse


def main() -> None:
    data_dir = sys.argv[1] if len(sys.argv) > 1 else "D:/project/twquant/data"
    wh = Warehouse(data_dir)
    cfg = ServingConfig(pg_schema="serving")
    all_days = partition_dates(wh, "prices_daily")
    if not all_days:
        raise SystemExit(f"資料湖無 prices_daily 分區：{data_dir}")
    start = dt.date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else all_days[0]
    end = dt.date.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else all_days[-1]
    days = [d for d in all_days if start <= d <= end]

    print(f"物化 {len(days)} 個交易日 {start} → {end} → serving（{cfg.dsn_env}）", flush=True)
    t0 = time.perf_counter()
    for i, day in enumerate(days, 1):
        r = m.materialize_day(wh, cfg, day)
        elapsed = time.perf_counter() - t0
        eta = elapsed / i * (len(days) - i)
        print(
            f"[{i}/{len(days)}] {r.as_of}: {r.factor_rows} 因子列 / {r.factors} 因子"
            f"  （已 {elapsed / 60:.0f}m，估剩 {eta / 60:.0f}m）",
            flush=True,
        )
    print(f"完成：{len(days)} 日、耗時 {(time.perf_counter() - t0) / 60:.1f} 分鐘", flush=True)


if __name__ == "__main__":
    main()
