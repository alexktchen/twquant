#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plot_jiangbo.py — 把 ms_replay 的交易畫在日內走勢圖（江波圖）上

每個交易日一張圖，每檔有交易的股票一格：1 分 K 收盤線、VWAP、平盤線、
買進 ▲ / 賣出 ▼、策略停損線與持有期間；另輸出一張累計損益總覽。

用法：python plot_jiangbo.py --run real_base --public public --out charts
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

# 色票：dataviz 預設色盤（淺色），價格／VWAP 兩色已通過 validate_palette.js
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
PRICE = "#2a78d6"      # series-1
VWAP = "#eb6834"       # series-2
UP_TINT = "#e34948"    # 台股慣例：平盤以上紅
DOWN_TINT = "#008300"  # 平盤以下綠
GAIN = "#d03b3b"       # 賺（台股紅）
LOSS = "#0ca30c"       # 賠（台股綠）

SETUP = {
    "INITIAL_BREAKOUT": "動能・初次突破", "MOMENTUM_CONTINUATION": "動能・延續",
    "PERSISTENT_MOMENTUM_START": "動能・連續推升", "FIRST_PULLBACK_REBREAK": "動能・回踩再突破",
    "TREND_INITIAL_ENTRY": "趨勢・首次進場", "TREND_RESUME": "趨勢・回踩續攻",
}
EXIT = {
    "MOMENTUM_STRUCTURE_BREAK_FULL": "結構破壞", "MOMENTUM_DECAY_PARTIAL": "動能衰退",
    "EMA_TREND_BREAK": "均線死叉", "HIGHER_LOW_BREAK": "跌破前低", "MARKET_TREND_INVALIDATED": "大盤轉弱",
    "RS_FAILURE": "強度轉弱", "TIME_EXIT": "13:00 清倉", "CLOCK_TIME_EXIT": "13:00 清倉",
    "EMERGENCY_STOP": "緊急停損", "EOD_FORCED_CLOSE": "收盤強制",
    "MOMENTUM_CUSTOM_TAKE_PROFIT": "停利", "TREND_CUSTOM_TAKE_PROFIT": "停利",
}


CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def setup_font() -> None:
    for path in ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",):
        if Path(path).exists():
            font_manager.fontManager.addfont(path)
            plt.rcParams["font.family"] = font_manager.FontProperties(fname=path).get_name()
    plt.rcParams.update({"axes.unicode_minus": False, "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
                         "savefig.facecolor": SURFACE, "axes.edgecolor": GRID, "text.color": INK,
                         "axes.labelcolor": INK_2, "xtick.color": MUTED, "ytick.color": MUTED})


def minute_of(text: str) -> float:
    dt = datetime.fromisoformat(text)
    return (dt.hour - 9) * 60 + dt.minute + dt.second / 60


def money(value: float) -> str:
    return f"{'+' if value > 0 else '-' if value < 0 else ''}{abs(value):,.0f}"


def load(run: Path, public: Path):
    trades = list(csv.DictReader((run / "trades.csv").open(encoding="utf-8-sig")))
    wanted = {(t["day"], t["symbol"]) for t in trades}
    bars = defaultdict(list)
    with (public / "bars.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if (row["date"], row["symbol"]) in wanted:
                bars[(row["date"], row["symbol"])].append(row)
    ref = {}
    with (public / "ref.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            ref[(row["date"], row["symbol"])] = row
    return trades, bars, ref


def draw_panel(ax, day, symbol, rows, info, trades, date_label: str = ""):
    xs, closes, vwaps = [], [], []
    pv = vol = 0.0
    for row in rows:
        hh, mm = (int(x) for x in row["time"].split(":"))
        o, h, low, c = (float(row[k]) for k in ("open", "high", "low", "close"))
        lots = float(row["volume_lots"])
        pv += (o + h + low + c) / 4 * lots  # 與重播時每分鐘 4 筆等量成交的 VWAP 相同
        vol += lots
        xs.append((hh - 9) * 60 + mm + 1)   # 收盤價在該分鐘結束時才成立
        closes.append(c)
        # 期貨版的 bars.csv 附逐筆算的 VWAP；股票近似版沒有，用 4 筆等量的算法重建。
        vwaps.append(float(row["vwap"]) if row.get("vwap") else (pv / vol if vol else c))
    prev = float(info["prev_close"])
    ax.fill_between(xs, closes, prev, where=[c >= prev for c in closes], color=UP_TINT, alpha=0.09,
                    linewidth=0, interpolate=True)
    ax.fill_between(xs, closes, prev, where=[c < prev for c in closes], color=DOWN_TINT, alpha=0.09,
                    linewidth=0, interpolate=True)
    ax.axhline(prev, color=MUTED, linewidth=0.9, linestyle=(0, (4, 3)))
    ax.plot(xs, vwaps, color=VWAP, linewidth=1.3)
    ax.plot(xs, closes, color=PRICE, linewidth=1.5)

    total = 0.0
    captions = []
    for n, t in enumerate(trades, 1):
        entry_x = minute_of(t["entry_time"])
        legs = json.loads(t["exit_legs"])
        exit_x = minute_of(legs[-1]["time"])
        entry_price = float(t["entry_price"])
        ax.axvspan(entry_x, exit_x, color=INK, alpha=0.06, linewidth=0)
        ax.hlines(float(t["stop_price"]), entry_x, exit_x, colors=INK_2, linewidth=1.0, linestyles=(0, (1, 1.5)))
        ax.plot([entry_x], [entry_price], marker="^", markersize=9, color=INK, markeredgecolor=SURFACE,
                markeredgewidth=1.2, zorder=5)
        ax.annotate(CIRCLED[n - 1], (entry_x, entry_price), xytext=(0, 9), textcoords="offset points",
                    ha="center", fontsize=9, color=INK, fontweight="bold")
        for leg in legs:
            ax.plot([minute_of(leg["time"])], [float(leg["price"])], marker="v", markersize=9, color=INK,
                    markeredgecolor=SURFACE, markeredgewidth=1.2, zorder=5)
        net = float(t["net_pnl"])
        total += net
        exits = "、".join(f"{leg['time'][11:16]} 賣 {float(leg['price']):g}（{EXIT.get(leg['reason'], leg['reason'])}）"
                         for leg in legs)
        captions.append(f"{CIRCLED[n - 1]} {t['entry_time'][11:16]} 買 {entry_price:g} → {exits}"
                        f"　{money(net)} 元（{float(t['net_ret_pct']):+.2f}%）")

    lo = min(min(closes), prev, *(float(t["stop_price"]) for t in trades))
    hi = max(max(closes), prev)
    pad = (hi - lo) * 0.12 or prev * 0.01
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(0, 270)
    ax.set_xticks([0, 60, 120, 180, 240, 270], ["09:00", "10:00", "11:00", "12:00", "13:00", "13:30"])
    ax.tick_params(labelsize=8, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)

    last_change = round((closes[-1] / prev - 1) * 100, 2) + 0.0  # 避免顯示 -0.00%
    setups = "、".join(dict.fromkeys(SETUP.get(t["setup"], t["setup"]) for t in trades))
    head = {"xycoords": "axes fraction", "textcoords": "offset points", "va": "bottom"}
    ax.annotate(f"{symbol} {info.get('name', '')}　收 {last_change:+.2f}%", (0, 1), xytext=(0, 24), ha="left",
                fontsize=11, color=INK, fontweight="bold", **head)
    ax.annotate(f"{date_label}{setups}｜{len(trades)} 筆", (0, 1), xytext=(0, 8), ha="left", fontsize=8.5,
                color=INK_2, **head)
    tag, colour = ("賺", GAIN) if total > 0 else ("賠", LOSS)
    ax.annotate(f"{money(total)} 元", (1, 1), xytext=(0, 24), ha="right", fontsize=11, color=INK,
                fontweight="bold", **head)
    ax.annotate(f" {tag} ", (1, 1), xytext=(-1, 8), ha="right", fontsize=8, color="#ffffff",
                bbox={"boxstyle": "round,pad=0.25", "facecolor": colour, "edgecolor": "none"}, **head)
    for i, line in enumerate(captions):
        ax.annotate(line, (0, 0), xycoords="axes fraction", xytext=(0, -26 - 13 * i), textcoords="offset points",
                    ha="left", va="top", fontsize=8.3, color=INK_2)
    return total


def legend_handles():
    return [
        Line2D([], [], color=PRICE, linewidth=1.8, label="成交價（1 分 K 收盤）"),
        Line2D([], [], color=VWAP, linewidth=1.6, label="VWAP"),
        Line2D([], [], color=MUTED, linewidth=1.0, linestyle=(0, (4, 3)), label="平盤（昨日參考價）"),
        Line2D([], [], color=INK_2, linewidth=1.2, linestyle=(0, (1, 1.5)), label="策略停損"),
        Line2D([], [], marker="^", color=INK, linestyle="none", markersize=8, label="買進"),
        Line2D([], [], marker="v", color=INK, linestyle="none", markersize=8, label="賣出"),
        Patch(facecolor=INK, alpha=0.08, label="持有期間"),
    ]


STOCK_NOTE = "（已扣價差、手續費、當沖稅）｜1 分 K 近似重播，不是真實成交"


def plot_panels(title: str, panels, bars, ref, path: Path, note: str = STOCK_NOTE,
                period: str = "當日", date_labels: bool = False) -> Path:
    """panels = [(day, symbol, trades)]，每格一張江波圖；版面以英吋計，兩欄。"""
    trades = [t for _, _, ts in panels for t in ts]
    cols, width = 2, 12.0
    left, right, gap = 0.7, 0.3, 0.75
    col_w = (width - left - right - gap) / cols
    header_h, title_h, plot_h = 1.45, 0.62, 2.3
    rows = [panels[i:i + cols] for i in range(0, len(panels), cols)]
    row_heights = [title_h + plot_h + 0.42 + 0.19 * max(len(p[2]) for p in row) + 0.25 for row in rows]
    height = header_h + sum(row_heights) + 0.1
    fig = plt.figure(figsize=(width, height), dpi=110)
    total = 0.0
    top = height - header_h
    for row, row_h in zip(rows, row_heights):
        for c, (day, symbol, panel_trades) in enumerate(row):
            x0 = left + c * (col_w + gap)
            bottom = top - title_h - plot_h
            ax = fig.add_axes([x0 / width, bottom / height, col_w / width, plot_h / height])
            total += draw_panel(ax, day, symbol, bars[(day, symbol)], ref[(day, symbol)], panel_trades,
                                date_label=f"{day[5:].replace('-', '/')}｜" if date_labels else "")
        top -= row_h
    wins = sum(1 for t in trades if float(t["net_pnl"]) > 0)
    fig.text(left / width, 1 - 0.3 / height, title, fontsize=16, fontweight="bold", color=INK, va="top")
    fig.text(left / width, 1 - 0.75 / height,
             f"{len(trades)} 筆｜賺 {wins} 筆、賠 {len(trades) - wins} 筆｜{period}淨損益 {money(total)} 元{note}",
             fontsize=10, color=INK_2, va="top")
    fig.legend(handles=legend_handles(), loc="upper left", bbox_to_anchor=(left / width, 1 - 1.0 / height),
               ncol=7, fontsize=8.5, frameon=False, handlelength=2.2, columnspacing=1.4)
    fig.savefig(path)
    plt.close(fig)
    return path


def plot_day(day, trades, bars, ref, out: Path) -> Path:
    by_symbol = defaultdict(list)
    for t in trades:
        by_symbol[t["symbol"]].append(t)
    symbols = sorted(by_symbol, key=lambda s: min(t["entry_time"] for t in by_symbol[s]))
    panels = [(day, s, by_symbol[s]) for s in symbols]
    return plot_panels(f"{day}  動能當沖重播", panels, bars, ref, out / f"jiangbo_{day}.png")


def plot_summary(trades, runs, out: Path, *, metric: str = "ret", title: str = "4 天總覽",
                 note: str = "（起始 100 萬）　1 分 K 近似重播，樣本太小，只能看方向",
                 filename: str = "jiangbo_summary.png") -> Path:
    ordered = sorted(trades, key=lambda t: t["exit_time"])
    fig = plt.figure(figsize=(12, 5.2), dpi=110)
    grid = fig.add_gridspec(1, 2, width_ratios=[1.7, 1], wspace=0.25, top=0.78, bottom=0.12, left=0.08, right=0.97)
    ax = fig.add_subplot(grid[0, 0])
    cumulative, values = 0.0, [0.0]
    for t in ordered:
        cumulative += float(t["net_pnl"])
        values.append(cumulative)
    ax.step(range(len(values)), values, where="post", color=PRICE, linewidth=2)
    ax.axhline(0, color=MUTED, linewidth=0.9)
    boundaries = {}
    for i, t in enumerate(ordered, 1):
        boundaries.setdefault(t["day"], i)
    for day, i in boundaries.items():
        ax.axvline(i - 1, color=GRID, linewidth=0.8)
        ax.text(i - 1 + 0.3, max(values) + (max(values) - min(values)) * 0.04, day[5:].replace("-", "/"),
                fontsize=8.5, color=INK_2)
    ax.plot([len(values) - 1], [values[-1]], marker="o", markersize=7, color=PRICE, markeredgecolor=SURFACE)
    ax.annotate(f"{money(values[-1])} 元", (len(values) - 1, values[-1]), xytext=(-6, -14), textcoords="offset points",
                ha="right", fontsize=10, color=INK, fontweight="bold")
    ax.set_title("累計淨損益（依出場順序，每筆一階）", loc="left", fontsize=11, color=INK)
    ax.set_xlabel("交易筆數")
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))

    ax2 = fig.add_subplot(grid[0, 1])
    labels = [r["label"] for r in runs]
    key, unit, heading = (("avg_net_ret_pct", "%", "每筆平均淨報酬") if metric == "ret"
                          else ("avg_r", " R", "每筆平均 R 倍數（淨損益 ÷ 停損風險）"))
    nets = [float(r[key]) for r in runs]
    colours = [GAIN if v > 0 else LOSS for v in nets]
    ax2.barh(range(len(runs)), nets, color=colours, height=0.55)
    span = max(abs(v) for v in nets) or 1.0
    for i, r in enumerate(runs):
        inside = nets[i] - 0.03 * span if nets[i] < 0 else nets[i] + 0.03 * span
        ax2.text(inside, i, f"{nets[i]:+.2f}{unit}", va="center", ha="right" if nets[i] < 0 else "left",
                 fontsize=9, color=INK)
        ax2.text(0.03 * span if nets[i] <= 0 else -0.03 * span, i, f"{r['trades']} 筆・勝率 {r['win_rate_pct']:.0f}%",
                 va="center", ha="left" if nets[i] <= 0 else "right", fontsize=8.5, color=INK_2)
    ax2.set_yticks(range(len(runs)), labels, fontsize=9)
    ax2.invert_yaxis()
    ax2.axvline(0, color=MUTED, linewidth=0.9)
    lo, hi = min(min(nets), 0), max(max(nets), 0)
    pad = span * 0.9
    ax2.set_xlim(lo - (pad if lo < 0 else pad * 0.8), hi + (pad if hi > 0 else pad * 0.8))
    ax2.set_title(f"{heading}（{len(runs)} 種設定）", loc="left", fontsize=11, color=INK)
    for a in (ax, ax2):
        a.tick_params(labelsize=8.5, length=0)
        a.grid(axis="y" if a is ax else "x", color=GRID, linewidth=0.6)
        for side in ("top", "right"):
            a.spines[side].set_visible(False)
        a.spines["left"].set_color(GRID)
        a.spines["bottom"].set_color(GRID)
    base = runs[0]
    fig.text(0.08, 0.95, title, fontsize=16, fontweight="bold", color=INK, va="top")
    fig.text(0.08, 0.88, f"{base['trades']} 筆｜勝率 {base['win_rate_pct']:.1f}%｜盈虧比 {base['payoff_ratio']:.2f}"
             f"（不虧要 {base['breakeven_win_rate_pct']:.0f}% 勝率）｜淨損益 {money(base['total_net_pnl'])} 元"
             f"{note}", fontsize=10, color=INK_2, va="top")
    path = out / filename
    fig.savefig(path)
    plt.close(fig)
    return path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--run", required=True, help="ms_replay 主要輸出資料夾")
    p.add_argument("--compare", nargs="*", default=[], help="其他情境：標籤=資料夾")
    p.add_argument("--public", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--base-label", default="原版")
    p.add_argument("--combine", action="store_true", help="所有日期畫在同一張圖（單一商品，例如期貨）")
    p.add_argument("--title", default="動能當沖重播")
    p.add_argument("--note", default=STOCK_NOTE, help="標題下方說明")
    p.add_argument("--summary-note", default="（起始 100 萬）　1 分 K 近似重播，樣本太小，只能看方向")
    p.add_argument("--metric", choices=["ret", "r"], default="ret", help="總覽右圖：每筆淨報酬% 或平均 R")
    p.add_argument("--prefix", default="jiangbo", help="輸出檔名前綴")
    args = p.parse_args()
    setup_font()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    trades, bars, ref = load(Path(args.run), Path(args.public))
    if args.combine:
        groups = defaultdict(list)
        for t in trades:
            groups[(t["day"], t["symbol"])].append(t)
        panels = [(day, symbol, groups[(day, symbol)]) for day, symbol in sorted(groups)]
        print(plot_panels(args.title, panels, bars, ref, out / f"{args.prefix}_days.png", note=args.note,
                          period="合計", date_labels=True))
    else:
        for day in sorted({t["day"] for t in trades}):
            print(plot_day(day, [t for t in trades if t["day"] == day], bars, ref, out))
    runs = []
    for label, folder in [(args.base_label, args.run)] + [tuple(x.split("=", 1)) for x in args.compare]:
        overall = json.loads((Path(folder) / "summary.json").read_text(encoding="utf-8"))["overall"]
        runs.append({"label": label, **overall})
    print(plot_summary(trades, runs, out, metric=args.metric, title=f"{args.title}｜總覽",
                       note=args.summary_note, filename=f"{args.prefix}_summary.png"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
