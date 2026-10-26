#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""畫日內動能檢驗：首小時報酬 vs 最後半小時報酬散佈圖 + 交易規則累計淨報酬。"""
import csv, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import plot_jiangbo as P
import matplotlib.pyplot as plt

d = Path(sys.argv[1]); P.setup_font()
rows = list(csv.DictReader(open(d / "daily_official_close.csv")))
reg_rows = list(csv.DictReader(open(d / "daily.csv")))
res = json.loads((d / "result_official_close.json").read_text(encoding="utf-8"))
fig = plt.figure(figsize=(12, 5.6), dpi=110)
g = fig.add_gridspec(1, 2, width_ratios=[1, 1.15], wspace=0.28, top=0.78, bottom=0.12, left=0.07, right=0.97)
ax = fig.add_subplot(g[0, 0])
x = [float(r["r_first"]) * 100 for r in rows]; y = [float(r["r_last"]) * 100 for r in rows]
ax.scatter(x, y, s=10, color=P.PRICE, alpha=0.45, linewidths=0)
reg = res["full"]["regression"]
xs = [min(x), max(x)]
ax.plot(xs, [reg["a"] * 100 + reg["b"] * v for v in xs], color=P.VWAP, linewidth=2)
ax.axhline(0, color=P.MUTED, linewidth=0.8); ax.axvline(0, color=P.MUTED, linewidth=0.8)
ax.set_xlabel("昨收 → 10:00 報酬（%）"); ax.set_ylabel("13:00 → 13:30 報酬（%）")
ax.set_title(f"每天一點：斜率 {reg['b']:+.4f}（t = {reg['t']:+.2f}），R² {reg['r2'] * 100:.2f}%", loc="left", fontsize=11, color=P.INK)
ax2 = fig.add_subplot(g[0, 1])
for data, label, color, width in ((rows, "修正版（官方收盤）", P.PRICE, 2.0), (reg_rows, "登記版（60 分 K 收盤）", P.INK_2, 1.2)):
    cum, ys = 0.0, [0.0]
    for r in data:
        cum += float(r["net"]) * 1e4; ys.append(cum)
    ax2.plot(range(len(ys)), ys, color=color, linewidth=width, label=label)
    ax2.annotate(f"{ys[-1]:+.0f} bp", (len(ys) - 1, ys[-1]), xytext=(-4, 6 if color == P.PRICE else -14),
                 textcoords="offset points", ha="right", fontsize=9.5, color=P.INK, fontweight="bold")
ax2.axhline(0, color=P.MUTED, linewidth=0.8)
ax2.legend(frameon=False, fontsize=9, loc="lower left")
ax2.set_xlabel("交易日"); ax2.set_ylabel("累計淨報酬（bp）")
ax2.set_title("照論文規則每天 13:00 進場、13:30 出場（扣小台成本）", loc="left", fontsize=11, color=P.INK)
for a in (ax, ax2):
    a.tick_params(labelsize=8.5, length=0); a.grid(color=P.GRID, linewidth=0.6)
    for side in ("top", "right"): a.spines[side].set_visible(False)
    a.spines["left"].set_color(P.GRID); a.spines["bottom"].set_color(P.GRID)
f = res["full"]
fig.text(0.07, 0.95, "論文說的「日內動能」，台股最近兩年有嗎？", fontsize=16, fontweight="bold", color=P.INK, va="top")
fig.text(0.07, 0.885, f"加權指數 {res['first_day']}～{res['last_day']}，{f['days']} 天｜方向命中率 {f['hit_rate_pct']:.1f}%｜"
         f"每天毛報酬 {f['mean_gross_pct'] * 100:+.1f} bp、成本 {f['mean_cost_pct'] * 100:.1f} bp、淨 {f['mean_net_pct'] * 100:+.1f} bp → 沒有複製成功",
         fontsize=10, color=P.INK_2, va="top")
fig.savefig(d / "intraday_momentum.png"); print(d / "intraday_momentum.png")
