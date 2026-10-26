#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 futures_signal_analysis.py 的結果畫成一張圖：累計淨點數 + 各特徵驗證期表現。"""
import argparse, csv, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import plot_jiangbo as P
import matplotlib.pyplot as plt

def main():
    p = argparse.ArgumentParser(); p.add_argument("--dir", required=True); a = p.parse_args()
    d = Path(a.dir); P.setup_font()
    s = json.loads((d / "analysis.json").read_text(encoding="utf-8"))
    rows = sorted(csv.DictReader(open(d / "events.csv", encoding="utf-8-sig")), key=lambda r: r["time"])
    fig = plt.figure(figsize=(12, 6.4), dpi=110)
    g = fig.add_gridspec(1, 2, width_ratios=[1.25, 1], wspace=0.42, top=0.80, bottom=0.10, left=0.07, right=0.97)
    ax = fig.add_subplot(g[0, 0])
    cum, ys = 0.0, [0.0]
    for r in rows:
        cum += float(r["label_net_points"]); ys.append(cum)
    ax.plot(range(len(ys)), ys, color=P.PRICE, linewidth=1.8)
    ax.axhline(0, color=P.MUTED, linewidth=0.9)
    split = next(i for i, r in enumerate(rows) if r["day"] >= s["split"])
    ax.axvline(split, color=P.INK_2, linewidth=1, linestyle=(0, (4, 3)))
    ax.text(split + 3, max(ys) - (max(ys) - min(ys)) * 0.02, "驗證期 →", fontsize=9, color=P.INK_2, va="top")
    ax.text(3, max(ys) - (max(ys) - min(ys)) * 0.02, "探索期", fontsize=9, color=P.INK_2, va="top")
    ax.annotate(f"{ys[-1]:+.0f} 點", (len(ys) - 1, ys[-1]), xytext=(-10, -22), textcoords="offset points",
                ha="right", fontsize=10, fontweight="bold", color=P.INK)
    ax.set_title("每個訊號各做 1 口：累計淨點數（已扣成本）", loc="left", fontsize=11, color=P.INK)
    ax.set_xlabel("訊號（依時間）")
    ax2 = fig.add_subplot(g[0, 1])
    feats = s["features"]
    for i, f in enumerate(feats):
        t = f["test_chosen"]
        if t["mean_r"] is None: continue
        lo, hi = t["ci_low"], t["ci_high"]
        if lo is not None: ax2.hlines(i, lo, hi, color=P.INK_2, linewidth=1.4)
        ax2.plot([t["mean_r"]], [i], marker="o", markersize=6, color=P.PRICE, markeredgecolor=P.SURFACE, zorder=3)
    ax2.axvline(0, color=P.MUTED, linewidth=0.9)
    ax2.axvline(s["criteria"]["min_mean_r"], color=P.GAIN, linewidth=1.1, linestyle=(0, (4, 3)))
    ax2.text(s["criteria"]["min_mean_r"], len(feats) - 0.2, " 及格線 0.2R", fontsize=8.5, color=P.INK_2, va="bottom")
    ax2.set_yticks(range(len(feats)), [f"{f['name']}（{f['chosen']}）" for f in feats], fontsize=8.3)
    ax2.invert_yaxis(); ax2.set_xlim(-0.12, 0.26)
    ax2.set_title("各特徵：驗證期平均 R 與 95% 信賴區間", loc="left", fontsize=11, color=P.INK)
    for a_ in (ax, ax2):
        a_.tick_params(labelsize=8.5, length=0)
        for side in ("top", "right"): a_.spines[side].set_visible(False)
        a_.spines["left"].set_color(P.GRID); a_.spines["bottom"].set_color(P.GRID)
        a_.grid(axis="x" if a_ is ax2 else "y", color=P.GRID, linewidth=0.6)
    b = s["baseline"]["all"]
    fig.text(0.07, 0.95, "小台指期：動能訊號能不能被挑出來？", fontsize=16, fontweight="bold", color=P.INK, va="top")
    fig.text(0.07, 0.885, f"28 個交易日、{b['n']} 個訊號｜勝率 {b['win_rate']}%｜平均 {b['mean_r']:+.3f} R"
             f"（95% CI {b['ci_low']:+.3f}～{b['ci_high']:+.3f}）｜18 個特徵 0 個及格 → 不做 ML",
             fontsize=10, color=P.INK_2, va="top")
    path = d / "signal_analysis.png"; fig.savefig(path); plt.close(fig); print(path)

if __name__ == "__main__":
    main()
