#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gap_strategy_test.py — 台指期開盤跳空多空雙向日內策略（照 gap_strategy/PREREGISTRATION.md）

輸入：期交所「期貨每日交易行情」TX 月檔（futDataDown，Big5 CSV），放在 --data 資料夾。
用法：python gap_strategy_test.py --data tx_daily --out gap_strategy
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

SPLIT = "2016/01/01"
SLIP_POINTS, TAX, COMMISSION, POINT_VALUE = 1.0, 0.00002, 30.0, 50


def num(text):
    try:
        return float(str(text).replace(",", "").strip())
    except ValueError:
        return None


def load(folder: str):
    by_date = defaultdict(list)
    for path in sorted(glob.glob(f"{folder}/TX_*.csv")):
        text = open(path, "rb").read().decode("big5", errors="replace").splitlines()
        reader = csv.reader(text)
        header = next(reader, None)
        if not header or "交易日期" not in header[0]:
            continue
        idx = {name.strip(): i for i, name in enumerate(header)}
        for row in reader:
            if len(row) < len(header) - 3:
                continue
            session = row[idx["交易時段"]].strip() if "交易時段" in idx else "一般"
            month = row[idx["到期月份(週別)"]].strip()
            if session != "一般" or "/" in month or "W" in month.upper():
                continue
            o, c, v = num(row[idx["開盤價"]]), num(row[idx["收盤價"]]), num(row[idx["成交量"]])
            if not o or not c or not v:
                continue
            by_date[row[idx["交易日期"]].strip()].append({"month": month, "open": o, "close": c, "volume": v})
    days = []
    closes_prev = {}
    for date in sorted(by_date):
        contracts = by_date[date]
        main = max(contracts, key=lambda x: x["volume"])
        prev = closes_prev.get(main["month"])
        if prev:
            price = main["open"]
            cost = 2 * SLIP_POINTS / price + 2 * TAX + 2 * COMMISSION / (price * POINT_VALUE)
            days.append({"date": date, "month": main["month"], "open": main["open"], "close": main["close"],
                         "prev_close": prev, "gap": main["open"] / prev - 1, "r": main["close"] / main["open"] - 1,
                         "cost": cost})
        closes_prev = {x["month"]: x["close"] for x in contracts}
    return days


def ci95(xs, iterations=2000, seed=7):
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(xs, k=len(xs))) for _ in range(iterations))
    return means[int(0.025 * iterations)], means[int(0.975 * iterations) - 1]


def quantile(sorted_values, q):
    return sorted_values[min(len(sorted_values) - 1, int(q * len(sorted_values)))]


def evaluate(days, rule):
    trades = []
    for d in days:
        side = rule(d)
        if side:
            trades.append({"date": d["date"], "side": side, "gross": side * d["r"], "net": side * d["r"] - d["cost"]})
    if not trades:
        return {"n": 0}, trades
    net = [t["net"] * 1e4 for t in trades]
    lo, hi = ci95(net)
    longs = [t for t in trades if t["side"] > 0]
    shorts = [t for t in trades if t["side"] < 0]
    return {"n": len(trades), "win_rate": round(sum(x > 0 for x in net) / len(net) * 100, 1),
            "mean_net_bp": round(statistics.fmean(net), 2), "ci_low": round(lo, 2), "ci_high": round(hi, 2),
            "mean_gross_bp": round(statistics.fmean(t["gross"] * 1e4 for t in trades), 2),
            "sum_net_pct": round(sum(net) / 100, 1),
            "long_n": len(longs), "long_mean_net_bp": round(statistics.fmean(t["net"] * 1e4 for t in longs), 2) if longs else None,
            "short_n": len(shorts), "short_mean_net_bp": round(statistics.fmean(t["net"] * 1e4 for t in shorts), 2) if shorts else None}, trades


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    out = Path(args.out)
    days = load(args.data)
    explore = [d for d in days if d["date"] < SPLIT]
    validate = [d for d in days if d["date"] >= SPLIT]
    gaps = sorted(d["gap"] for d in explore)
    q = {k: quantile(gaps, v) for k, v in (("q10", 0.10), ("q20", 0.20), ("q80", 0.80), ("q90", 0.90))}
    rules = {
        "G1 反轉（20/80 分位）": lambda d: -1 if d["gap"] >= q["q80"] else (1 if d["gap"] <= q["q20"] else 0),
        "G2 反轉（10/90 分位）": lambda d: -1 if d["gap"] >= q["q90"] else (1 if d["gap"] <= q["q10"] else 0),
        "G3 順勢（20/80 分位）": lambda d: 1 if d["gap"] >= q["q80"] else (-1 if d["gap"] <= q["q20"] else 0),
        "D1 每天放空（對照、已污染）": lambda d: -1,
    }
    result = {"days": len(days), "first": days[0]["date"], "last": days[-1]["date"], "explore_days": len(explore),
              "validate_days": len(validate), "thresholds_pct": {k: round(v * 100, 3) for k, v in q.items()}, "rules": {}}
    yearly = {}
    for name, rule in rules.items():
        ex, _ = evaluate(explore, rule)
        va, trades = evaluate(validate, rule)
        passed = va["n"] >= 100 and va["mean_net_bp"] > 0 and va["ci_low"] > 0
        result["rules"][name] = {"explore": ex, "validate": va, "passed": passed}
        per_year = defaultdict(list)
        for t in trades:
            per_year[t["date"][:4]].append(t["net"] * 1e4)
        yearly[name] = {y: (len(v), round(statistics.fmean(v), 1)) for y, v in sorted(per_year.items())}
    result["validate_yearly"] = yearly
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "days.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(days[0]))
        writer.writeheader()
        writer.writerows(days)

    t = result["thresholds_pct"]
    lines = ["# 台指期開盤跳空多空策略：結果", "", "照 PREREGISTRATION.md 一次跑完。單位 bp（0.01%），每筆＝當天開盤進、收盤出。", "",
             f"資料：{result['first']}～{result['last']}，{result['days']} 天（探索 {result['explore_days']}、驗證 {result['validate_days']}）。",
             f"探索期跳空分位：10%={t['q10']:+.2f}%、20%={t['q20']:+.2f}%、80%={t['q80']:+.2f}%、90%={t['q90']:+.2f}%", "",
             "| 規則 | 探索期 n / 平均淨 bp | 驗證期 n / 勝率 / 平均淨 bp | 驗證期 95% CI | 驗證期 做多 / 放空 平均淨 bp | 及格 |",
             "|---|---|---|---|---|---|"]
    for name, r in result["rules"].items():
        ex, va = r["explore"], r["validate"]
        lines.append(f"| {name} | {ex['n']} / {ex['mean_net_bp']:+.1f} | {va['n']} / {va['win_rate']}% / {va['mean_net_bp']:+.1f} | "
                     f"{va['ci_low']:+.1f} ～ {va['ci_high']:+.1f} | {va['long_mean_net_bp'] if va['long_mean_net_bp'] is not None else '—'} / "
                     f"{va['short_mean_net_bp'] if va['short_mean_net_bp'] is not None else '—'} | {'✅' if r['passed'] else '❌'} |")
    lines += ["", "## 驗證期分年度（筆數, 平均淨 bp）", ""]
    names = list(rules)
    lines.append("| 年 | " + " | ".join(names) + " |")
    lines.append("|---" * (len(names) + 1) + "|")
    for y in sorted({y for v in yearly.values() for y in v}):
        lines.append(f"| {y} | " + " | ".join(f"{yearly[n].get(y, ('—', '—'))[0]}, {yearly[n].get(y, ('—', '—'))[1]}" for n in names) + " |")
    (out / "RESULT.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
