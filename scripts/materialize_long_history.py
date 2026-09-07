"""T-167：把 2015-06-01~2019-12-31 的因子物化出來（長窗口回測的前置）。

**耗時是量出來的，不是估的**：`PgTableSource(cache=True, since=...)` 之下，
首日 73.7 秒（一次性把來源表載進記憶體），**之後穩態 3.7 秒/日**。
1,128 個交易日單程序約 **1.16 小時**——不需要分片。

（`rematerialize_price_factors.py` 的 docstring 寫「約 100 秒/日 → 45 小時」，
那個數字把一次性的快取載入攤到每一天，而且含 broker 因子。實測差 20 倍以上。）

**排除 7 個因子**：
- `chip.broker_*` 三個：`broker_trades` 只有 2022 起，長歷史算不出東西；
  而且它們每天無條件全撈 5,700 萬列（單次 286 秒，T-150 未修）——
  跑 1,128 天要 90 小時，全部為了空結果。
- `ml.*` 四個：那是模型分數表（`ml_scores`），不是可計算的因子。

剩下的 37 個**完整包含** composite 集合（`price.reversal_5d`／
`price.residual_reversal_5d`／`fundamental.revenue_yoy`），故 `composite_daily`
會照常重算（D-057 的三種情形裡最單純的那種）。

用法：
    python scripts/materialize_long_history.py --from 2015-06-01 --to 2019-12-31
    python scripts/materialize_long_history.py --from 2015-06-01 --to 2019-12-31 --shard 0/4
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import twquant.factors.builtins  # noqa: F401  # 副作用：註冊全因子
from twquant.config import ServingConfig, load_config
from twquant.data.serving import materialize as smat
from twquant.data.serving import schema as sch
from twquant.data.serving import store
from twquant.data.serving.pg_view import PgTableSource
from twquant.factors import registry

_CONFIG = pathlib.Path(__file__).resolve().parents[1] / "configs" / "serving.yaml"
_EXCLUDE_PREFIXES = ("chip.broker", "ml.")


def factors_to_build() -> list[str]:
    """全部註冊因子扣掉 broker 與 ml（理由見 module docstring）。"""
    return sorted(f for f in registry.list_factors() if not f.startswith(_EXCLUDE_PREFIXES))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="lo", type=dt.date.fromisoformat, required=True)
    ap.add_argument("--to", dest="hi", type=dt.date.fromisoformat, required=True)
    ap.add_argument("--shard", default="0/1", help="i/n：只跑日期序號 %% n == i 的日子")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    i, n = (int(x) for x in args.shard.split("/"))
    if not 0 <= i < n:
        raise SystemExit(f"--shard 須滿足 0 ≤ i < n，得到 {args.shard}")

    cfg = load_config(_CONFIG, ServingConfig)
    factors = factors_to_build()
    missing = set(cfg.composite_factors) - set(factors)
    if missing:
        # 部分重疊時 `_materialize_on` 會 raise；在這裡先擋，訊息比它清楚（D-057）
        raise SystemExit(
            f"因子子集未完整包含 composite 集合，缺 {sorted(missing)}"
            "——那會讓 composite_daily 與因子表對同一個因子有兩個版本"
        )
    print(f"物化 {len(factors)} 個因子（已排除 broker 與 ml）")

    # `since` 必須涵蓋**最長**的因子回看，否則輸出會缺前段（該參數的 docstring 明文警告）
    max_lb = max(registry.get(f).lookback_days for f in factors)
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
            r = smat._materialize_on(source, conn, cfg, d, list(factors), trim_lookback=True)
            if k % 50 == 0 or k == len(mine):
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
