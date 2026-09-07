"""長窗口四支為什麼輸給 0050——把「輸」拆成可歸因的三塊。

1. 成本拖累：0× 與 1× 的 CAGR 差，就是每年被交易成本吃掉的部分。
2. beta 與相關：對 TAIEX 逐日報酬回歸。beta > 1 代表跌的時候跌得更多，
   那不是選股失敗，是曝險過度。
3. 回撤的時間與形狀：最大回撤是一次崩盤（2020-03）還是慢性失血？
   對照 0050 同期的回撤，才知道是「市場跌」還是「策略額外跌」。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

import numpy as np
import polars as pl
import psycopg

R = Path("D:/project/twquant/data")


def _equity(path: Path) -> pl.DataFrame:
    b = json.loads(path.read_text(encoding="utf-8"))
    rows = [(dt.date.fromisoformat(e[0]), float(e[1])) for e in b["equity"]]
    return pl.DataFrame(rows, schema=["date", "equity"], orient="row").sort("date")


def _dd(eq: pl.DataFrame) -> tuple[float, dt.date, dt.date, dt.date | None]:
    """最大回撤與其 (峰, 谷, 回復日)。"""
    v = eq["equity"].to_numpy()
    d = eq["date"].to_list()
    peak_i, worst, worst_pair = 0, 0.0, (0, 0)
    for i in range(len(v)):
        if v[i] > v[peak_i]:
            peak_i = i
        dd = v[i] / v[peak_i] - 1
        if dd < worst:
            worst, worst_pair = dd, (peak_i, i)
    p, t = worst_pair
    rec = next((d[j] for j in range(t, len(v)) if v[j] >= v[p]), None)
    return worst, d[p], d[t], rec


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn, conn.cursor() as cur:
        cur.execute(
            "select date, close from serving.index_daily where index_id='TAIEX' "
            "and date between '2017-01-01' and '2026-02-28' order by date"
        )
        idx = pl.DataFrame(cur.fetchall(), schema=["date", "close"], orient="row")
        cur.execute(
            "select date, close from serving.prices_daily where stock_id='0050' "
            "and date between '2017-01-01' and '2026-02-28' order by date"
        )
        etf = pl.DataFrame(cur.fetchall(), schema=["date", "close"], orient="row")
    idx = idx.with_columns(pl.col("close").cast(pl.Float64).pct_change().alias("r_m"))
    etf_eq = etf.with_columns(pl.col("close").cast(pl.Float64).alias("equity")).select(
        "date", "equity"
    )
    # 0050 的 raw close **沒還原分割**（2025-06 有 1:4），拿它算自己的 MDD 會得到 −77% 的假值。
    # 這裡只用它算「策略峰→谷區間內 0050 跌多少」，而四支策略的谷都在 2022-10 以前，故安全。
    # 0050 自己的長窗口 MDD 用 benchmarks_two_windows.py（有還原分割）的 −36.38%。
    print(
        f"{'策略':12s} {'成本拖累':>9s} {'beta':>6s} {'相關':>6s} {'年化α':>8s} | {'MDD':>8s} {'峰':>10s} {'谷':>10s} {'回復':>10s} {'0050同期':>9s}"
    )
    rows: list[dict[str, object]] = []
    for cell, cfg in (
        ("LW-C1", "lw_c1_breakout"),
        ("LW-C1abs", "lw_c1_abs_threshold"),
        ("LW-B2", "lw_b2_broad_capw"),
        ("LW-X1", "lw_x1_simple20"),
    ):
        d = R / "tracks_lw_nodiv"
        m0 = json.loads((d / f"{cell}_{cfg}_0x.json").read_text(encoding="utf-8"))["metrics"]
        m1 = json.loads((d / f"{cell}_{cfg}_1x.json").read_text(encoding="utf-8"))["metrics"]
        drag = (m0["cagr"] - m1["cagr"]) * 100
        eq = _equity(d / f"{cell}_{cfg}_1x.json")
        j = (
            eq.with_columns(pl.col("equity").pct_change().alias("r_s"))
            .join(idx.select("date", "r_m"), on="date", how="inner")
            .drop_nulls()
        )
        rs, rm = j["r_s"].to_numpy(), j["r_m"].to_numpy()
        beta = float(np.cov(rs, rm)[0, 1] / np.var(rm))
        corr = float(np.corrcoef(rs, rm)[0, 1])
        alpha = float((rs.mean() - beta * rm.mean()) * 244 * 100)
        worst, p, t, rec = _dd(eq)
        # 0050 在策略峰→谷這段期間跌了多少
        e_win = etf_eq.filter(pl.col("date").is_between(p, t))["equity"].to_numpy()
        etf_same = (e_win.min() / e_win[0] - 1) * 100 if len(e_win) else float("nan")
        print(
            f"{cell:12s} {drag:>8.2f}pp {beta:>6.2f} {corr:>6.2f} {alpha:>+7.2f}% | "
            f"{worst * 100:>7.2f}% {p!s:>10s} {t!s:>10s} {str(rec) if rec else '未回復':>10s} {etf_same:>8.2f}%"
        )
        print(f"{'':12s} 周轉率 {m1['turnover']:.1f}×／年")
        rows.append(
            {
                "cell": cell,
                "cost_drag_pp": drag,
                "beta": beta,
                "corr": corr,
                "alpha_pct": alpha,
                "mdd": worst,
                "peak": str(p),
                "trough": str(t),
                "recovered": str(rec) if rec else None,
                "etf_same_period_pct": etf_same,
                "turnover": m1["turnover"],
                "cagr_0x": m0["cagr"],
                "cagr_1x": m1["cagr"],
            }
        )
    out = R / "reports/lw_diagnosis.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n→ {out}")


if __name__ == "__main__":
    main()
