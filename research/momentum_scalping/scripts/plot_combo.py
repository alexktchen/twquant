#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plot_combo.py — 組合「ORB 只做多 + 噪音區只放空」：挑選期 vs 樣本外。用法：python plot_combo.py --dir ../combo --bidir ../intraday_bidir"""
import argparse
import csv
import json
import random
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import plot_jiangbo as P  # noqa: E402


def ci95(xs, iterations=2000, seed=7):
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(xs, k=len(xs))) for _ in range(iterations))
    return means[int(0.025 * iterations)], means[int(0.975 * iterations) - 1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    p.add_argument("--bidir", required=True)
    a = p.parse_args()
    base = Path(a.dir)
    res = json.loads((base / "result.json").read_text(encoding="utf-8"))
    bid = json.loads((Path(a.bidir) / "result.json").read_text(encoding="utf-8"))
    fut = list(csv.DictReader((Path(a.bidir) / "trades.csv").open(encoding="utf-8-sig")))
    days = bid["rules"]["ORB5"]["days"]
    combo = defaultdict(float)
    for t in fut:
        if (t["rule"] == "ORB5" and t["side"] == "1") or (t["rule"] == "NOISE" and t["side"] == "-1"):
            combo[t["day"]] += float(t["net_pnl"])
    combo_daily = [combo.get(d, 0.0) for d in days]
    lo, hi = ci95(combo_daily)
    ins = {"ORB 只做多": bid["rules"]["ORB5"]["long"], "噪音區只放空": bid["rules"]["NOISE"]["short"],
           "組合": {"daily_mean": statistics.fmean(combo_daily), "daily_ci": [lo, hi], "days": len(days)}}
    oos = {"ORB 只做多": res["oos"]["ORB_LONG_5m"], "噪音區只放空": res["oos"]["NOISE_SHORT_30m"],
           "組合": res["oos"]["COMBO_5m+30m"]}
    P.setup_font()
    fig = plt.figure(figsize=(13, 6.4), dpi=110)
    g = fig.add_gridspec(1, 2, width_ratios=[1, 1.25], wspace=0.28, top=0.8, bottom=0.12, left=0.13, right=0.97)
    ax = fig.add_subplot(g[0, 0])
    rows = []
    for name in ins:
        rows.append((f"{name}\n挑選期・小台逐筆（{ins[name]['days']} 天）", ins[name], P.INK))
        rows.append((f"{name}\n樣本外・現貨代理（{oos[name]['days']} 天）", oos[name], P.VWAP))
    for k, (label, s, color) in enumerate(rows):
        ax.hlines(k, s["daily_ci"][0], s["daily_ci"][1], color=P.INK_2, linewidth=1.3)
        ax.plot([s["daily_mean"]], [k], marker="o", markersize=8, color=color, markeredgecolor=P.SURFACE, zorder=3)
        ax.annotate(f"{s['daily_mean']:+,.0f}", (s["daily_ci"][1], k), xytext=(5, 0), textcoords="offset points",
                    va="center", fontsize=8.5, color=P.INK_2)
    ax.axvline(0, color=P.MUTED, linewidth=0.9)
    ax.set_yticks(range(len(rows)), [r[0] for r in rows], fontsize=8.5)
    ax.invert_yaxis()
    ax.set_title("每日平均淨損益（元）與 95% CI：黑＝挑選期、橘＝樣本外", loc="left", fontsize=10.5, color=P.INK)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))

    ax2 = fig.add_subplot(g[0, 1])
    s = res["oos"]["NOISE_SHORT_60m"]
    ys = [0] + s["cumulative"]
    ax2.plot(range(len(ys)), ys, color=P.DOWN_TINT, linewidth=1.8)
    ax2.axhline(0, color=P.MUTED, linewidth=0.8)
    years = {}
    for i, d in enumerate(s["day_list"], 1):
        years.setdefault(d[:4], i)
    for y, i in years.items():
        ax2.axvline(i, color=P.GRID, linewidth=0.9)
        ax2.text(i + 3, max(ys) * 0.98, y, fontsize=8.5, color=P.INK_2, va="top")
    ax2.annotate(f"{ys[-1]:+,.0f} 元", (len(ys) - 1, ys[-1]), xytext=(-6, -16), textcoords="offset points", ha="right",
                 fontsize=10, color=P.INK, fontweight="bold")
    ax2.set_title(f"噪音區只放空・60 分 K 每小時檢查：樣本外 {s['days']} 天、{s['n']} 筆、勝率 {s['win_rate']}%",
                  loc="left", fontsize=10.5, color=P.INK)
    ax2.set_xlabel("交易日（2024-11 → 2026-10，不含挑選期 29 天）", fontsize=8.5)
    ax2.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v / 1000:,.0f}k"))
    for x in (ax, ax2):
        x.tick_params(labelsize=8.5, length=0)
        x.grid(color=P.GRID, linewidth=0.6)
        for side in ("top", "right"):
            x.spines[side].set_visible(False)
        x.spines["left"].set_color(P.GRID)
        x.spines["bottom"].set_color(P.GRID)
    fig.text(0.02, 0.965, "「各取其優」組合：換到沒挑過的日子就不成立", fontsize=15, fontweight="bold", color=P.INK, va="top")
    fig.text(0.02, 0.915, f"ORB 只做多 + 噪音區只放空｜樣本外組合 {oos['組合']['days']} 天合計 {oos['組合']['total_net']:+,.0f} 元"
             f"｜唯一長樣本：噪音區只放空 2 年每日 +{s['daily_mean']:,.0f} 元，CI {s['daily_ci'][0]:+,.0f}～{s['daily_ci'][1]:+,.0f}（不顯著）",
             fontsize=10, color=P.INK_2, va="top")
    fig.text(0.02, 0.875, "現貨代理：Yahoo 加權指數 K 線、無成交量（VWAP 以均價代替）、09:00 開盤；成本照小台 1 口", fontsize=8.5,
             color=P.MUTED, va="top")
    fig.savefig(base / "combo.png")
    print(base / "combo.png")


if __name__ == "__main__":
    main()
