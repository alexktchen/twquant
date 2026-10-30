#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""short_composite_test.py：台指期複合放空（studies/short_composite/PREREGISTRATION.md）。

用法：python short_composite_test.py --fubon <fubon_1m> --tx <tx_daily> --chips <data> --trades <fubon_9y/trades.csv> --out <studies/short_composite>
只跑一次；不及格不調參數。原始資料都在 data/research_ms，不進 git。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gap_strategy_test as G  # noqa: E402
import minute_bidir_test as M  # noqa: E402

START, END = "2017-05-22", "2026-09-08"       # 2026-09-09 起是封存的第二保留段（D-109），不碰
LOOK_TREND, WINDOW, VOL_WIN, TAIL_MIN = (21, 63, 252), 250, 20, 13 * 60 + 15
Q, SEED, ITER, MIN_T, TOP_SHARE = 0.20, 7, 2000, 3.0, 0.5


def daily_returns(folder: str):
    """[(YYYY-MM-DD, 同合約收盤對收盤報酬)]"""
    return [(d["date"].replace("/", "-"), d["close"] / d["prev_close"] - 1) for d in G.load(folder)]


def load_l(path: Path):
    out = {}
    for row in csv.DictReader(path.open(encoding="utf-8")):
        if row["trader_type"] == "1" and float(row["total_oi"]) > 0:
            out[row["date"].replace("/", "-")] = (float(row["top10_buy"]) - float(row["top10_sell"])) / float(row["total_oi"])
    return out


def quantile(values, q):
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


def votes(rets, l_series):
    """{交易日 t: {trend, chips, vol}}，全部只用 t−1 收盤已知的資料。"""
    days = [d for d, _ in rets]
    r = [x for _, x in rets]
    log = [math.log1p(x) for x in r]
    l_days = sorted(l_series)
    vols = [None if i + 1 < VOL_WIN else statistics.pstdev(r[i + 1 - VOL_WIN : i + 1]) for i in range(len(r))]
    out = {}
    for t in range(1, len(days)):
        i = t - 1  # t−1 收盤
        cuts = [sum(log[i + 1 - n : i + 1]) for n in LOOK_TREND if i + 1 >= n]
        if len(cuts) < len(LOOK_TREND):
            continue
        known = [d for d in l_days if d <= days[i]]
        if len(known) < WINDOW + 1 or vols[i] is None:
            continue
        hist_l = [l_series[d] for d in known[-WINDOW - 1 : -1]]
        hist_v = [v for v in vols[max(0, i - WINDOW) : i] if v is not None]
        if len(hist_v) < WINDOW:
            continue
        out[days[t]] = {"trend": int(sum(c < 0 for c in cuts) >= 2), "chips": int(l_series[known[-1]] <= quantile(hist_l, Q)),
                        "vol": int(vols[i] > statistics.median(hist_v))}
    return out


def tail_leg(days):
    """B 尾盤動能空：13:15 時價 < 昨收 → 下一根 K 開盤放空，收盤前回補。"""
    out = {}
    for d in days:
        if d.prev_close is None or TAIL_MIN >= d.eod or d.price_at(TAIL_MIN) >= d.prev_close:
            continue
        et, ep, _ = d.fill(TAIL_MIN, -1)
        xt, xp, _ = d.fill(d.eod, 1)
        if et >= xt:
            continue
        out[d.day] = M.trade(d, "TAIL", -1, TAIL_MIN, (et, ep), (xt, xp), "EOD")
    return out


def noise_leg(path: Path):
    out = defaultdict(float)
    for row in csv.DictReader(path.open(encoding="utf-8-sig")):
        if row["rule"] == "NOISE" and int(row["side"]) < 0:
            out[row["day"]] += float(row["net_pnl"])
    return out


def ci95(xs):
    rng = random.Random(SEED)
    m = sorted(statistics.fmean(rng.choices(xs, k=len(xs))) for _ in range(ITER))
    return m[int(0.025 * ITER)], m[int(0.975 * ITER) - 1]


def stat(xs):
    if len(xs) < 2:
        return {"days": len(xs)}
    lo, hi = ci95(xs)
    mean, sd = statistics.fmean(xs), statistics.stdev(xs)
    return {"days": len(xs), "mean": round(mean, 1), "ci_low": round(lo, 1), "ci_high": round(hi, 1), "t": round(mean / (sd / math.sqrt(len(xs))), 2) if sd else None,
            "total": round(sum(xs), 0), "active_days": sum(x != 0 for x in xs), "win_rate_active": round(sum(x > 0 for x in xs) / max(1, sum(x != 0 for x in xs)) * 100, 1)}


def row(name, s):
    return (f"| {name} | {s.get('days', 0)} | {s.get('active_days', '')} | {s.get('win_rate_active', '')}% | {s.get('mean', 0):+,.0f} | "
            f"{s.get('ci_low', 0):+,.0f} ～ {s.get('ci_high', 0):+,.0f} | {s.get('t', '')} | {s.get('total', 0):+,.0f} |")


def main() -> int:
    p = argparse.ArgumentParser()
    for name in ("fubon", "tx", "chips", "trades", "out"):
        p.add_argument(f"--{name}", required=True)
    a = p.parse_args()
    raw = M.load_bars(Path(a.fubon))
    kind = M.average_kind(raw, sorted(raw)[:30])
    days, excluded = M.build_days(raw, kind)
    tail = tail_leg(days)
    noise = noise_leg(Path(a.trades))
    v = votes(daily_returns(a.tx), load_l(Path(a.chips) / "large_trader_tx.csv"))
    sample = [d.day for d in days if START <= d.day <= END and d.day in v]
    missing = [d.day for d in days if START <= d.day <= END and d.day not in v]
    pnl = {d: (noise.get(d, 0.0), tail[d]["net_pnl"] if d in tail else 0.0) for d in sample}
    gate = {d: sum(v[d].values()) >= 2 for d in sample}
    on, off = [d for d in sample if gate[d]], [d for d in sample if not gate[d]]
    both = [sum(pnl[d]) for d in on]

    report = {"all_days": stat([sum(pnl[d]) for d in sample]), "gate_on": stat(both), "gate_off": stat([sum(pnl[d]) for d in off]),
              "A_on": stat([pnl[d][0] for d in on]), "A_off": stat([pnl[d][0] for d in off]), "B_on": stat([pnl[d][1] for d in on]), "B_off": stat([pnl[d][1] for d in off])}
    for key in ("trend", "chips", "vol"):
        report[f"only_{key}"] = stat([sum(pnl[d]) for d in sample if v[d][key]])
    years = defaultdict(list)
    for d in on:
        years[d[:4]].append(sum(pnl[d]))
    yearly = {y: {"days": len(x), "total": round(sum(x), 0)} for y, x in sorted(years.items())}
    without_2026 = sum(x["total"] for y, x in yearly.items() if y != "2026")
    total = sum(both)
    top5 = sum(sorted(both, reverse=True)[:5])
    s = report["gate_on"]
    checks = {"ci_low_above_0": s.get("ci_low", -1) > 0, "without_2026_above_0": without_2026 > 0,
              "top5_share_below_50pct": total > 0 and top5 / total < TOP_SHARE, "t_at_least_3": (s.get("t") or 0) >= MIN_T}
    passed = all(checks.values())
    out = {"sample": [sample[0], sample[-1]], "n_sample": len(sample), "n_without_votes": len(missing), "excluded_days": len(excluded), "vwap_kind": kind,
           "gate_share_pct": round(len(on) / len(sample) * 100, 1), "report": report, "yearly_on": yearly, "without_2026": without_2026,
           "top5": top5, "total": total, "checks": checks, "passed": passed}
    dest = Path(a.out)
    (dest / "result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["# 台指期複合放空：結果", "", "照 PREREGISTRATION.md 一次跑完。金額是小台 1 口的新台幣，已扣成本。", "",
             f"樣本 {sample[0]}～{sample[-1]}，{len(sample)} 個交易日；閘門開 {len(on)} 天（{out['gate_share_pct']}%）。", "",
             f"**主要判定：{'及格' if passed else '不及格'}。**", "", "| 條件 | 結果 |", "|---|---|"]
    lines += [f"| {k} | {'✅' if c else '❌'} |" for k, c in checks.items()]
    lines += ["", f"不含 2026 合計 {without_2026:+,.0f} 元；最好 5 天 {top5:+,.0f} 元，佔總獲利 {top5 / total * 100 if total > 0 else 0:.0f}%。", "",
              "| 範圍 | 天數 | 有交易的天 | 有交易時賺錢比例 | 每日平均 | 95% CI | t | 合計 |", "|---|---|---|---|---|---|---|---|"]
    names = {"gate_on": "**閘門開：A＋B（主要）**", "gate_off": "閘門關：A＋B", "all_days": "全部日子：A＋B", "A_on": "A 噪音區空，閘門開", "A_off": "A 噪音區空，閘門關",
             "B_on": "B 尾盤空，閘門開", "B_off": "B 尾盤空，閘門關", "only_trend": "只看趨勢票", "only_chips": "只看法人票", "only_vol": "只看波動票"}
    lines += [row(names[k], report[k]) for k in names]
    lines += ["", "## 閘門開的分年度", "", "| 年 | 天數 | 合計 |", "|---|---|---|"]
    lines += [f"| {y} | {x['days']} | {x['total']:+,.0f} |" for y, x in yearly.items()]
    (dest / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
