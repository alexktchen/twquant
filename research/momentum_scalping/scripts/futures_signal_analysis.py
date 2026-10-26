#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
futures_signal_analysis.py — 照 PREREGISTRATION.md 執行：三分位分組、探索期選組、驗證期檢驗。

用法：python futures_signal_analysis.py --events signal_dataset/events.csv --split 2026-10-02 --out signal_dataset
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional

FEATURES = ["f_minute", "f_day_change_pct", "f_gap_pct", "f_ma5_dev_pct", "f_vwap_dev_pct", "f_day_range_pct",
            "f_pos_in_range", "f_volume_expansion", "f_range_expansion", "f_mom_1m", "f_mom_3m", "f_mom_5m",
            "f_ema_spread_pct", "f_ema_slope_pct", "f_wave_extension_pct", "f_wave_age_bars", "f_stop_distance_pct"]
NAMES = {
    "f_minute": "時段（開盤後分鐘）", "f_day_change_pct": "當日漲跌%", "f_gap_pct": "開盤跳空%",
    "f_ma5_dev_pct": "距日線 MA5%", "f_vwap_dev_pct": "距 VWAP%", "f_day_range_pct": "當日振幅%",
    "f_pos_in_range": "位於當日高低區間的位置", "f_volume_expansion": "量比", "f_range_expansion": "振幅比",
    "f_mom_1m": "1 分動能", "f_mom_3m": "3 分動能", "f_mom_5m": "5 分動能", "f_ema_spread_pct": "EMA5-EMA10 差距%",
    "f_ema_slope_pct": "EMA5 斜率%", "f_wave_extension_pct": "距波段起點%", "f_wave_age_bars": "波段第幾根",
    "f_stop_distance_pct": "停損距離%", "strategy": "策略別",
}
MIN_N, MIN_R = 40, 0.20


def num(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def mean(xs: List[float]) -> Optional[float]:
    return statistics.fmean(xs) if xs else None


def ci95(xs: List[float], iterations: int = 2000, seed: int = 7):
    if len(xs) < 2:
        return None, None
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(xs, k=len(xs))) for _ in range(iterations))
    return means[int(0.025 * iterations)], means[int(0.975 * iterations) - 1]


def tercile_edges(values: List[float]):
    s = sorted(values)
    return s[len(s) // 3], s[2 * len(s) // 3]


def bucket(value: float, edges) -> int:
    return 0 if value < edges[0] else (1 if value < edges[1] else 2)


def describe(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    r = [row["r"] for row in rows]
    lo, hi = ci95(r)
    return {"n": len(r), "mean_r": None if not r else round(mean(r), 4),
            "win_rate": None if not r else round(sum(x > 0 for x in r) / len(r) * 100, 1),
            "ci_low": None if lo is None else round(lo, 4), "ci_high": None if hi is None else round(hi, 4),
            "net_points": None if not rows else round(mean([row["points"] for row in rows]), 2)}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--events", required=True)
    p.add_argument("--split", required=True, help="驗證期第一天（含）")
    p.add_argument("--out", required=True)
    args = p.parse_args()
    raw = list(csv.DictReader(open(args.events, encoding="utf-8-sig")))
    rows = []
    for e in raw:
        r = num(e.get("label_net_r"))
        if r is None:
            continue
        rows.append({**e, "r": r, "points": num(e.get("label_net_points")) or 0.0})
    train = [x for x in rows if x["day"] < args.split]
    test = [x for x in rows if x["day"] >= args.split]

    results = []
    for feature in FEATURES:
        tr = [x for x in train if num(x[feature]) is not None]
        te = [x for x in test if num(x[feature]) is not None]
        edges = tercile_edges([num(x[feature]) for x in tr])
        groups_tr = [[x for x in tr if bucket(num(x[feature]), edges) == b] for b in range(3)]
        groups_te = [[x for x in te if bucket(num(x[feature]), edges) == b] for b in range(3)]
        best = max(range(3), key=lambda b: mean([x["r"] for x in groups_tr[b]]) if groups_tr[b] else -9)
        stats_te = describe(groups_te[best])
        passed = (stats_te["n"] >= MIN_N and stats_te["mean_r"] is not None and stats_te["mean_r"] > MIN_R
                  and stats_te["ci_low"] is not None and stats_te["ci_low"] > 0)
        label = ["低", "中", "高"][best]
        results.append({"feature": feature, "name": NAMES[feature], "edges": [round(edges[0], 5), round(edges[1], 5)],
                        "chosen": label, "train_groups": [describe(g) for g in groups_tr],
                        "test_groups": [describe(g) for g in groups_te], "train_chosen": describe(groups_tr[best]),
                        "test_chosen": stats_te, "passed": passed})
    # 策略別
    strat_tr = {s: [x for x in train if x["strategy"] == s] for s in ("TREND", "MOMENTUM")}
    best_s = max(strat_tr, key=lambda s: mean([x["r"] for x in strat_tr[s]]) if strat_tr[s] else -9)
    te_s = describe([x for x in test if x["strategy"] == best_s])
    results.append({"feature": "strategy", "name": NAMES["strategy"], "edges": None, "chosen": best_s,
                    "train_groups": [describe(strat_tr[s]) for s in ("TREND", "MOMENTUM")],
                    "test_groups": [describe([x for x in test if x["strategy"] == s]) for s in ("TREND", "MOMENTUM")],
                    "train_chosen": describe(strat_tr[best_s]), "test_chosen": te_s,
                    "passed": te_s["n"] >= MIN_N and (te_s["mean_r"] or 0) > MIN_R and (te_s["ci_low"] or -1) > 0})

    summary = {
        "split": args.split, "train_days": sorted({x["day"] for x in train}), "test_days": sorted({x["day"] for x in test}),
        "baseline": {"all": describe(rows), "train": describe(train), "test": describe(test),
                     "by_strategy_all": {s: describe([x for x in rows if x["strategy"] == s]) for s in ("TREND", "MOMENTUM")}},
        "exit_reasons": dict(sorted({k: sum(1 for x in rows if x["label_exit_reason"] == k)
                                     for k in {x["label_exit_reason"] for x in rows}}.items(), key=lambda kv: -kv[1])),
        "features": results, "passed": [r["feature"] for r in results if r["passed"]],
        "criteria": {"min_n": MIN_N, "min_mean_r": MIN_R, "ci_low_gt": 0},
    }
    out = Path(args.out)
    (out / "analysis.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    b = summary["baseline"]
    lines = ["# 小台指期動能訊號：可篩選性檢驗結果", "",
             "照 PREREGISTRATION.md 執行，一次跑完，未調整任何設定。", "",
             "## 基準：全部訊號（不篩選）", "",
             "| 期間 | 筆數 | 勝率 | 平均 net R | 95% CI | 平均淨點數 |", "|---|---|---|---|---|---|"]
    for key, name in (("train", "探索期"), ("test", "驗證期"), ("all", "全部")):
        d = b[key]
        lines.append(f"| {name} | {d['n']} | {d['win_rate']}% | {d['mean_r']:+.3f} | {d['ci_low']:+.3f} ～ {d['ci_high']:+.3f} | {d['net_points']:+.1f} |")
    lines += ["", "## 各特徵：探索期選出的那一組，在驗證期的表現", "",
              f"及格：驗證期筆數 ≥ {MIN_N}、平均 net R > {MIN_R}、95% CI 下界 > 0。", "",
              "| 特徵 | 選的組 | 探索期 n / 平均R | 驗證期 n / 平均R | 驗證期 95% CI | 及格 |", "|---|---|---|---|---|---|"]
    for r in results:
        tc, te = r["train_chosen"], r["test_chosen"]
        lines.append(f"| {r['name']} | {r['chosen']} | {tc['n']} / {tc['mean_r']:+.3f} | {te['n']} / "
                     f"{(te['mean_r'] if te['mean_r'] is not None else float('nan')):+.3f} | "
                     f"{te['ci_low'] if te['ci_low'] is not None else '—'} ～ {te['ci_high'] if te['ci_high'] is not None else '—'} | "
                     f"{'✅' if r['passed'] else '❌'} |")
    verdict = ("**0 個特徵及格 → 沒有找到可篩選的優勢，依登記不做 ML、不加規則。**" if not summary["passed"]
               else f"**{len(summary['passed'])} 個特徵及格：{', '.join(NAMES[f] for f in summary['passed'])}。"
                    "共檢驗 18 個，單一個及格可能是運氣，需在新資料上再驗一次。**")
    lines += ["", "## 結論", "", verdict, ""]
    (out / "ANALYSIS.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
