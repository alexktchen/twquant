#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
intraday_momentum_test.py — 加權指數「日內動能」複製檢驗（照 intraday_momentum/PREREGISTRATION.md）

r_first = 10:00 價格 ÷ 前一日收盤 − 1（原文是首半小時，60 分 K 只能用首小時）
r_last  = 13:30 收盤 ÷ 13:00 價格 − 1
檢驗：OLS + HC1 單尾 p 值；交易規則 sign(r_first)·r_last − 小台成本；bootstrap 95% CI。

用法：python intraday_momentum_test.py --out intraday_momentum
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

TPE = timezone(timedelta(hours=8))
POINT_VALUE, COMMISSION, TAX, SLIP_POINTS = 50, 30.0, 0.00002, 1.0


def bars(interval: str, rng: str):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/%5ETWII?interval={interval}&range={rng}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = json.load(urllib.request.urlopen(req, timeout=30))["chart"]["result"][0]
    q = data["indicators"]["quote"][0]
    by_day = defaultdict(dict)
    for i, ts in enumerate(data["timestamp"]):
        if q["close"][i] is None:
            continue
        dt = datetime.fromtimestamp(ts, TPE)
        by_day[dt.date().isoformat()][dt.strftime("%H:%M")] = {k: q[k][i] for k in ("open", "high", "low", "close")}
    return dict(sorted(by_day.items()))


def ols_hc1(x, y):
    n = len(x)
    mx, my = statistics.fmean(x), statistics.fmean(y)
    sxx = sum((a - mx) ** 2 for a in x)
    b = sum((a - mx) * (c - my) for a, c in zip(x, y)) / sxx
    a = my - b * mx
    resid = [c - a - b * xi for xi, c in zip(x, y)]
    var_b = n / (n - 2) * sum(((xi - mx) ** 2) * e ** 2 for xi, e in zip(x, resid)) / sxx ** 2
    t = b / math.sqrt(var_b)
    p_one = 0.5 * math.erfc(t / math.sqrt(2))  # 單尾 H1: b > 0（常態近似，n 夠大）
    r2 = 1 - sum(e ** 2 for e in resid) / sum((c - my) ** 2 for c in y)
    return {"n": n, "a": a, "b": b, "t": t, "p_one_sided": p_one, "r2": r2}


def ci95(xs, iterations=2000, seed=7):
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(xs, k=len(xs))) for _ in range(iterations))
    return means[int(0.025 * iterations)], means[int(0.975 * iterations) - 1]


def build_rows(by_day, first_key, entry_key, need, official=None):
    """official：{day: 官方收盤}。給了就用官方收盤當昨收與 13:30 收盤（Yahoo 分時 K 最後一根不含收盤集合競價）。"""
    rows, prev_close = [], None
    for day, b in by_day.items():
        complete = all(k in b for k in need) and (official is None or day in official)
        if complete and prev_close:
            r_first = b[first_key]["close"] / prev_close - 1
            entry = b[entry_key]["close"]  # 該根 K 收盤＝13:00 的價格
            last_close = official[day] if official is not None else b["13:00"]["close"]
            r_last = last_close / entry - 1
            level = b["13:00"]["close"]
            cost = (2 * SLIP_POINTS / level) + 2 * TAX + 2 * COMMISSION / (level * POINT_VALUE)
            side = 1 if r_first > 0 else -1 if r_first < 0 else 0
            rows.append({"day": day, "r_first": r_first, "r_last": r_last, "side": side,
                         "net": side * r_last - (cost if side else 0), "gross": side * r_last, "cost": cost,
                         "level": level})
        last_key = max(b) if b else None
        if official is not None:
            prev_close = official.get(day, prev_close)
        else:
            prev_close = b[last_key]["close"] if last_key else prev_close
    return rows


def summarize(rows):
    reg = ols_hc1([r["r_first"] for r in rows], [r["r_last"] for r in rows])
    net = [r["net"] * 100 for r in rows]
    lo, hi = ci95(net)
    traded = [r for r in rows if r["side"]]
    return {"days": len(rows), "regression": reg, "mean_net_pct": statistics.fmean(net), "ci95_net_pct": [lo, hi],
            "mean_gross_pct": statistics.fmean(r["gross"] * 100 for r in rows),
            "hit_rate_pct": sum(1 for r in traded if r["gross"] > 0) / len(traded) * 100,
            "mean_cost_pct": statistics.fmean(r["cost"] * 100 for r in traded),
            "sum_net_pct": sum(net)}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--official-close", action="store_true",
                   help="修正版：昨收與 13:30 收盤改用官方日收盤（事後發現 60 分 K 最後一根不含收盤競價）")
    args = p.parse_args()
    out = Path(args.out)
    hourly = bars("60m", "2y")
    official = None
    if args.official_close:
        official = {day: next(iter(b.values()))["close"] for day, b in bars("1d", "2y").items() if b}
    rows = build_rows(hourly, "09:00", "12:00", ["09:00", "10:00", "11:00", "12:00", "13:00"], official)
    half = len(rows) // 2
    result = {"full": summarize(rows), "year1": summarize(rows[:half]), "year2": summarize(rows[half:]),
              "first_day": rows[0]["day"], "last_day": rows[-1]["day"]}
    # 參考：近 60 天 30 分 K，原文定義（首半小時 09:30、最後半小時 13:00→13:30）
    halfhour = bars("30m", "60d")
    rows30 = build_rows(halfhour, "09:00", "12:30", ["09:00", "12:30", "13:00"], official)
    result["halfhour_60d"] = summarize(rows30)
    passed = (result["full"]["regression"]["b"] > 0 and result["full"]["regression"]["p_one_sided"] < 0.05
              and result["full"]["mean_net_pct"] > 0 and result["full"]["ci95_net_pct"][0] > 0)
    result["passed"] = passed
    tag = "_official_close" if args.official_close else ""
    (out / f"result{tag}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / f"daily{tag}.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    def line(name, s):
        r = s["regression"]
        return (f"| {name} | {s['days']} | {r['b']:+.4f} | {r['t']:+.2f} | {r['p_one_sided']:.3f} | {r['r2'] * 100:.2f}% | "
                f"{s['hit_rate_pct']:.1f}% | {s['mean_gross_pct'] * 100:+.2f} | {s['mean_net_pct'] * 100:+.2f} | "
                f"{s['ci95_net_pct'][0] * 100:+.2f} ～ {s['ci95_net_pct'][1] * 100:+.2f} |")

    text = ["# 加權指數日內動能：複製檢驗結果", "", "照 PREREGISTRATION.md 一次跑完。報酬單位：bp（0.01%）。", "",
            "| 樣本 | 天數 | 斜率 b | t 值 | 單尾 p | R² | 方向命中率 | 每日毛報酬 bp | 每日淨報酬 bp | 淨報酬 95% CI bp |",
            "|---|---|---|---|---|---|---|---|---|---|",
            line(f"全期 {result['first_day']}～{result['last_day']}", result["full"]),
            line("第一年", result["year1"]), line("第二年", result["year2"]),
            line("參考：近 60 天 30 分 K（首半小時）", result["halfhour_60d"]), "",
            f"平均成本（小台，每日一來回）：{result['full']['mean_cost_pct'] * 100:.2f} bp", "",
            "**結論：" + ("兩項標準都通過，複製成功。" if passed else "未通過及格標準，複製不成功。") + "**", ""]
    (out / f"RESULT{tag}.md").write_text("\n".join(text), encoding="utf-8")
    print("\n".join(text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
