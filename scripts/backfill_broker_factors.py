"""T-271：分段補算分點因子 `chip.broker_*`。

    uv run --all-groups python scripts/backfill_broker_factors.py --from 2022-03-01 --to 2026-08-31

**為什麼要分段**：`broker_trades` 有 5,770 萬列。一次展開會把 PostgreSQL 連同 Docker
一起壓垮——2026-09-22 實際發生（`server closed the connection unexpectedly`，整個 stack 重啟，
所幸 WAL 當機復原完整、資料一列未失）。逐日路徑同樣不可行：單日 > 10 分鐘、全期 190 小時以上。

**分段怎麼保持正確**：隔日沖判定是 expanding 統計，早期分段若只看自己那一段，
會把「還沒累積夠事件」的分點誤判成正常分點——那是靜默的錯值，不會報錯。故分兩趟：

  第一趟  逐年算**事件**（`daytrade_events`，純區域聚合）→ 接起來做**一次全期累計**
          （`cumulate_events`）。累計的對象是「每分點每結算日一列」的小表，撐得住。
  第二趟  逐年算乾淨面板（`clean_daily`，注入第一趟的全期 state）→ 接起來。
          面板是「每檔每日一列」（約 220 萬列），也撐得住。
  最後    在完整面板上算三個因子的滾動窗 → 標準化 → 寫入。

滾動窗必須在**接好的完整面板**上算，不能逐年算：跨年邊界的 20 日窗會缺前一年的尾巴。

因子的數學一律呼叫 `factors.broker` 的共用函數，不在這裡重寫——兩套實作會漂移，
而漂移的後果是同一支因子有兩個版本的值，下游看不出來。
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import sys
import time
from typing import Any

import polars as pl

import twquant.factors.builtins  # noqa: F401  # 匯入觸發因子註冊
from twquant.config import ServingConfig, load_config
from twquant.data.serving import schema as sch
from twquant.data.serving import store
from twquant.data.serving.pg_view import PgTableSource
from twquant.data.serving.sync import upsert
from twquant.factors.broker import (
    clean_daily,
    concentration_values,
    count_diff_values,
    cumulate_events,
    daytrade_events,
    persistence_values,
)
from twquant.factors.registry import get
from twquant.factors.standardize import standardize

CONFIGS = pathlib.Path(__file__).resolve().parents[1] / "configs"
_UNIVERSE = r"^[1-9][0-9]{3}$"
CHUNK = 200_000
FACTORS = (
    "chip.broker_concentration_20d",
    "chip.broker_count_diff_5d",
    "chip.broker_persistence_10d",
)


def _segments(start: dt.date, end: dt.date, months: int) -> list[tuple[dt.date, dt.date]]:
    """把 [start, end] 切成每段 `months` 個月的閉區間。"""
    if months < 1:
        raise SystemExit(f"--segment-months 須 >= 1，得到 {months}")
    out: list[tuple[dt.date, dt.date]] = []
    cur = start
    while cur <= end:
        y, m = divmod((cur.year * 12 + cur.month - 1) + months, 12)
        nxt = dt.date(y, m + 1, 1)
        out.append((cur, min(end, nxt - dt.timedelta(days=1))))
        cur = nxt
    return out


def _standardise(fdf: pl.DataFrame) -> pl.DataFrame:
    """逐 (date, factor) 算 pct 與 z——與 `materialize._compute_universe` 逐字相同。"""
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", required=True)
    ap.add_argument("--dry-run", action="store_true", help="算完就停，不寫入")
    ap.add_argument(
        "--segment-months",
        type=int,
        default=6,
        help="每段幾個月。實測整年一段峰值 6.67 GB——今晚已有一次把 PG 連同 Docker 壓垮的紀錄，"
        "故預設半年把峰值砍半。調大只會更快一點，但風險是整段重來",
    )
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if not os.environ.get("TWQUANT_PG_DSN"):
        raise SystemExit("缺 TWQUANT_PG_DSN")
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    cfg = load_config(CONFIGS / "serving.yaml", ServingConfig)
    params: dict[str, Any] = dict(get(FACTORS[0]).params)
    segments = _segments(start, end, args.segment_months)
    print(f"分 {len(segments)} 段（每段 {args.segment_months} 個月）：{start} ~ {end}", flush=True)
    t0 = time.perf_counter()

    conn = store.connect(cfg)
    try:
        # ── 第一趟：逐年算事件，接起來做一次全期累計 ──────────────────
        ev: list[pl.DataFrame] = []
        for a, b in segments:
            v = PgTableSource(conn, cfg.pg_schema, since=a).view(b)
            e = daytrade_events(v).collect()
            ev.append(e)
            print(
                f"  [事件] {a}~{b}：{e.height:,} 列　累計 {time.perf_counter() - t0:.0f} 秒",
                flush=True,
            )
        state = cumulate_events(pl.concat(ev).lazy()).collect().lazy()
        del ev
        print(f"[1/3] 全期隔日沖統計完成（{time.perf_counter() - t0:.0f} 秒）", flush=True)

        # ── 第二趟：逐年算乾淨面板，注入全期 state ────────────────────
        panels: list[pl.DataFrame] = []
        vols: list[pl.DataFrame] = []
        for a, b in segments:
            v = PgTableSource(conn, cfg.pg_schema, since=a).view(b)
            d = clean_daily(v, params, state=state).collect()
            panels.append(d)
            vols.append(v.table("prices_daily").select("date", "stock_id", "volume").collect())
            print(
                f"  [面板] {a}~{b}：{d.height:,} 列　累計 {time.perf_counter() - t0:.0f} 秒",
                flush=True,
            )
        daily = pl.concat(panels).unique(subset=["date", "stock_id"], keep="last").lazy()
        vol = pl.concat(vols).unique(subset=["date", "stock_id"], keep="last").lazy()
        del panels, vols
        print(f"[2/3] 乾淨面板完成（{time.perf_counter() - t0:.0f} 秒）", flush=True)

        # ── 滾動窗必須在完整面板上算（跨年邊界需要前一年的尾巴）──────
        w = {n: int(get(n).params["window"]) for n in FACTORS}  # type: ignore[call-overload]
        frames = [
            concentration_values(daily, vol, w[FACTORS[0]]).with_columns(factor=pl.lit(FACTORS[0])),
            count_diff_values(daily, w[FACTORS[1]]).with_columns(factor=pl.lit(FACTORS[1])),
            persistence_values(daily, w[FACTORS[2]]).with_columns(factor=pl.lit(FACTORS[2])),
        ]
        fdf = (
            pl.concat([f.collect() for f in frames])
            .filter(
                pl.col("stock_id").str.contains(_UNIVERSE) & pl.col("date").is_between(start, end)
            )
            .drop_nulls("value")
        )
        print(
            f"[3/3] 三個因子共 {fdf.height:,} 列、{fdf['date'].n_unique():,} 天"
            f"（{time.perf_counter() - t0:.0f} 秒）",
            flush=True,
        )
        if fdf.is_empty():
            raise SystemExit("結果為空——不寫入任何東西")
        if args.dry_run:
            print("[OK] --dry-run：未寫入。")
            return

        merged = _standardise(fdf)
        rows = merged.select("factor", "stock_id", "date", "value", "z", "pct")
        cross = merged.group_by(["date", "factor"]).agg(
            pl.col("value").mean().alias("mean"),
            pl.col("value").std().alias("std"),
            pl.len().cast(pl.Int32).alias("n_universe"),
        )
        sch.ensure_schema(conn, cfg.pg_schema)
        # 每批各自提交：整批放同一個交易會撞 staging 暫存表（ON COMMIT DROP 只在提交時釋放），
        # 而且中途失敗會整批損失、進度也看不到。2026-09-21 踩過這個坑。
        for i in range(0, rows.height, CHUNK):
            with conn.cursor() as cur:
                upsert(cur, cfg.pg_schema, sch.spec_of("factor_values"), rows.slice(i, CHUNK))
            conn.commit()
            print(f"  寫入 {min(i + CHUNK, rows.height):,}/{rows.height:,}", flush=True)
        with conn.cursor() as cur:
            upsert(cur, cfg.pg_schema, sch.spec_of("factor_cross_stats"), cross)
        conn.commit()
    finally:
        conn.close()
    print(f"[OK] {rows.height:,} 列 → factor_values（共 {time.perf_counter() - t0:.0f} 秒）")


if __name__ == "__main__":
    main()
