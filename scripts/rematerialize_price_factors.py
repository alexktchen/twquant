"""T-142：全期子集重物化——T-139／T-140 改動過的價格因子。

用法：
    python scripts/rematerialize_price_factors.py --from 2020-01-02 --to 2026-09-04
    python scripts/rematerialize_price_factors.py --from 2022-01-01 --to 2022-12-31 --shard 0/5

為什麼要重物化：T-139／T-140 改了 `price.py` 的 9 處除法（`safe_ratio` 防零分母與溢位）
並加了跨距防護（`_lagged_return`），但 `factor_values` 裡是**改動之前**算出來的值。
不重物化，PG 裡的 22,988 列 `inf` 會一直留著，而 `inf` 通過每一道 null 檢查。

**範圍是刻意收窄的**。實測影響：改動後每天只有約 **11 列**變成 null（佔當日約 0.1%），
因為只有「往回數 N 列卻跨越數百個開市日」的荒謬情形才會被攔下。
全因子物化約 100 秒/日 × 1,622 日 ≈ 45 小時；只重算這 9 個因子快得多。

**為什麼子集裡有 `fundamental.revenue_yoy`**：D-057 規定因子子集若與 composite 集合
（`price.reversal_5d`、`price.residual_reversal_5d`、`fundamental.revenue_yoy`）**部分**
重疊即 raise——重算會得到不同的綜合分，不重算則兩張表對同一因子有兩個版本。
我們改到了其中兩個，故必須把第三個補齊，讓子集**完整包含** composite 集合。

2023-02-20 ~ 06-30 那段**不在本腳本範圍**：那裡的價格被 T-136 修補過，宇宙由
790 檔變成 3,700 檔，影響的是**所有**因子而不只這 9 個，須全因子重物化（另行執行）。

`--shard i/n` 供多程序平行：memory 記載物化是 O(n²)，單程序全期不可行，
分片跑才實際。分片以日期序號取模，故各片的工作量均勻。
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from twquant.config import ServingConfig, load_config
from twquant.data.serving import materialize as smat
from twquant.data.serving import schema as sch
from twquant.data.serving import store
from twquant.data.serving.pg_view import PgTableSource

_CONFIG = pathlib.Path(__file__).resolve().parents[1] / "configs" / "serving.yaml"

# T-139／T-140 改動過的因子（`price.py` 裡用到 `safe_ratio` 或 `_lagged_return` 的）
CHANGED = (
    "price.mom_1d",
    "price.mom_20d",
    "price.mom_60d",
    "price.reversal_5d",
    "price.residual_reversal_5d",
    "price.high_52w",
    "price.vol_20d",
    "price.rs_market_20d",
)
# D-057：子集須**完整包含** composite 集合，否則 `_materialize_on` 會 raise
COMPOSITE_FILLER = ("fundamental.revenue_yoy",)
FACTORS = tuple(sorted({*CHANGED, *COMPOSITE_FILLER}))

# T-136 修補過的日期：那裡宇宙整個變了，須全因子物化，不在本腳本範圍
SKIP_LO, SKIP_HI = dt.date(2023, 2, 20), dt.date(2023, 6, 30)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="lo", type=dt.date.fromisoformat, required=True)
    ap.add_argument("--to", dest="hi", type=dt.date.fromisoformat, required=True)
    ap.add_argument("--shard", default="0/1", help="i/n：只跑日期序號 % n == i 的日子")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    i, n = (int(x) for x in args.shard.split("/"))
    if not 0 <= i < n:
        raise SystemExit(f"--shard 須滿足 0 ≤ i < n，得到 {args.shard}")

    cfg = load_config(_CONFIG, ServingConfig)
    comp = set(cfg.composite_factors)
    missing = comp - set(FACTORS)
    if missing:
        # 靜默略過會讓 composite_daily 與 factor_values 對同一因子有兩個版本（D-057）
        raise SystemExit(
            f"子集未完整包含 composite 集合，缺 {sorted(missing)}。"
            "請把它們加進 COMPOSITE_FILLER，否則 composite_daily 會與因子表脫節。"
        )
    print(f"重物化 {len(FACTORS)} 個因子：{', '.join(FACTORS)}")
    print(f"composite 集合 {sorted(comp)} 已完整包含 ✓")

    conn = store.connect(cfg)
    try:
        source = PgTableSource(conn, cfg.pg_schema)
        days = source.dates_in_range("prices_daily", args.lo, args.hi)
        skipped = [d for d in days if SKIP_LO <= d <= SKIP_HI]
        days = [d for d in days if not (SKIP_LO <= d <= SKIP_HI)]
        mine = [d for k, d in enumerate(days) if k % n == i]
        print(
            f"\n{args.lo} ~ {args.hi}：共 {len(days) + len(skipped)} 個交易日，"
            f"跳過 T-136 修補區 {len(skipped)} 日（須全因子物化），"
            f"本片（{i}/{n}）{len(mine)} 日"
        )
        if args.dry_run:
            print("--dry-run，未執行。", flush=True)
            return
        sch.ensure_schema(conn, cfg.pg_schema)
        t0 = time.perf_counter()
        for k, d in enumerate(mine, 1):
            r = smat._materialize_on(source, conn, cfg, d, list(FACTORS))
            if k % 20 == 0 or k == len(mine):
                el = time.perf_counter() - t0
                print(
                    f"  {k:5}/{len(mine)}  {d}  {r.factor_rows:6,} 列  "
                    f"{el / 60:.1f} 分（剩約 {el / k * (len(mine) - k) / 60:.0f} 分）",
                    flush=True,
                )
    finally:
        conn.close()
    print("完成。", flush=True)


if __name__ == "__main__":
    main()
