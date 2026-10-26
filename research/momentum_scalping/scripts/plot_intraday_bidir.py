#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plot_intraday_bidir.py — 把 intraday_bidir_test.py 的結果畫成江波圖（每條策略一張，29 天小圖）＋總覽

用法：python plot_intraday_bidir.py --dir intraday_bidir
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

import plot_jiangbo as P  # noqa: E402

WEEK = "一二三四五六日"
LONG_C, SHORT_C = P.UP_TINT, P.DOWN_TINT  # 台股慣例：做多紅、放空綠
BAND = "#7a5bc7"


def minute(text: str) -> float:
    dt = datetime.fromisoformat(text)
    return (dt.hour - 8) * 60 + dt.minute - 45 + dt.second / 60


def money(v: float) -> str:
    return f"{'+' if v > 0 else '-' if v < 0 else ''}{abs(v):,.0f}"


def style(ax):
    ax.tick_params(labelsize=7.5, length=0)
    ax.grid(axis="y", color=P.GRID, linewidth=0.5)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(P.GRID)


def panel(ax, day, info, trades, extra, rule):
    rows = info["rows"]
    xs = [r[0] for r in rows]
    ax.plot(xs, [r[2] for r in rows], color=P.VWAP, linewidth=0.9)
    ax.plot(xs, [r[1] for r in rows], color=P.PRICE, linewidth=1.2)
    if info["prev_close"]:
        ax.axhline(info["prev_close"], color=P.MUTED, linewidth=0.7, linestyle=(0, (4, 3)))
    lows, highs = [r[1] for r in rows], [r[1] for r in rows]
    if rule == "NOISE" and extra.get("bands"):
        bx = [b["minute"] for b in extra["bands"]]
        for key in ("ub", "lb"):
            ys = [b[key] for b in extra["bands"]]
            ax.step(bx + [300], ys + ys[-1:], where="post", color=BAND, linewidth=0.9, linestyle=(0, (2, 1.5)))
            (highs if key == "ub" else lows).extend(ys)
    for t in trades:
        x0, x1 = minute(t["entry_time"]), minute(t["exit_time"])
        side = int(t["side"])
        ax.axvspan(x0, x1, color=LONG_C if side > 0 else SHORT_C, alpha=0.16, linewidth=0)
        if rule != "VWAP":
            ax.plot([x0], [float(t["entry_price"])], marker="^" if side > 0 else "v", markersize=6, color=P.INK,
                    markeredgecolor=P.SURFACE, markeredgewidth=0.8, zorder=5)
            ax.plot([x1], [float(t["exit_price"])], marker="o", markersize=4.5, color=P.SURFACE,
                    markeredgecolor=P.INK, markeredgewidth=1.0, zorder=5)
        if t["stop_price"]:
            ax.hlines(float(t["stop_price"]), x0, x1, colors=P.INK_2, linewidth=0.8, linestyles=(0, (1, 1.2)))
            (lows if side > 0 else highs).append(float(t["stop_price"]))
    lo, hi = min(lows), max(highs)
    if info["prev_close"]:
        lo, hi = min(lo, info["prev_close"]), max(hi, info["prev_close"])
    pad = (hi - lo) * 0.08 or 10
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(0, 300)
    ax.set_xticks([15, 75, 135, 195, 255], ["9", "10", "11", "12", "13"])
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    style(ax)
    net = sum(float(t["net_pnl"]) for t in trades)
    d = date.fromisoformat(day)
    ax.set_title(f"{day[5:].replace('-', '/')}（{WEEK[d.weekday()]}）{len(trades)} 筆", loc="left", fontsize=8.5,
                 color=P.INK_2, pad=3)
    ax.text(1, 1.02, money(net) if trades else "沒交易", transform=ax.transAxes, ha="right", va="bottom", fontsize=9,
            fontweight="bold", color=(P.GAIN if net > 0 else P.LOSS if net < 0 else P.MUTED))
    return net


def legend(rule):
    items = [Line2D([], [], color=P.PRICE, linewidth=1.6, label="小台成交價（1 分）"),
             Line2D([], [], color=P.VWAP, linewidth=1.3, label="VWAP"),
             Line2D([], [], color=P.MUTED, linewidth=0.9, linestyle=(0, (4, 3)), label="昨收"),
             Patch(facecolor=LONG_C, alpha=0.3, label="持有多單"), Patch(facecolor=SHORT_C, alpha=0.3, label="持有空單")]
    if rule != "VWAP":
        items += [Line2D([], [], marker="^", color=P.INK, linestyle="none", markersize=7, label="進場做多"),
                  Line2D([], [], marker="v", color=P.INK, linestyle="none", markersize=7, label="進場放空"),
                  Line2D([], [], marker="o", color=P.SURFACE, markeredgecolor=P.INK, linestyle="none", markersize=6,
                         label="出場")]
    if rule == "ORB5":
        items.append(Line2D([], [], color=P.INK_2, linewidth=1.0, linestyle=(0, (1, 1.2)), label="停損"))
    if rule == "NOISE":
        items.append(Line2D([], [], color=BAND, linewidth=1.1, linestyle=(0, (2, 1.5)), label="噪音區上下緣"))
    return items


def grid(rule, name, days, chart, trades, extras, summary, desc, path):
    cols = 5
    nrows = (len(days) + cols - 1) // cols
    width, left, right, gap_x = 14.0, 0.55, 0.25, 0.42
    header, row_h, plot_h = 1.3, 1.95, 1.5
    col_w = (width - left - right - gap_x * (cols - 1)) / cols
    height = header + nrows * row_h + 0.15
    fig = plt.figure(figsize=(width, height), dpi=105)
    by_day = defaultdict(list)
    for t in trades:
        by_day[t["day"]].append(t)
    for k, day in enumerate(days):
        r, c = divmod(k, cols)
        x0 = left + c * (col_w + gap_x)
        y0 = height - header - (r + 1) * row_h + (row_h - plot_h) * 0.35
        ax = fig.add_axes([x0 / width, y0 / height, col_w / width, plot_h / height])
        panel(ax, day, chart[day], by_day[day], extras.get(day, {}), rule)
    s = summary["both"]
    fig.text(left / width, 1 - 0.25 / height, f"{name}：小台日內多空，有訊號就做那邊", fontsize=15,
             fontweight="bold", color=P.INK, va="top")
    fig.text(left / width, 1 - 0.68 / height,
             f"{desc}｜{s['days']} 天 {s['n']} 筆、勝率 {s['win_rate']}%、合計 {money(s['total_net'])} 元"
             f"（做多 {money(summary['long']['total_net'])}、放空 {money(summary['short']['total_net'])}）｜已扣滑價、手續費、期交稅",
             fontsize=9.5, color=P.INK_2, va="top")
    fig.legend(handles=legend(rule), loc="upper left", bbox_to_anchor=(left / width, 1 - 0.92 / height), ncol=10,
               fontsize=8.5, frameon=False, handlelength=2.0, columnspacing=1.3)
    fig.savefig(path)
    plt.close(fig)
    return path


DESC = {"ORB5": "第一根 5 分 K 收紅做多、收黑放空，停損在該 K 另一端，停利 10R，否則收盤平",
        "VWAP": "每分鐘：價格在 VWAP 之上持多、之下持空，方向一變就反手",
        "NOISE": "前 14 天同時點平均波動畫噪音區，每 30 分檢查：突破上緣做多、跌破下緣放空（前 14 天暖身）"}


def overview(result, path):
    fig = plt.figure(figsize=(13, 8.4), dpi=110)
    g = fig.add_gridspec(1, 3, wspace=0.3, top=0.84, bottom=0.53, left=0.07, right=0.95)
    g2 = fig.add_gridspec(1, 1, top=0.42, bottom=0.07, left=0.2, right=0.97)
    styles = {"both": (P.INK, "-", "多空都做"), "long": (LONG_C, (0, (4, 2)), "只做多"),
              "short": (SHORT_C, (0, (1, 1.2)), "只放空")}
    for i, (rule, r) in enumerate(result["rules"].items()):
        ax = fig.add_subplot(g[0, i])
        ends = []
        for key, (color, ls, label) in styles.items():
            ys = [0] + r[key]["cumulative"]
            ax.plot(range(len(ys)), ys, color=color, linestyle=ls, linewidth=1.8, label=label)
            ends.append((ys[-1], f"{label} {money(ys[-1])}", color))
        ends.sort(key=lambda e: -e[0])
        lo, hi = ax.get_ylim()
        gap, last = (hi - lo) * 0.09, None
        n_days = len(r["both"]["cumulative"])
        for v, text, color in ends:
            y = v if last is None else min(v, last - gap)
            ax.annotate(text, (n_days, v), xytext=(n_days + 0.6, y), textcoords="data", fontsize=7.8, color=color,
                        va="center")
            last = y
        ax.set_ylim(min(lo, last - gap * 0.6), hi)
        ax.axhline(0, color=P.MUTED, linewidth=0.8)
        ax.set_xlim(0, n_days * 1.5)
        ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
        ax.set_title(f"{r['name']}（{len(r['days'])} 天）", loc="left", fontsize=10.5, color=P.INK)
        ax.set_xlabel("交易日", fontsize=8.5)
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v / 1000:,.0f}k"))
        style(ax)
        ax.spines["left"].set_visible(True)
        ax.spines["left"].set_color(P.GRID)
    fig.text(0.07, 0.885, "累計淨損益（元，小台 1 口）", fontsize=9.5, color=P.INK_2)

    ax = fig.add_subplot(g2[0, 0])
    labels, k = [], 0
    for rule, r in result["rules"].items():
        for key in ("both", "long", "short"):
            s = r[key]
            color = styles[key][0]
            ax.hlines(k, s["daily_ci"][0], s["daily_ci"][1], color=P.INK_2, linewidth=1.3)
            ax.plot([s["daily_mean"]], [k], marker="o", markersize=7, color=color, markeredgecolor=P.SURFACE, zorder=3)
            ax.annotate(f"{money(s['daily_mean'])}", (s["daily_ci"][1], k), xytext=(5, 0), textcoords="offset points",
                        va="center", fontsize=8, color=P.INK_2)
            labels.append(f"{r['name']}｜{styles[key][2]}（{s['n']} 筆）")
            k += 1
    ax.axvline(0, color=P.MUTED, linewidth=0.9)
    ax.set_yticks(range(len(labels)), labels, fontsize=8.5)
    ax.invert_yaxis()
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_title("每日平均淨損益（圓點）與 95% 信賴區間（橫線，以天重抽）：橫線全在 0 右邊才算及格", loc="left",
                 fontsize=10.5, color=P.INK)
    style(ax)
    ax.grid(axis="x", color=P.GRID, linewidth=0.5)
    fig.text(0.07, 0.965, "小台指期日內多空雙向：三條文獻策略都沒有及格", fontsize=15, fontweight="bold", color=P.INK,
             va="top")
    fig.text(0.07, 0.925, f"{result['first']}～{result['last']}｜照論文預設參數、事前登記、只跑一次｜已扣滑價每邊 1 點、手續費、期交稅",
             fontsize=10, color=P.INK_2, va="top")
    fig.savefig(path)
    plt.close(fig)
    return path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    args = p.parse_args()
    base = Path(args.dir)
    P.setup_font()
    result = json.loads((base / "result.json").read_text(encoding="utf-8"))
    chart = json.loads((base / "chart_data.json").read_text(encoding="utf-8"))
    trades = list(csv.DictReader((base / "trades.csv").open(encoding="utf-8-sig")))
    for rule, r in result["rules"].items():
        path = grid(rule, r["name"], r["days"], chart["days"], [t for t in trades if t["rule"] == rule],
                    chart["extras"].get(rule, {}), r, DESC[rule], base / f"jiangbo_{rule}.png")
        print(path)
    print(overview(result, base / "overview.png"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
