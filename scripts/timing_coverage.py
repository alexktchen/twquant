"""擇時能救多少？——反事實：大盤在 200 日線下就空手，這四支會長什麼樣。

規則用 Faber (2007) 的原版：收盤 > 10 個月（≈200 交易日）簡單移動平均 ＝ 持有，否則空手。
**不掃參數**——這是文獻裡最常被過擬合的一條規則（Zakamulin 2014），只用原版。
訊號用 **t−1 的狀態決定 t 的曝險**（無前視）。空手日報酬記 0（保守：台幣活存約 1%，忽略）。
換手成本：每次狀態切換扣一次單邊成本（進出各一次＝一個來回），用 0.5% 的來回估計
（台股賣出證交稅 0.3% ＋ 兩邊手續費），這是估計不是引擎——引擎版在 P4 跑。

這不是「擇時好不好」的研究，是「這四支的病，擇時對不對症」：
若空手之後 MDD 仍 > 40%，那是選股的病，擇時治不了。
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import polars as pl
import psycopg

R = Path("D:/project/twquant/data")
SMA_DAYS = 200  # Faber 2007 的 10 個月；非調參對象
ROUND_TRIP_COST = 0.005  # 一次「賣光再買回」的估計成本；引擎版會用真實成本模型
DAYS_PER_YEAR = 244


def _stats(r: np.ndarray, dates: list[dt.date]) -> dict[str, float]:
    eq = np.cumprod(1 + r)
    yrs = (dates[-1] - dates[0]).days / 365.25
    peak = np.maximum.accumulate(eq)
    mdd = float((eq / peak - 1).min())
    sd = r.std()
    return {
        "cagr": float(eq[-1] ** (1 / yrs) - 1),
        "mdd": mdd,
        "sharpe": float(r.mean() / sd * math.sqrt(DAYS_PER_YEAR)) if sd else 0.0,
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn, conn.cursor() as cur:
        cur.execute(
            "select date, close from serving.index_daily where index_id='TAIEX' "
            "and date between '2016-01-01' and '2026-02-28' order by date"
        )
        idx = pl.DataFrame(cur.fetchall(), schema=["date", "close"], orient="row")
    idx = (
        idx.with_columns(pl.col("close").cast(pl.Float64))
        .with_columns((pl.col("close") > pl.col("close").rolling_mean(SMA_DAYS)).alias("bull"))
        # t 的曝險由 t−1 的狀態決定
        .with_columns(pl.col("bull").shift(1).alias("exposed"))
        .drop_nulls("exposed")
    )
    print(
        f"TAIEX {SMA_DAYS} 日線規則：2017-01-03 起持有日佔 "
        f"{float(idx.filter(pl.col('date') >= dt.date(2017, 1, 3))['exposed'].mean()):.0%}\n"
    )
    print(
        f"{'策略':10s} | {'原版 CAGR':>9s} {'MDD':>8s} {'Sharpe':>6s} | {'擇時版 CAGR':>10s} {'MDD':>8s} {'Sharpe':>6s} | {'切換次數':>5s} {'MDD 改善':>8s}"
    )

    rows: list[dict[str, object]] = []
    for cell, cfg in (
        ("LW-C1", "lw_c1_breakout"),
        ("LW-B2", "lw_b2_broad_capw"),
        ("LW-X1", "lw_x1_simple20"),
        ("LW-C1abs", "lw_c1_abs_threshold"),
    ):
        b = json.loads(
            (R / "tracks_lw_nodiv" / f"{cell}_{cfg}_1x.json").read_text(encoding="utf-8")
        )
        eq = (
            pl.DataFrame(
                [(dt.date.fromisoformat(e[0]), float(e[1])) for e in b["equity"]],
                schema=["date", "equity"],
                orient="row",
            )
            .with_columns(pl.col("equity").pct_change().alias("r_s"))
            .drop_nulls("r_s")
        )
        j = eq.join(idx.select("date", "exposed"), on="date", how="inner").sort("date")
        r = j["r_s"].to_numpy()
        ex = j["exposed"].to_numpy().astype(bool)
        # 切換：exposed 由 True→False 或 False→True；每次切換扣半個來回
        switches = int(np.sum(ex[1:] != ex[:-1]))
        r_timed = np.where(ex, r, 0.0).copy()
        sw_idx = np.where(ex[1:] != ex[:-1])[0] + 1
        r_timed[sw_idx] -= ROUND_TRIP_COST / 2
        dates = j["date"].to_list()
        base, timed = _stats(r, dates), _stats(r_timed, dates)
        print(
            f"{cell:10s} | {base['cagr'] * 100:>+8.2f}% {base['mdd'] * 100:>7.2f}% {base['sharpe']:>+6.2f} | "
            f"{timed['cagr'] * 100:>+9.2f}% {timed['mdd'] * 100:>7.2f}% {timed['sharpe']:>+6.2f} | "
            f"{switches:>5d} {(abs(base['mdd']) - abs(timed['mdd'])) * 100:>+7.1f}pp"
        )
        rows.append({"cell": cell, "base": base, "timed": timed, "switches": switches})

    out = R / "reports/timing_counterfactual.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n判準（D-092 P4 事前寫定）：擇時版 MDD ≤ 40% 才算「對症」；≤ 30% 是甲層。")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
