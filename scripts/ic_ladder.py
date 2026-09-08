"""多因子 IC 階梯：對一串因子跑多視窗 RankIC，印表並記入研究登記簿的 IC 家族。

用法：
    TWQUANT_PG_DSN=... uv run --all-groups python scripts/ic_ladder.py \
        --factors chip.trust_streak,chip.foreign_streak --horizons 1,5,10,20,60 \
        --data-dir D:/project/twquant/data

為什麼是腳本而非 `twquant ic-report`：
- CLI 一次只跑一個因子，且研究登記簿落在 `configs/system.yaml` 的**相對** `data_dir`——
  在 worktree 執行會把登記簿切成兩半（CLAUDE.md §0.2、T-084）。本腳本強制 `--data-dir` 絕對路徑。
- 讀 PG serving、走 `PitView`（黃金規則 1），與 CLI 同一條 `compute` → `compute_ic_report` 路徑。

讀數提醒（報告裡要一併寫）：
- 前向報酬為 close(t)→close(t+h)。法人／融資資料在收盤後才可得，**1 日視窗不可執行**，只作參考。
- 視窗重疊時日頻 t 值高估；保守讀法以 t ÷ √h 折算（月度區塊標準誤的近似）。
- 全樣本 IC 不是 D-006 的 OOS promote 檢驗，兩者不得混用。
"""

from __future__ import annotations

import argparse
import hashlib
import math
import time

import polars as pl

import twquant.factors.builtins  # noqa: F401  # 匯入觸發全因子註冊
from twquant.config import ServingConfig, load_config
from twquant.data.serving import store
from twquant.data.serving.pg_view import PgTableSource
from twquant.factors.compute import compute
from twquant.factors.ic import compute_ic_report
from twquant.reporting.research_log import append_research_log

_CONFIGS = "configs"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--factors", required=True, help="逗號分隔的因子名")
    ap.add_argument("--horizons", default="1,5,10,20,60")
    ap.add_argument("--universe", default=r"^[1-9][0-9]{3}$", help="四碼普通股、排除權證")
    ap.add_argument(
        "--data-dir", required=True, help="研究登記簿落點，**必須是主 checkout 的絕對路徑**"
    )
    ap.add_argument("--no-log", action="store_true", help="只印不記")
    args = ap.parse_args()

    horizons = [int(x) for x in args.horizons.split(",")]
    scfg = load_config(f"{_CONFIGS}/serving.yaml", ServingConfig)
    conn = store.connect(scfg)
    src = PgTableSource(conn, scfg.pg_schema)
    as_of = src.latest_date("prices_daily")
    if as_of is None:
        raise SystemExit("PG 無行情資料")
    view = src.view(as_of)
    prices = (
        view.table("prices_daily")
        .select("date", "stock_id", "close")
        .filter(pl.col("stock_id").str.contains(args.universe))
        .collect()
    )
    print(
        f"as_of={as_of}  prices rows={prices.height:,}  {prices['date'].min()} .. {prices['date'].max()}",
        flush=True,
    )

    entries: list[dict[str, object]] = []
    for f in args.factors.split(","):
        t0 = time.time()
        try:
            fdf = compute([f], view).filter(pl.col("stock_id").str.contains(args.universe))
        except Exception as exc:  # noqa: BLE001 — 據實記下哪個因子算不出來，不靜默略過，也不讓一個因子拖垮整批
            print(f"{f:28s} FAILED: {type(exc).__name__}: {exc}", flush=True)
            continue
        if fdf.is_empty():
            print(f"{f:28s} 無值（因子在此期間算不出來）", flush=True)
            continue
        rep = compute_ic_report(f, fdf, prices, horizons)
        print(
            f"{f:28s} n={fdf.height:>9,} {fdf['date'].min()}..{fdf['date'].max()} ({time.time() - t0:.0f}s)",
            flush=True,
        )
        for h in rep.horizons:
            t_adj = h.t_stat / math.sqrt(h.horizon)
            print(
                f"    {h.horizon:>2}d IC{h.ic_mean:+.4f} t{h.t_stat:+6.1f} (t/√h{t_adj:+5.1f}) LS{h.ls_spread:+.4f}",
                flush=True,
            )
        phash = hashlib.sha256(f"{f}|{args.universe}|{args.horizons}".encode()).hexdigest()[:12]
        for h in rep.horizons:
            for name, val in (
                ("ic_mean", h.ic_mean),
                ("t_stat", h.t_stat),
                ("ls_spread", h.ls_spread),
            ):
                entries.append(
                    {
                        "entry_type": "ic_test",
                        "strategy": "",
                        "factor": f,
                        "params_hash": phash,
                        "date": as_of.isoformat(),
                        "metric_name": f"{name}_{h.horizon}d",
                        "metric_value": float(val),
                        "trial_scope": f"ic:{f}",
                    }
                )
    if entries and not args.no_log:
        path = append_research_log(args.data_dir, entries)
        print(f"\n研究登記簿寫入 {path}（{len(entries)} 筆）", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
