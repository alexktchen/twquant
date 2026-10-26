#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
combo_oos_test.py — 「ORB 只做多 + 噪音區只放空」樣本外驗證（照 combo/PREREGISTRATION.md）

用 Yahoo ^TWII 盤中 K 線當小台的代理：5 分 K 測 ORB、60 分 K／30 分 K 測噪音區。
沒有成交量，VWAP 以當日 K 線收盤累計平均代替。成本照小台。

用法：python combo_oos_test.py --raw combo/raw --out combo
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

TPE = timezone(timedelta(hours=8))
POINT_VALUE, COMMISSION, TAX, SLIP = 50, 30.0, 0.00002, 1.0
ORB_TARGET_R, NOISE_LOOKBACK = 10, 14
SELECTION = ("2026-09-09", "2026-10-22")  # 挑組合用掉的 29 天
ORB_LAST_BAR = "13:20"


def bars(path: Path):
    d = json.loads(path.read_text())["chart"]["result"][0]
    q = d["indicators"]["quote"][0]
    by_day = defaultdict(list)
    for i, ts in enumerate(d["timestamp"]):
        if None in (q["open"][i], q["high"][i], q["low"][i], q["close"][i]):
            continue
        dt = datetime.fromtimestamp(ts, TPE)
        by_day[dt.date().isoformat()].append((dt.strftime("%H:%M"), q["open"][i], q["high"][i], q["low"][i], q["close"][i]))
    return {k: sorted(v) for k, v in sorted(by_day.items())}


def daily_closes(path: Path):
    return {day: rows[-1][4] for day, rows in bars(path).items()}


def net(entry, exit_, side):
    points = side * (exit_ - entry)
    return points, points * POINT_VALUE - 2 * COMMISSION - TAX * (entry + exit_) * POINT_VALUE


def orb_long(day, rows):
    if rows[0][0] != "09:00" or len(rows) < 3:
        return []
    _, o, _, low, c = rows[0]
    if c <= o:
        return []
    entry, stop = rows[1][1] + SLIP, low
    risk = entry - stop
    if risk <= 0:
        return []
    target, exit_, reason = entry + ORB_TARGET_R * risk, None, "EOD"
    last = [r for r in rows if r[0] <= ORB_LAST_BAR][-1]
    for label, _o, h, lo, cl in rows[1:]:
        if label > ORB_LAST_BAR:
            break
        if lo <= stop:  # 同一根同時碰停損、停利 → 算先停損
            exit_, reason = stop - SLIP, "STOP"
            break
        if h >= target:
            exit_, reason = target - SLIP, "TARGET"
            break
    if exit_ is None:
        exit_ = last[4] - SLIP
    points, pnl = net(entry, exit_, 1)
    return [{"day": day, "sleeve": "ORB_LONG", "side": 1, "entry": round(entry, 2), "exit": round(exit_, 2),
             "reason": reason, "points": round(points, 2), "net_pnl": round(pnl, 1)}]


def noise_short(day, rows, history, prev_close, checks):
    labels = [r[0] for r in rows]
    if any(c not in labels for c in checks) or prev_close is None:
        return []
    open_ = rows[0][1]
    closes = {r[0]: r[4] for r in rows}
    sigma = {c: statistics.fmean(abs(h[c] / h["_open"] - 1) for h in history) for c in checks}
    trades, pos, entry = [], 0, None
    running = []
    for label, _o, _h, _l, close in rows:
        running.append(close)
        if label not in checks:
            continue
        lb = min(open_, prev_close) * (1 - sigma[label])
        twap = statistics.fmean(running)
        target = -1 if close < min(lb, twap) else 0
        if target == pos:
            continue
        if pos:
            points, pnl = net(entry, close + SLIP, -1)
            trades.append({"day": day, "sleeve": "NOISE_SHORT", "side": -1, "entry": round(entry, 2),
                           "exit": round(close + SLIP, 2), "reason": "EXIT", "points": round(points, 2),
                           "net_pnl": round(pnl, 1), "entry_label": entry_label, "exit_label": label})
        else:
            entry, entry_label = close - SLIP, label
        pos = target
    if pos:
        final = rows[-1][4] + SLIP
        points, pnl = net(entry, final, -1)
        trades.append({"day": day, "sleeve": "NOISE_SHORT", "side": -1, "entry": round(entry, 2), "exit": round(final, 2),
                       "reason": "EOD", "points": round(points, 2), "net_pnl": round(pnl, 1),
                       "entry_label": entry_label, "exit_label": rows[-1][0]})
    return trades


def run_noise(series, closes, checks, days_wanted):
    out, done = [], []
    names = list(series)
    complete = [d for d in names if all(c in {r[0] for r in series[d]} for c in checks)]
    for k, day in enumerate(complete):
        hist = complete[max(0, k - NOISE_LOOKBACK):k]
        if day in days_wanted and len(hist) == NOISE_LOOKBACK:
            history = [{**{r[0]: r[4] for r in series[h]}, "_open": series[h][0][1]} for h in hist]
            prev_day = names[names.index(day) - 1] if names.index(day) else None
            prev_close = closes.get(prev_day) if prev_day else None
            out += noise_short(day, series[day], history, prev_close, checks)
            done.append(day)
    return out, done


def ci95(xs, iterations=2000, seed=7):
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(xs, k=len(xs))) for _ in range(iterations))
    return means[int(0.025 * iterations)], means[int(0.975 * iterations) - 1]


def describe(trades, days):
    per_day = {d: 0.0 for d in days}
    for t in trades:
        per_day[t["day"]] += t["net_pnl"]
    daily = list(per_day.values())
    if not daily:
        return {"days": 0, "n": 0}
    lo, hi = ci95(daily)
    return {"days": len(days), "n": len(trades), "traded_days": sum(1 for d in days if any(t["day"] == d for t in trades)),
            "win_rate": round(sum(t["net_pnl"] > 0 for t in trades) / len(trades) * 100, 1) if trades else None,
            "net_points": round(statistics.fmean(t["net_pnl"] / POINT_VALUE for t in trades), 2) if trades else None,
            "daily_mean": round(statistics.fmean(daily), 0), "daily_ci": [round(lo, 0), round(hi, 0)],
            "total_net": round(sum(daily), 0), "passed": statistics.fmean(daily) > 0 and lo > 0,
            "cumulative": [round(sum(daily[:k + 1]), 0) for k in range(len(daily))], "day_list": list(per_day)}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--raw", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    raw, out = Path(args.raw), Path(args.out)
    b5, b30, b60 = bars(raw / "twii_5m.json"), bars(raw / "twii_30m.json"), bars(raw / "twii_60m.json")
    closes = daily_closes(raw / "twii_1d.json")
    in_sel = lambda d: SELECTION[0] <= d <= SELECTION[1]  # noqa: E731
    results, trades_all = {}, []

    for tag, keep in (("oos", lambda d: not in_sel(d)), ("insample", in_sel)):
        days5 = [d for d in b5 if keep(d)]
        orb = [t for d in days5 for t in orb_long(d, b5[d])]
        n30, d30 = run_noise(b30, closes, ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30"],
                             {d for d in b30 if keep(d)})
        n60, d60 = run_noise(b60, closes, ["09:00", "10:00", "11:00", "12:00"], {d for d in b60 if keep(d)})
        both_days = sorted(set(days5) & set(d30))
        combo = [t for t in orb + n30 if t["day"] in set(both_days)]
        results[tag] = {"ORB_LONG_5m": describe(orb, days5), "NOISE_SHORT_30m": describe(n30, d30),
                        "NOISE_SHORT_60m": describe(n60, d60), "COMBO_5m+30m": describe(combo, both_days)}
        for name, rows in (("ORB_LONG_5m", orb), ("NOISE_SHORT_30m", n30), ("NOISE_SHORT_60m", n60)):
            trades_all += [{**t, "sample": tag, "series": name} for t in rows]
    # 60 分 K 樣本外分年度
    yearly = defaultdict(list)
    for t in trades_all:
        if t["sample"] == "oos" and t["series"] == "NOISE_SHORT_60m":
            yearly[t["day"][:4]].append(t["net_pnl"])
    results["noise60_yearly"] = {y: {"n": len(v), "total": round(sum(v), 0), "win_rate": round(sum(x > 0 for x in v) / len(v) * 100, 1)}
                                 for y, v in sorted(yearly.items())}
    (out / "result.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "trades.csv").open("w", encoding="utf-8") as handle:
        keys = ["sample", "series", "day", "sleeve", "side", "entry", "exit", "reason", "points", "net_pnl", "entry_label", "exit_label"]
        handle.write(",".join(keys) + "\n")
        for t in trades_all:
            handle.write(",".join(str(t.get(k, "")) for k in keys) + "\n")

    lines = ["# 組合「ORB 只做多 + 噪音區只放空」：樣本外驗證結果", "",
             "照 PREREGISTRATION.md 一次跑完。加權指數現貨當代理，成本照小台 1 口。", "",
             "| 樣本 | 子策略 | 天數 | 筆數 | 勝率 | 每筆淨點數 | 每日平均淨損益 | 以天 95% CI | 合計淨損益 | 及格 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for tag, label in (("oos", "樣本外"), ("insample", "挑選期（只看代理是否像小台）")):
        for name, s in results[tag].items():
            if not s.get("days"):
                continue
            lines.append(f"| {label} | {name} | {s['days']} | {s['n']} | {s['win_rate'] if s['win_rate'] is not None else '—'}% | "
                         f"{s['net_points'] if s['net_points'] is not None else '—'} | {s['daily_mean']:+,.0f} | "
                         f"{s['daily_ci'][0]:+,.0f} ～ {s['daily_ci'][1]:+,.0f} | {s['total_net']:+,.0f} | {'✅' if s['passed'] else '❌'} |")
    lines += ["", "噪音區只放空（60 分 K）樣本外分年度：" +
              "；".join(f"{y} {v['n']} 筆 勝率 {v['win_rate']}% 合計 {v['total']:+,.0f}" for y, v in results["noise60_yearly"].items()), ""]
    (out / "RESULT.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
