#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plot_chips.py — 測試 B 圖：各濾網驗證期「訊號端／另一端／每天放空」的隔天放空淨報酬。用法：python plot_chips.py --dir ../short_futures"""
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
    a = p.parse_args()
    base = Path(a.dir)
    res = json.loads((base / "result.json").read_text(encoding="utf-8"))
    P.setup_font()
    fig = plt.figure(figsize=(12.5, 6.6), dpi=110)
    ax = fig.add_axes([0.27, 0.1, 0.62, 0.68])
    labels, k = [], 0
    groups = (("validate", "訊號端", P.DOWN_TINT), ("validate_other_tail", "另一端", P.UP_TINT),
              ("validate_every_day", "同期每天都空", P.MUTED))
    for name, r in res["rules"].items():
        for key, label, color in groups:
            s = r[key]
            ax.hlines(k, s["ci_low"], s["ci_high"], color=P.INK_2, linewidth=1.2)
            ax.plot([s["mean_net_bp"]], [k], marker="o", markersize=8 if key == "validate" else 6, color=color,
                    markeredgecolor=P.SURFACE, zorder=3)
            ax.annotate(f"{s['mean_net_bp']:+.1f} bp（{s['n']} 天）", (s["ci_high"], k), xytext=(5, 0),
                        textcoords="offset points", va="center", fontsize=8.3, color=P.INK_2)
            labels.append(name if key == "validate" else label)
            k += 1
        k += 0.6
    ys = []
    k = 0
    for _ in res["rules"]:
        ys += [k, k + 1, k + 2]
        k += 3.6
    ax.set_yticks(ys, labels, fontsize=8.5)
    ax.invert_yaxis()
    ax.axvline(0, color=P.MUTED, linewidth=0.9)
    ax.set_xlabel("隔天開盤放空、收盤回補的平均淨報酬（bp，已扣小台成本）與 95% CI", fontsize=9)
    ax.tick_params(labelsize=8.5, length=0)
    ax.grid(axis="x", color=P.GRID, linewidth=0.6)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    l1 = res["rules"]["L1 大額特定法人淨部位最偏空"]["validate"]
    fig.text(0.02, 0.965, "期貨籌碼濾網：法人偏空的隔天確實比較弱，但 4 個都沒過及格線", fontsize=15, fontweight="bold",
             color=P.INK, va="top")
    fig.text(0.02, 0.915, f"最接近的是 L1：驗證期 2016–2026 共 {l1['n']} 天、勝率 {l1['win_rate']}%、平均 {l1['mean_net_bp']:+.1f} bp，"
             f"CI {l1['ci_low']:+.1f}～{l1['ci_high']:+.1f}（下界差一點點）｜F1、F3 只有近 3 年資料", fontsize=10, color=P.INK_2, va="top")
    fig.text(0.02, 0.875, "綠＝訊號端、紅＝另一端、灰＝同期每天放空（對照）", fontsize=9, color=P.MUTED, va="top")
    fig.savefig(base / "chips.png")
    print(base / "chips.png")


if __name__ == "__main__":
    main()
