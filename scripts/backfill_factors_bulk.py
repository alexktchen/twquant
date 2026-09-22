"""整批補算因子（T-273）：一次算完整個面板，而不是逐日重跑。

    uv run --all-groups python scripts/backfill_factors_bulk.py \
        --factors price.kd_k_9,price.rsi_14 --from 2018-11-01 --to 2026-08-31

**為什麼需要這支**：`serving materialize` 是逐日路徑——每一天都重讀歷史、重算整個面板、
只留那一天。實測即使給了 `--factors` 與 `--trim-lookback`，單日仍要 **3 分 21 秒**，
1,900 個交易日要 **106 小時**。滾動窗因子沒有理由這樣算。

**安全條件**：只有「逐日算與整批算等價」的因子能走這條路。註冊表有 `anchor_invariant`
旗標宣告這件事，但**那個旗標沒有任何程式在檢查**（全 repo 只有一條測試斷言用到它）。
所以本腳本不信旗標：預設會**抽樣實際比對**整批結果與逐日結果，不一致即中止。
`chip.broker_*` 那類 expanding 統計會在這一關被擋下。

輸出與 `serving materialize` 一致：`factor_values`（value/z/pct）與 `factor_cross_stats`。
**不寫 `composite_daily`**——綜合分要全部成分因子齊備才有意義，補算單支因子時不重算。
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time

import polars as pl

import twquant.factors.builtins  # noqa: F401  # 匯入觸發因子註冊
from twquant.config import ServingConfig, load_config
from twquant.data.serving import schema as sch
from twquant.data.serving import store
from twquant.data.serving.pg_view import PgTableSource
from twquant.data.serving.sync import upsert
from twquant.factors.compute import compute
from twquant.factors.registry import get
from twquant.factors.standardize import standardize

CONFIGS = __import__("pathlib").Path(__file__).resolve().parents[1] / "configs"
_UNIVERSE = r"^[1-9][0-9]{3}$"  # 與 materialize._UNIVERSE 一致：四碼普通股
CHUNK = 200_000


def _standardise(fdf: pl.DataFrame) -> pl.DataFrame:
    """逐 (date, factor) 算 pct 與 z——逐字照抄 `materialize._compute_universe`。

    這裡若與物化路徑有任何差異，同一支因子就會有兩個版本的 z，
    而下游（模型特徵、綜合分）看不出來。故不重寫邏輯，只換成一次吃多天。
    """
    n_over = pl.col("value").count().over(["date", "factor"])
    ranked = fdf.with_columns(
        (
            (pl.col("value").rank().over(["date", "factor"]) - 1)
            / pl.max_horizontal(n_over - 1, pl.lit(1))
        ).alias("pct")
    )
    zdf = standardize(fdf).rename({"value": "z"}).select("date", "stock_id", "factor", "z")
    return ranked.join(zdf, on=["date", "stock_id", "factor"], how="inner").filter(
        pl.col("z").is_finite()
    )


def _verify(
    bulk: pl.DataFrame, source: PgTableSource, names: list[str], days: list[dt.date]
) -> None:
    """抽樣比對：整批算出的 value 必須等於逐日算出的 value。

    這是本腳本唯一的安全保證。`anchor_invariant` 旗標只是宣告，沒有程式在驗它。

    **逐日這邊不裁切**（`trim_lookback=False`），因為那是 `serving materialize` 的預設，
    也就是 `factor_values` 現有資料的產生方式。拿裁切版來比會比到另一件事：
    實測 `--trim-lookback` 會讓宇宙少掉幾檔（歷史湊不滿），且對 EWM 因子還會改變數值。
    """
    for day in days:
        per_day = (
            compute(names, source.view(day), dates=[day])
            .filter(pl.col("stock_id").str.contains(_UNIVERSE))
            .drop_nulls("value")
            .sort("factor", "stock_id")
        )
        got = (
            bulk.filter(pl.col("date") == day)
            .select("date", "stock_id", "factor", "value")
            .sort("factor", "stock_id")
        )
        if per_day.height != got.height:
            raise SystemExit(
                f"{day} 抽樣比對失敗：逐日 {per_day.height} 列、整批 {got.height} 列。"
                "該因子不是 anchor-invariant，不得整批補算。"
            )
        diff = (
            per_day.select("factor", "stock_id", pl.col("value").alias("a"))
            .join(
                got.select("factor", "stock_id", pl.col("value").alias("b")),
                on=["factor", "stock_id"],
            )
            .filter((pl.col("a") - pl.col("b")).abs() > 1e-9)
        )
        if diff.height:
            raise SystemExit(
                f"{day} 抽樣比對失敗：{diff.height} 列數值不同（最大差 "
                f"{(diff['a'] - diff['b']).abs().max():.3g}）。不得整批補算。"
            )
        print(f"  抽樣 {day} 比對通過（{got.height:,} 列）", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--factors", required=True, help="逗號分隔的因子名")
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", required=True)
    ap.add_argument("--verify-days", type=int, default=3, help="抽樣比對幾天（0＝不驗，不建議）")
    ap.add_argument(
        "--verify-only",
        action="store_true",
        help="只驗證不寫入，且略過 anchor_invariant 的宣告檢查——"
        "用來替『尚未宣告』的因子取得證據，決定該不該宣告",
    )
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if not os.environ.get("TWQUANT_PG_DSN"):
        raise SystemExit("缺 TWQUANT_PG_DSN")
    names = [f.strip() for f in args.factors.split(",") if f.strip()]
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    not_invariant = [n for n in names if not get(n).anchor_invariant]
    if not_invariant and not args.verify_only:
        raise SystemExit(
            f"這些因子未宣告 anchor_invariant，不得整批補算：{', '.join(not_invariant)}"
            "——expanding 統計（如 chip.broker_*）逐日與整批結果不同。"
        )

    cfg = load_config(CONFIGS / "serving.yaml", ServingConfig)
    conn = store.connect(cfg)
    try:
        source = PgTableSource(conn, cfg.pg_schema)
        t0 = time.perf_counter()
        # 用迄日的視圖算整段：滾動窗在 t 只看得到 <= t 的資料，故與逐日等價
        # ——這正是 `_verify` 要實際驗證的那個假設。
        raw = compute(names, source.view(end), dates=None)
        bulk = raw.filter(
            pl.col("stock_id").str.contains(_UNIVERSE) & pl.col("date").is_between(start, end)
        ).drop_nulls("value")
        print(
            f"整批計算 {bulk.height:,} 列、{bulk['date'].n_unique():,} 天"
            f"（{time.perf_counter() - t0:.0f} 秒）",
            flush=True,
        )
        if bulk.is_empty():
            raise SystemExit("整批計算結果為空——不寫入任何東西")

        if not_invariant:
            print(
                f"（--verify-only：{', '.join(not_invariant)} 尚未宣告 anchor_invariant，"
                "本次只取證據、不寫入）",
                flush=True,
            )
        if args.verify_days:
            all_days = sorted(bulk["date"].unique().to_list())
            step = max(1, len(all_days) // (args.verify_days + 1))
            _verify(bulk, source, names, all_days[step::step][: args.verify_days])

        if args.verify_only:
            print()
            print("[OK] 只驗證模式：比對全數通過，未寫入任何資料。")
            return
        merged = _standardise(bulk)
        rows = merged.select("factor", "stock_id", "date", "value", "z", "pct")
        cross = merged.group_by(["date", "factor"]).agg(
            pl.col("value").mean().alias("mean"),
            pl.col("value").std().alias("std"),
            pl.len().cast(pl.Int32).alias("n_universe"),
        )
        sch.ensure_schema(conn, cfg.pg_schema)
        with conn.cursor() as cur:
            for i in range(0, rows.height, CHUNK):
                upsert(cur, cfg.pg_schema, sch.spec_of("factor_values"), rows.slice(i, CHUNK))
                print(f"  寫入 {min(i + CHUNK, rows.height):,}/{rows.height:,}", flush=True)
            upsert(cur, cfg.pg_schema, sch.spec_of("factor_cross_stats"), cross)
        conn.commit()
    finally:
        conn.close()
    print(f"[OK] {rows.height:,} 列 → factor_values（共 {time.perf_counter() - t0:.0f} 秒）")


if __name__ == "__main__":
    main()
