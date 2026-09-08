"""T-107／T-108：重物化被 `winsorize_mad` 壓成常數的稀疏籌碼因子。

用法：
    python scripts/rematerialize_sparse_chip_factors.py --from 2020-01-02 --to 2026-09-04
    python scripts/rematerialize_sparse_chip_factors.py --from 2020-01-02 --to 2026-09-04 --shard 0/4

**為什麼要重物化。** `winsorize_mad` 在 MAD = 0（過半股票同值，稀疏因子的常態）時
上下界同為 median，`clip` 把整個橫斷面壓成常數，z 全為 0。實測 `factor_values`：

| 因子 | 被壓成常數的日子 |
|---|---|
| `chip.trust_streak` | **1,622 / 1,622（100%）** |
| `chip.trust_net_10d` | **1,613 / 1,613（100%）** |
| `chip.foreign_streak` | 935 / 1,622（57.6%） |
| `chip.short_margin_ratio` | 48 / 1,618（3.0%） |

前兩個從**未有過任何一天**帶資訊——它們在每一代模型裡都只是一排 0，而沒有任何一關
報錯（黃金規則 5 的同型失效）。D-074 把 `chip.trust_streak` 稱為「先導最強的籌碼訊號」，
那個先導量測用的是原始值，模型吃的是 z。

`chip.broker_count_diff_5d`（5/5 日）**不在本腳本範圍**：broker 因子每天無條件全撈
5,700 萬列的 `serving.broker_trades`（單次約 286 秒，T-150 未修），為了 5 個日子付
24 分鐘不划算，且它不在任何策略或模型配置裡。

**與 composite 集合零交集**，故 `_materialize_on` 只寫因子表、不動 `composite_daily`
（D-057 的三種情形裡最單純的那種）。
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

# 實測 MAD = 0 而被壓成常數的籌碼因子（broker 類另議，見 module docstring）
FACTORS = (
    "chip.foreign_streak",
    "chip.short_margin_ratio",
    "chip.trust_net_10d",
    "chip.trust_streak",
)


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
    clash = set(cfg.composite_factors) & set(FACTORS)
    if clash:
        # 部分重疊時 `_materialize_on` 會 raise；在這裡先擋，訊息比它清楚
        raise SystemExit(
            f"子集與 composite 集合部分重疊：{sorted(clash)}。"
            "本腳本的前提是零交集（只寫因子表、不動 composite_daily）"
        )
    print(f"重物化 {len(FACTORS)} 個稀疏籌碼因子：{', '.join(FACTORS)}")

    # 瓶頸是 PG 讀取而非計算：`PgTableSource.scan()` 預設整表讀出，`trim_lookback`
    # 在讀出**之後**才過濾。`since` 把日期條件推進 SQL、`cache=True` 讓整段只讀一次。
    # `since` 必須涵蓋最長回看，否則因子輸出會缺前段。
    import twquant.factors.builtins  # noqa: F401
    from twquant.factors.registry import get as _get

    max_lb = max(_get(f).lookback_days for f in FACTORS)
    since = args.lo - dt.timedelta(days=max_lb * 2 + 60)
    print(f"PG 讀取下界 since={since}（最長回看 {max_lb} 天 × 2 + 60 緩衝）")

    conn = store.connect(cfg)
    try:
        source = PgTableSource(conn, cfg.pg_schema, cache=True, since=since)
        days = source.dates_in_range("prices_daily", args.lo, args.hi)
        mine = [d for k, d in enumerate(days) if k % n == i]
        print(f"\n{args.lo} ~ {args.hi}：共 {len(days)} 個交易日，本片（{i}/{n}）{len(mine)} 日")
        if args.dry_run:
            print("--dry-run，未執行。", flush=True)
            return
        sch.ensure_schema(conn, cfg.pg_schema)
        t0 = time.perf_counter()
        for k, d in enumerate(mine, 1):
            r = smat._materialize_on(source, conn, cfg, d, list(FACTORS), trim_lookback=True)
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
