"""T-149：重物化 2023-02-20 ~ 06-30（T-136 修補區）的因子——**用歷史上實際存在的因子集**。

為什麼不能直接跑 `twquant serving materialize --from … --to …`（實測後放棄的做法）：

1. **它產出的因子集與歷史不一致**。實測 2023-02-20（唯一跑完的一天）有 **42** 個因子，
   而歷史上每一天都是 **35** 個。多出來的 7 個是 `chip.broker_*` ×3、`ml.*` ×2、
   `price.industry_*` ×2——把它們只補在這 89 天，等於在時間軸上挖一個反向的洞。
2. **它慢到不可行**。90 分鐘只完成 1 天。瓶頸是 `chip.broker_*` 三個因子每天都
   **無條件全撈** `serving.broker_trades`（5,700 萬列，單次約 286 秒）——`PgTableSource`
   沒有把日期條件推進 SQL。89 天 × 那個代價 ≈ 5 天。

為什麼這 89 天非重算不可：T-136 修補了該區間的價格，宇宙由約 790 檔變成約 3,700 檔。
橫斷面 z／pct 是**相對**量，宇宙變了就每一個因子值都變了。實測列數對照——
2023-04-10 是 29,077 列、2023-06-30 是 29,205 列，而區間外的 2023-07-03 是 52,640 列：
缺口區的因子值到現在仍算在那個砍半的宇宙上。

**這 89 天不重算，T-145 的重訓就會吃到一個「宇宙只有一半」的半年**——那正是 D-064
判定「凍結模型的每一個數字都不可信」的三個汙染之一。

用法：
    python scripts/rematerialize_2023_gap.py --dry-run
    python scripts/rematerialize_2023_gap.py            # 全段
    python scripts/rematerialize_2023_gap.py --shard 0/3  # 三程序平行
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

# 歷史上每一天實際存在的 35 個因子（量自 2023-07-03，區間外的正常日）。
# **刻意不含** chip.broker_*（每天全撈 5,700 萬列，且 ML 特徵不用）、ml.*（那是模型
# 分數不是特徵，由 `ml score` 落地）、price.industry_*（歷史從未物化）。
# 補這三類只會讓這 89 天與其餘全期不一致。
FACTORS = (
    "chip.foreign_net_10d",
    "chip.foreign_streak",
    "chip.inst_net_10d",
    "chip.margin_velocity_5d",
    "chip.short_margin_ratio",
    "chip.trust_net_10d",
    "chip.trust_streak",
    "fundamental.accruals",
    "fundamental.asset_growth",
    "fundamental.book_yield",
    "fundamental.dividend_yield",
    "fundamental.earnings_yield",
    "fundamental.gross_profitability",
    "fundamental.leverage",
    "fundamental.log_market_cap",
    "fundamental.revenue_accel",
    "fundamental.revenue_yoy",
    "fundamental.roe",
    "fundamental.sue",
    "price.box_breakout_60d",
    "price.double_bottom_120d",
    "price.flag_hold_50d",
    "price.high_52w",
    "price.hs_top_120d",
    "price.intraday_ret_20d",
    "price.mom_1d",
    "price.mom_20d",
    "price.mom_60d",
    "price.overnight_ret_20d",
    "price.residual_reversal_5d",
    "price.reversal_5d",
    "price.rs_market_20d",
    "price.vol_20d",
    "volume.turnover_z_20d",
    "volume.vol_ratio_5d",
)

LO, HI = dt.date(2023, 2, 20), dt.date(2023, 6, 30)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", default="0/1", help="i/n：只跑日期序號 % n == i 的日子")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    i, n = (int(x) for x in args.shard.split("/"))
    if not 0 <= i < n:
        raise SystemExit(f"--shard 須滿足 0 ≤ i < n，得到 {args.shard}")

    cfg = load_config(_CONFIG, ServingConfig)
    missing = set(cfg.composite_factors) - set(FACTORS)
    if missing:
        # D-057：靜默略過會讓 composite_daily 與 factor_values 對同一因子有兩個版本
        raise SystemExit(
            f"子集未完整包含 composite 集合，缺 {sorted(missing)}——"
            "composite_daily 會與因子表脫節。"
        )

    import twquant.factors.builtins  # noqa: F401
    from twquant.factors.registry import get as _get

    max_lb = max(_get(f).lookback_days for f in FACTORS)
    # `since` 必須涵蓋最長回看，否則因子輸出會缺前段（該參數的 docstring 明文警告）
    since = LO - dt.timedelta(days=max_lb * 2 + 60)
    print(f"重物化 {len(FACTORS)} 個因子（歷史因子集，不含 broker／ml／industry）")
    print(f"composite 集合 {sorted(cfg.composite_factors)} 已完整包含 ✓")
    print(f"PG 讀取下界 since={since}（最長回看 {max_lb} 天 × 2 + 60 緩衝）")

    conn = store.connect(cfg)
    try:
        source = PgTableSource(conn, cfg.pg_schema, cache=True, since=since)
        days = source.dates_in_range("prices_daily", LO, HI)
        mine = [d for k, d in enumerate(days) if k % n == i]
        print(f"\n{LO} ~ {HI}：共 {len(days)} 個交易日，本片（{i}/{n}）{len(mine)} 日")
        if args.dry_run:
            print("--dry-run，未執行。", flush=True)
            return
        sch.ensure_schema(conn, cfg.pg_schema)
        t0 = time.perf_counter()
        for k, d in enumerate(mine, 1):
            r = smat._materialize_on(source, conn, cfg, d, list(FACTORS), trim_lookback=True)
            if k % 5 == 0 or k == len(mine):
                el = time.perf_counter() - t0
                print(
                    f"  {k:4}/{len(mine)}  {d}  {r.factor_rows:6,} 列  "
                    f"{el / 60:.1f} 分（剩約 {el / k * (len(mine) - k) / 60:.0f} 分）",
                    flush=True,
                )
    finally:
        conn.close()
    print("完成。", flush=True)


if __name__ == "__main__":
    main()
