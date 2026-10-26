#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plot_short_stock.py — 測試 A 圖：各規則驗證期平均淨報酬與 CI，加 A1 分年度。用法：python plot_short_stock.py --dir ../short_stock"""
import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import plot_jiangbo as P  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    p.add_argument("--title", default="個股漲停隔天放空：")
    a = p.parse_args()
    base = Path(a.dir)
    res = json.loads((base / "result.json").read_text(encoding="utf-8"))
    P.setup_font()
    fig = plt.figure(figsize=(13, 6.6), dpi=110)
    g = fig.add_gridspec(1, 2, width_ratios=[1.25, 1], wspace=0.3, top=0.8, bottom=0.12, left=0.25, right=0.97)
    ax = fig.add_subplot(g[0, 0])
    # 判定用的那一段（補充：A1～A4 看上櫃、A5 看上市）；舊結果沒有 judged_on 就用合併
    seg = {"validate_tpex": "上櫃", "validate_twse": "上市", "validate": "合併"}
    # 微軟正黑體沒有「≥」，換成 Big5 也有的「≧」
    rows = [(f"{name.replace('≥', '≧')}（{seg[r.get('judged_on', 'validate')]}）", r) for name, r in res["rules"].items()
            if r[r.get("judged_on", "validate")]["n"]]
    for k, (name, r) in enumerate(rows):
        v = r[r.get("judged_on", "validate")]
        ax.hlines(k, v["ci_low"], v["ci_high"], color=P.INK_2, linewidth=1.3)
        ax.plot([v["mean_net_pct"]], [k], marker="o", markersize=8, color=P.GAIN if v["mean_net_pct"] > 0 else P.LOSS,
                markeredgecolor=P.SURFACE, zorder=3)
        ax.plot([v["mean_no_penalty_pct"]], [k], marker="|", markersize=12, color=P.INK, zorder=3)
        ax.annotate(f"{v['mean_net_pct']:+.2f}%（{v['n']} 筆）", (v["ci_high"], k), xytext=(5, 0), textcoords="offset points",
                    va="center", fontsize=8.5, color=P.INK_2)
    ax.axvline(0, color=P.MUTED, linewidth=0.9)
    ax.set_yticks(range(len(rows)), [n for n, _ in rows], fontsize=9)
    ax.invert_yaxis()
    ax.set_title("驗證期每筆平均淨報酬（圓點）與 95% CI；直槓＝不扣 7% 鎖漲停懲罰", loc="left", fontsize=10, color=P.INK)
    ax2 = fig.add_subplot(g[0, 1])
    a1 = res["rules"]["A1 漲停隔日（主要）"]["yearly"]
    years = list(a1)
    vals = [a1[y][1] for y in years]
    ax2.bar(range(len(years)), vals, color=[P.GAIN if v > 0 else P.LOSS for v in vals], width=0.6)
    for i, y in enumerate(years):
        ax2.text(i, vals[i], f"{vals[i]:+.2f}%\n{a1[y][0]} 筆", ha="center", va="bottom" if vals[i] > 0 else "top", fontsize=8,
                 color=P.INK_2)
    ax2.set_xticks(range(len(years)), years)
    ax2.axhline(0, color=P.MUTED, linewidth=0.9)
    ax2.set_title("A1 驗證期分年度（上市＋上櫃合併，每筆平均淨%）", loc="left", fontsize=10, color=P.INK)
    for x in (ax, ax2):
        x.tick_params(labelsize=8.5, length=0)
        x.grid(color=P.GRID, linewidth=0.6)
        for side in ("top", "right"):
            x.spines[side].set_visible(False)
        x.spines["left"].set_color(P.GRID)
        x.spines["bottom"].set_color(P.GRID)
    a1r = res["rules"]["A1 漲停隔日（主要）"]
    judged = a1r.get("judged_on", "validate")
    v = a1r[judged]
    verdict = "及格" if res["rules"]["A1 漲停隔日（主要）"]["passed"] else "不及格"
    fig.text(0.02, 0.965, f"{a.title}主要規則 A1 {verdict}", fontsize=15, fontweight="bold", color=P.INK, va="top")
    fig.text(0.02, 0.915, f"A1 驗證期 2022–2026（{seg[judged]}）：{v['n']} 筆、勝率 {v['win_rate']}%、平均 {v['mean_net_pct']:+.2f}%，"
             f"CI {v['ci_low']:+.2f}～{v['ci_high']:+.2f}（以交易日群集重抽）｜每筆成本 {res['cost_pct']}%，收盤鎖漲停另扣 7%",
             fontsize=10, color=P.INK_2, va="top")
    fig.savefig(base / "short_stock.png")
    print(base / "short_stock.png")


if __name__ == "__main__":
    main()
