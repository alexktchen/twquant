#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""box_break_test.py：台指期箱型突破（studies/box_break/PREREGISTRATION.md）。只跑一次，不調參數。

用法：python box_break_test.py --fubon <fubon_1m> --out <studies/box_break>
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import minute_bidir_test as M  # noqa: E402

START, END = "2017-05-22", "2026-09-08"
BOX_BARS, STEP, RETEST_BARS = 12, 5, 6
SEED, ITER, MIN_T, TOP_SHARE = 7, 2000, 3.0, 0.5


def five_min(d):
    """[(收完的時刻, 高, 低, 收)]：08:45 起每 5 分鐘一根，只收完整的那幾根。"""
    buckets = defaultdict(list)
    for i, minute in enumerate(d.m):
        buckets[(minute - M.OPEN_MIN) // STEP].append(i)
    bars = []
    for k in sorted(buckets):
        idx = buckets[k]
        end = M.OPEN_MIN + (k + 1) * STEP
        if end > d.eod:
            break
        bars.append((end, max(d.h[i] for i in idx), min(d.l[i] for i in idx), d.c[idx[-1]]))
    return bars


def box_at(bars, i):
    past = bars[i - BOX_BARS : i]
    return max(b[1] for b in past), min(b[2] for b in past)


def rule_a(d):
    """突破就做、反向突破反手。"""
    bars, decisions = five_min(d), []
    for i in range(BOX_BARS, len(bars)):
        top, bottom = box_at(bars, i)
        close = bars[i][3]
        if close > top:
            decisions.append((bars[i][0], 1))
        elif close < bottom:
            decisions.append((bars[i][0], -1))
    return M.run_targets(d, "BOX_A", [x for x in decisions if x[0] < d.eod])


def rule_b(d):
    """突破後等回測才進場；跌回箱子中線出場；不反手。"""
    bars, decisions = five_min(d), []
    pos, setup, mid = 0, None, None
    for i in range(BOX_BARS, len(bars)):
        end, high, low, close = bars[i]
        if end >= d.eod:
            break
        if pos:
            if (pos > 0 and close < mid) or (pos < 0 and close > mid):
                decisions.append((end, 0))
                pos, setup = 0, None
            continue
        if setup is not None:
            side, level, m0, born = setup
            if i - born > RETEST_BARS:
                setup = None
            elif side > 0 and low <= level and close > level or side < 0 and high >= level and close < level:
                decisions.append((end, side))
                pos, mid, setup = side, m0, None
                continue
        if setup is None:
            top, bottom = box_at(bars, i)
            if close > top:
                setup = (1, top, (top + bottom) / 2, i)
            elif close < bottom:
                setup = (-1, bottom, (top + bottom) / 2, i)
    return M.run_targets(d, "BOX_B", decisions)


def ci95(xs):
    rng = random.Random(SEED)
    m = sorted(statistics.fmean(rng.choices(xs, k=len(xs))) for _ in range(ITER))
    return m[int(0.025 * ITER)], m[int(0.975 * ITER) - 1]


def summarize(trades, names):
    daily = defaultdict(float)
    for t in trades:
        daily[t["day"]] += t["net_pnl"]
    xs = [daily.get(d, 0.0) for d in names]
    lo, hi = ci95(xs)
    mean, sd = statistics.fmean(xs), statistics.stdev(xs)
    total = sum(xs)
    years = defaultdict(float)
    for d, v in daily.items():
        years[d[:4]] += v
    top5 = sum(sorted(xs, reverse=True)[:5])
    no26 = sum(v for y, v in years.items() if y != "2026")
    n = len(trades)
    out = {"trades": n, "per_day": round(n / len(names), 2), "win_rate": round(sum(t["net_pnl"] > 0 for t in trades) / max(1, n) * 100, 1),
           "gross_points": round(statistics.fmean(t["gross_points"] for t in trades), 1) if n else 0, "net_points": round(statistics.fmean(t["net_pnl"] for t in trades) / 50, 1) if n else 0,
           "mean_day": round(mean, 1), "ci_low": round(lo, 1), "ci_high": round(hi, 1), "t": round(mean / (sd / math.sqrt(len(xs))), 2), "total": round(total, 0),
           "no_2026": round(no26, 0), "top5": round(top5, 0), "yearly": {y: round(v, 0) for y, v in sorted(years.items())}}
    out["checks"] = {"ci_low_above_0": lo > 0, "without_2026_above_0": no26 > 0, "top5_share_below_50pct": total > 0 and top5 / total < TOP_SHARE, "t_at_least_3": out["t"] >= MIN_T}
    out["passed"] = all(out["checks"].values())
    return out


def line(name, s):
    return (f"| {name} | {s['trades']:,} | {s['per_day']} | {s['win_rate']}% | {s['gross_points']:+.1f} | {s['net_points']:+.1f} | {s['mean_day']:+,.0f} | "
            f"{s['ci_low']:+,.0f} ～ {s['ci_high']:+,.0f} | {s['t']} | {s['total']:+,.0f} | {s['no_2026']:+,.0f} |")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--fubon", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    raw = M.load_bars(Path(a.fubon))
    kind = M.average_kind(raw, sorted(raw)[:30])
    days, _ = M.build_days(raw, kind)
    sample = [d for d in days if START <= d.day <= END]
    names = [d.day for d in sample]
    A, B = [], []
    for d in sample:
        A += rule_a(d)
        B += rule_b(d)
    r = {"A": summarize(A, names), "A_long": summarize([t for t in A if t["side"] > 0], names), "A_short": summarize([t for t in A if t["side"] < 0], names),
         "B": summarize(B, names), "B_long": summarize([t for t in B if t["side"] > 0], names), "B_short": summarize([t for t in B if t["side"] < 0], names)}
    dest = Path(a.out)
    (dest / "result.json").write_text(json.dumps({"days": len(names), "sample": [names[0], names[-1]], "vwap_kind": kind, **r}, ensure_ascii=False, indent=1), encoding="utf-8")
    labels = {"A": "**A 突破就做（主要）**", "A_long": "A 只看多單", "A_short": "A 只看空單", "B": "B 回測版", "B_long": "B 只看多單", "B_short": "B 只看空單"}
    lines = ["# 台指期箱型突破：結果", "", "照 PREREGISTRATION.md 一次跑完。小台 1 口，金額是新台幣，已扣成本。", "", f"樣本 {names[0]}～{names[-1]}，{len(names)} 個交易日。", "",
             f"**主要判定：{'及格' if r['A']['passed'] else '不及格'}。**", "", "| 條件 | A | B |", "|---|---|---|"]
    lines += [f"| {k} | {'✅' if r['A']['checks'][k] else '❌'} | {'✅' if r['B']['checks'][k] else '❌'} |" for k in r["A"]["checks"]]
    lines += ["", "| 規則 | 筆數 | 每天筆數 | 勝率 | 每筆毛點 | 每筆淨點 | 每日平均 | 95% CI | t | 合計 | 不含 2026 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    lines += [line(labels[k], r[k]) for k in labels]
    lines += ["", "## 分年度合計", "", "| 年 | A | B |", "|---|---|---|"]
    lines += [f"| {y} | {r['A']['yearly'].get(y, 0):+,.0f} | {r['B']['yearly'].get(y, 0):+,.0f} |" for y in sorted(set(r["A"]["yearly"]) | set(r["B"]["yearly"]))]
    (dest / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
