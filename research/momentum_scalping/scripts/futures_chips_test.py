#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
futures_chips_test.py — 測試 B：籌碼濾網 → 隔天台指期開盤放空、收盤回補（照 short_futures/PREREGISTRATION.md）

用法：python futures_chips_test.py --tx tx_daily --data short_futures/data --out short_futures
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gap_strategy_test as G  # noqa: E402

VALIDATE_L = "2016/01/01"
LAST_TRADE = "2026/10/22"


def ci95(xs, iterations=2000, seed=7):
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(xs, k=len(xs))) for _ in range(iterations))
    return means[int(0.025 * iterations)], means[int(0.975 * iterations) - 1]


def quantile(values, q):
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


def stats(trades):
    if not trades:
        return {"n": 0}
    net = [t["net_bp"] for t in trades]
    lo, hi = ci95(net)
    return {"n": len(net), "win_rate": round(sum(x > 0 for x in net) / len(net) * 100, 1),
            "mean_net_bp": round(statistics.fmean(net), 2), "ci_low": round(lo, 2), "ci_high": round(hi, 2),
            "mean_gross_bp": round(statistics.fmean(t["gross_bp"] for t in trades), 2)}


def load_signals(data: Path):
    large = defaultdict(dict)
    for row in csv.DictReader((data / "large_trader_tx.csv").open(encoding="utf-8")):
        if row["trader_type"] == "1":
            total = float(row["total_oi"])
            if total > 0:
                large[row["date"]]["L"] = (float(row["top10_buy"]) - float(row["top10_sell"])) / total
    dates = sorted(large)
    for prev, cur in zip(dates, dates[1:]):
        large[cur]["dL"] = large[cur]["L"] - large[prev]["L"]
    inst = defaultdict(dict)
    for row in csv.DictReader((data / "institutional.csv").open(encoding="utf-8")):
        key = "foreign" if row["identity"].startswith("外資") else row["identity"]
        inst[(row["commodity"], row["date"])][key] = float(row["net_oi"].replace(",", ""))
    mtx_oi = {r["date"]: float(r["mtx_total_oi"]) for r in csv.DictReader((data / "mtx_oi.csv").open(encoding="utf-8"))}
    chips = defaultdict(dict)
    tx_dates = sorted(d for c, d in inst if c == "TXF")
    for prev, cur in zip(tx_dates, tx_dates[1:]):
        a, b = inst[("TXF", prev)].get("foreign"), inst[("TXF", cur)].get("foreign")
        if a is not None and b is not None:
            chips[cur]["dF"] = b - a
    for (c, d), v in inst.items():
        if c == "MXF" and d in mtx_oi and len(v) == 3:
            chips[d]["R"] = -sum(v.values()) / mtx_oi[d]
    return large, chips


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tx", required=True)
    p.add_argument("--data", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    out = Path(args.out)
    days = [d for d in G.load(args.tx) if d["date"] <= LAST_TRADE]
    next_day = {a["date"]: b for a, b in zip(days, days[1:])}
    large, chips = load_signals(Path(args.data))

    first_f = min(chips) if chips else None
    split_f = (date.fromisoformat(first_f.replace("/", "-")) + timedelta(days=365)).isoformat().replace("-", "/") if first_f else None
    specs = {
        "L1 大額特定法人淨部位最偏空": (large, "L", "low", VALIDATE_L, 100),
        "L2 大額特定法人淨部位降最多": (large, "dL", "low", VALIDATE_L, 100),
        "F1 外資淨未平倉加空最多": (chips, "dF", "low", split_f, 60),
        "F3 散戶多空比最偏多（反指標）": (chips, "R", "high", split_f, 60),
    }
    result = {"first_tx": days[0]["date"], "last_tx": days[-1]["date"], "large_first": min(large), "large_last": max(large),
              "chips_first": first_f, "chips_split": split_f, "rules": {}}
    yearly = {}
    for name, (source, key, tail, split, min_n) in specs.items():
        explore_vals = [v[key] for d, v in source.items() if key in v and d < split]
        lo_q, hi_q = quantile(explore_vals, 0.2), quantile(explore_vals, 0.8)

        def trades_for(pick, start, end):
            rows = []
            for d, v in sorted(source.items()):
                if key not in v or not (start <= d < end) or d not in next_day or not pick(v[key]):
                    continue
                n = next_day[d]
                rows.append({"signal": d, "trade": n["date"], "gross_bp": -n["r"] * 1e4, "net_bp": (-n["r"] - n["cost"]) * 1e4})
            return rows

        signal = (lambda x: x <= lo_q) if tail == "low" else (lambda x: x >= hi_q)
        other = (lambda x: x >= hi_q) if tail == "low" else (lambda x: x <= lo_q)
        ex = trades_for(signal, "0000", split)
        va = trades_for(signal, split, "9999")
        va_other = trades_for(other, split, "9999")
        va_all = trades_for(lambda x: True, split, "9999")
        s = stats(va)
        passed = s["n"] >= min_n and s["mean_net_bp"] > 0 and s["ci_low"] > 0
        result["rules"][name] = {"key": key, "threshold": lo_q if tail == "low" else hi_q, "explore": stats(ex),
                                 "validate": s, "validate_other_tail": stats(va_other), "validate_every_day": stats(va_all),
                                 "split": split, "min_n": min_n, "passed": passed}
        per = defaultdict(list)
        for t in va:
            per[t["trade"][:4]].append(t["net_bp"])
        yearly[name] = {y: (len(v), round(statistics.fmean(v), 1)) for y, v in sorted(per.items())}
    result["yearly"] = yearly
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# 測試 B：期貨籌碼濾網 → 隔天小台開盤空、收盤補", "",
             "照 PREREGISTRATION.md 一次跑完。單位 bp（0.01%），已扣小台成本。", "",
             f"大額交易人資料 {result['large_first']}～{result['large_last']}；三大法人 {first_f} 起（探索期到 {split_f}）。", "",
             "| 濾網 | 探索期 n / 平均淨 bp | 驗證期 n / 勝率 / 平均淨 bp | 驗證期 95% CI | 另一端（最偏多）平均淨 bp | 同期每天放空 | 及格 |",
             "|---|---|---|---|---|---|---|"]
    for name, r in result["rules"].items():
        e, v, o, a = r["explore"], r["validate"], r["validate_other_tail"], r["validate_every_day"]
        lines.append(f"| {name} | {e['n']} / {e.get('mean_net_bp', 0):+.1f} | {v['n']} / {v.get('win_rate')}% / {v.get('mean_net_bp', 0):+.1f} | "
                     f"{v.get('ci_low', 0):+.1f} ～ {v.get('ci_high', 0):+.1f} | {o.get('mean_net_bp', 0):+.1f}（{o['n']} 天） | "
                     f"{a.get('mean_net_bp', 0):+.1f}（{a['n']} 天） | {'✅' if r['passed'] else '❌'} |")
    lines += ["", "## 驗證期分年度（筆數, 平均淨 bp）", ""]
    for name, y in yearly.items():
        lines.append(f"- {name}：" + "；".join(f"{k} {n} 筆 {m:+.1f}" for k, (n, m) in y.items()))
    (out / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
