"""擇時反事實套到**全部**結果包：大盤在 200 日線下就空手，每一支會長什麼樣。

規則、成本估計、無前視處理與 timing_coverage.py 相同（Faber 2007 原版，不掃參數）。
短窗口（2022-07~2026-02）只含一個空頭體制，那裡的數字**參考價值遠低於長窗口**——
一次事件不是統計。兩個視窗分開列，不混。
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
SMA_DAYS = 200
ROUND_TRIP_COST = 0.005
DAYS_PER_YEAR = 244


def _stats(r: np.ndarray, dates: list[dt.date]) -> dict[str, float]:
    eq = np.cumprod(1 + r)
    yrs = max((dates[-1] - dates[0]).days / 365.25, 1e-9)
    mdd = float((eq / np.maximum.accumulate(eq) - 1).min())
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
        .with_columns(pl.col("bull").shift(1).alias("exposed"))
        .drop_nulls("exposed")
        .select("date", "exposed")
    )

    rows: list[dict[str, object]] = []
    for d in sorted(R.iterdir()):
        if not d.is_dir() or not (d.name.startswith("tracks") or d.name.startswith("ladder")):
            continue
        for f in sorted(d.glob("*_1x.json")):
            b = json.loads(f.read_text(encoding="utf-8"))
            if "equity" not in b or len(b["equity"]) < 250:
                continue
            eq = (
                pl.DataFrame(
                    [(dt.date.fromisoformat(e[0]), float(e[1])) for e in b["equity"]],
                    schema=["date", "equity"],
                    orient="row",
                )
                .with_columns(pl.col("equity").pct_change().alias("r_s"))
                .drop_nulls("r_s")
            )
            j = eq.join(idx, on="date", how="inner").sort("date")
            r, ex = j["r_s"].to_numpy(), j["exposed"].to_numpy().astype(bool)
            rt = np.where(ex, r, 0.0).copy()
            sw = np.where(ex[1:] != ex[:-1])[0] + 1
            rt[sw] -= ROUND_TRIP_COST / 2
            dates = j["date"].to_list()
            base, timed = _stats(r, dates), _stats(rt, dates)
            rows.append(
                {
                    "dir": d.name,
                    "file": f.stem,
                    "long_window": "_lw" in d.name,
                    "credit_dividends": d.name.endswith("_div"),
                    "base": base,
                    "timed": timed,
                    "switches": len(sw),
                    "mdd_gain_pp": (abs(base["mdd"]) - abs(timed["mdd"])) * 100,
                }
            )

    out = R / "reports/timing_all.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    for lw, label in (
        (True, "長窗口 2017-01~2026-02（2,227 日，三個空頭體制）"),
        (False, "短窗口 2022-07~2026-02（886 日，**只有一個空頭體制，參考價值低**）"),
    ):
        sub = [x for x in rows if x["long_window"] is lw and not x["credit_dividends"]]
        if not sub:
            continue
        print(f"\n{'=' * 96}\n{label}　·　價格報酬　·　1×　·　{len(sub)} 支")
        print(
            f"{'策略':38s} | {'原版 CAGR':>9s} {'MDD':>8s} {'Sh':>5s} | {'擇時 CAGR':>9s} {'MDD':>8s} {'Sh':>5s} | {'MDD改善':>7s}"
        )
        for x in sorted(sub, key=lambda y: y["timed"]["mdd"], reverse=True):
            b, t = x["base"], x["timed"]
            flag = (
                " ←≤30%" if abs(t["mdd"]) <= 0.30 else (" ←≤40%" if abs(t["mdd"]) <= 0.40 else "")
            )
            print(
                f"{x['file'][:38]:38s} | {b['cagr'] * 100:>+8.2f}% {b['mdd'] * 100:>7.2f}% {b['sharpe']:>+5.2f} | "
                f"{t['cagr'] * 100:>+8.2f}% {t['mdd'] * 100:>7.2f}% {t['sharpe']:>+5.2f} | {x['mdd_gain_pp']:>+6.1f}pp{flag}"
            )
        n30 = sum(1 for x in sub if abs(x["timed"]["mdd"]) <= 0.30)
        n40 = sum(1 for x in sub if abs(x["timed"]["mdd"]) <= 0.40)
        print(f"  擇時後 MDD ≤ 30%：{n30}／{len(sub)}　≤ 40%：{n40}／{len(sub)}")
    print(f"\n→ {out}")


if __name__ == "__main__":
    main()
