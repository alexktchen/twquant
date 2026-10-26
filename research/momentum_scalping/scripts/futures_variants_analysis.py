#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
futures_variants_analysis.py — 照 futures_variants/PREREGISTRATION.md：9 組設定，探索期選 1 組，驗證期檢驗一次。

用法：python futures_variants_analysis.py --dir futures_variants --split 2026-10-02
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SIDES = {"long": "只做多", "short": "只放空", "both": "多空都做"}
EXITS = {"system": "原出場", "structure": "只留結構停損", "stop_close": "初始停損＋13:00"}
POINT_VALUE = 50


def ci95(xs, iterations=2000, seed=7):
    if len(xs) < 2:
        return None, None
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(xs, k=len(xs))) for _ in range(iterations))
    return means[int(0.025 * iterations)], means[int(0.975 * iterations) - 1]


def stats(rows):
    if not rows:
        return {"n": 0}
    r = [x["r"] for x in rows]
    lo, hi = ci95(r)
    return {"n": len(r), "win_rate": round(sum(v > 0 for v in r) / len(r) * 100, 1),
            "mean_r": round(statistics.fmean(r), 4), "ci_low": None if lo is None else round(lo, 4),
            "ci_high": None if hi is None else round(hi, 4),
            "mean_points": round(statistics.fmean(x["points"] for x in rows), 2),
            "total_pnl": round(sum(x["pnl"] for x in rows)),
            "hold_minutes": round(statistics.fmean(x["hold"] for x in rows), 1)}


def load(folder: Path):
    rows = []
    for e in csv.DictReader(open(folder / "events.csv", encoding="utf-8-sig")):
        try:
            rows.append({**e, "r": float(e["label_net_r"]), "points": float(e["label_net_points"]),
                         "pnl": float(e["label_net_pnl"]), "hold": float(e["label_hold_minutes"])})
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    p.add_argument("--split", required=True)
    args = p.parse_args()
    base = Path(args.dir)
    data = {(side, ex): load(base / f"{side}_{ex}") for side in ("long", "short") for ex in EXITS}
    for ex in EXITS:
        data[("both", ex)] = sorted(data[("long", ex)] + data[("short", ex)], key=lambda x: x["time"])
    configs = []
    for (side, ex), rows in data.items():
        train = [x for x in rows if x["day"] < args.split]
        test = [x for x in rows if x["day"] >= args.split]
        reasons = {}
        for x in rows:
            reasons[x["label_exit_reason"]] = reasons.get(x["label_exit_reason"], 0) + 1
        configs.append({"side": side, "exit": ex, "label": f"{SIDES[side]}｜{EXITS[ex]}",
                        "train": stats(train), "test": stats(test), "all": stats(rows),
                        "exit_reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1]))})
    chosen = max(configs, key=lambda c: c["train"]["mean_r"])
    t = chosen["test"]
    passed = t["n"] >= 40 and t["mean_r"] > 0 and t["ci_low"] is not None and t["ci_low"] > 0
    summary = {"split": args.split, "configs": configs, "chosen": chosen["label"], "passed": passed}
    (base / "analysis.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# 小台指期期貨化修改：結果", "", "照 PREREGISTRATION.md 一次跑完。R＝扣成本淨損益 ÷ 進場停損風險；每筆 1 口小台。", "",
             "| 設定 | 探索期 n / 平均R | 驗證期 n / 勝率 / 平均R | 驗證期 95% CI | 驗證期每筆淨點數 | 28 天合計淨損益 |",
             "|---|---|---|---|---|---|"]
    for c in sorted(configs, key=lambda c: -c["train"]["mean_r"]):
        tr, te, al = c["train"], c["test"], c["all"]
        mark = "　← 探索期最佳" if c is chosen else ""
        lines.append(f"| {c['label']}{mark} | {tr['n']} / {tr['mean_r']:+.3f} | {te['n']} / {te['win_rate']}% / {te['mean_r']:+.3f} | "
                     f"{te['ci_low']:+.3f} ～ {te['ci_high']:+.3f} | {te['mean_points']:+.1f} | {al['total_pnl']:+,.0f} 元 |")
    lines += ["", f"**探索期選出：{chosen['label']}。驗證期 {'通過' if passed else '未通過'}"
                  f"（n={t['n']}，平均 {t['mean_r']:+.3f} R，CI {t['ci_low']:+.3f}～{t['ci_high']:+.3f}）。**", ""]
    (base / "RESULT.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    plot(base, data, configs, chosen, args.split)
    return 0


def plot(base, data, configs, chosen, split):
    import plot_jiangbo as P
    import matplotlib.pyplot as plt
    P.setup_font()
    colors = {"system": P.PRICE, "structure": P.VWAP, "stop_close": "#1baf7a"}
    fig = plt.figure(figsize=(13, 8.6), dpi=110)
    g = fig.add_gridspec(1, 3, wspace=0.28, top=0.86, bottom=0.55, left=0.06, right=0.97)
    g2 = fig.add_gridspec(1, 1, top=0.44, bottom=0.07, left=0.21, right=0.97)
    for i, side in enumerate(("long", "short", "both")):
        ax = fig.add_subplot(g[0, i])
        for ex in EXITS:
            rows = data[(side, ex)]
            cum, ys = 0.0, [0.0]
            for x in rows:
                cum += x["points"]
                ys.append(cum)
            ax.plot(range(len(ys)), ys, color=colors[ex], linewidth=1.6, label=EXITS[ex])
            offset = {"system": 0, "structure": -9, "stop_close": 9}[ex]
            ax.annotate(f"{ys[-1]:+.0f}", (len(ys) - 1, ys[-1]), xytext=(3, offset), textcoords="offset points",
                        fontsize=8.5, color=P.INK, va="center")
            split_i = next((k for k, x in enumerate(rows) if x["day"] >= split), None)
        if split_i is not None:
            ax.axvline(split_i, color=P.INK_2, linewidth=0.9, linestyle=(0, (4, 3)))
        ax.axhline(0, color=P.MUTED, linewidth=0.8)
        ax.set_title(f"{SIDES[side]}：累計淨點數（每訊號 1 口）", loc="left", fontsize=10.5, color=P.INK)
        ax.set_xlabel("訊號（依時間，虛線右邊是驗證期）", fontsize=8.5)
        if i == 0:
            ax.legend(frameon=False, fontsize=8.5, loc="upper left")
    ax = fig.add_subplot(g2[0, 0])
    order = sorted(configs, key=lambda c: (list(SIDES).index(c["side"]), list(EXITS).index(c["exit"])))
    for k, c in enumerate(order):
        te = c["test"]
        ax.hlines(k, te["ci_low"], te["ci_high"], color=P.INK_2, linewidth=1.4)
        ax.plot([te["mean_r"]], [k], marker="o", markersize=7, color=colors[c["exit"]], markeredgecolor=P.SURFACE, zorder=3)
        tr = c["train"]
        ax.plot([tr["mean_r"]], [k], marker="|", markersize=12, color=P.INK, zorder=3)
    ax.axvline(0, color=P.MUTED, linewidth=0.9)
    ax.set_yticks(range(len(order)), [c["label"] + ("　★探索期最佳" if c is chosen else "") for c in order], fontsize=9)
    ax.invert_yaxis()
    ax.set_title("驗證期平均 R（圓點）與 95% 信賴區間；直槓＝探索期平均 R", loc="left", fontsize=10.5, color=P.INK)
    for a in fig.axes:
        a.tick_params(labelsize=8.5, length=0)
        a.grid(color=P.GRID, linewidth=0.6)
        for side_ in ("top", "right"):
            a.spines[side_].set_visible(False)
        a.spines["left"].set_color(P.GRID)
        a.spines["bottom"].set_color(P.GRID)
    te = chosen["test"]
    fig.text(0.06, 0.965, "小台指期：把 Momentum_Scalping 改成期貨版（加放空、放寬出場）", fontsize=15, fontweight="bold",
             color=P.INK, va="top")
    fig.text(0.06, 0.925, f"28 天、9 組設定｜探索期最佳：{chosen['label']}｜驗證期 {te['n']} 筆、勝率 {te['win_rate']}%、"
             f"平均 {te['mean_r']:+.3f} R（CI {te['ci_low']:+.3f}～{te['ci_high']:+.3f}）", fontsize=10, color=P.INK_2, va="top")
    fig.savefig(base / "futures_variants.png")
    print(base / "futures_variants.png")


if __name__ == "__main__":
    raise SystemExit(main())
