#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
intraday_bidir_test.py — 小台指期日內多空雙向：三條文獻策略（照 intraday_bidir/PREREGISTRATION.md）

ORB5   Zarattini & Aziz (2023)：開盤第一根 5 分 K 決定方向，停損在該 K 另一端，停利 10R，否則收盤平倉
VWAP   Zarattini & Aziz (2023)：每分鐘收盤在 VWAP 之上持多、之下持空，方向變就反手
NOISE  Zarattini, Aziz & Barbon (2024)：前 14 天同時點平均波動畫出噪音區，每 30 分鐘檢查，
       突破上緣且在 VWAP 之上持多、跌破下緣且在 VWAP 之下持空，否則空手

用法：python intraday_bidir_test.py --taifex taifex_compact --out intraday_bidir
"""
from __future__ import annotations

import argparse
import bisect
import json
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import futures_replay as F  # noqa: E402
import ms_replay as M  # noqa: E402

POINT_VALUE, COMMISSION, TAX, SLIP, LATENCY_US = 50, 30.0, 0.00002, 1.0, 500_000
SESSION_OPEN, ORB_MINUTES, EOD = "08:45", 5, "13:44"
ORB_TARGET_R, NOISE_LOOKBACK, NOISE_STEP = 10, 14, 30
NOISE_CHECKS = list(range(NOISE_STEP, 271, NOISE_STEP))  # 開盤後第 30…270 分鐘＝09:15…13:15
RULES = {"ORB5": "開盤 5 分鐘方向（ORB）", "VWAP": "VWAP 順勢反手", "NOISE": "噪音區突破"}
SIDE = {1: "多", -1: "空"}


class Day:
    """一天的主力合約逐筆：時間、價格、累計 VWAP；所有查價都只看 t 之前的成交。"""

    def __init__(self, day, info, prev_close):
        self.day, self.contract, self.prev_close = day, info["contract"], prev_close
        self.t = [x[0] for x in info["ticks"]]
        self.p = [x[2] for x in info["ticks"]]
        self.vwap, pv, vol = [], 0.0, 0.0
        for _, _, price, size in info["ticks"]:
            pv += price * size
            vol += size
            self.vwap.append(pv / vol)
        self.open = self.p[0]
        # 結算日近月合約 13:30 就停止交易：收盤前 1 分鐘平倉（平常 13:44，結算日 13:29）
        last_minute = M.us_to_dt(self.t[-1]).replace(second=0, microsecond=0)
        self.eod = min(self.clock(EOD), int(last_minute.timestamp() * M.US))

    def at(self, minutes_after_open: float) -> int:
        base = datetime.fromisoformat(f"{self.day}T{SESSION_OPEN}:00").replace(tzinfo=M.TPE)
        return int((base + timedelta(minutes=minutes_after_open)).timestamp() * M.US)

    def clock(self, hhmm: str) -> int:
        return int(datetime.fromisoformat(f"{self.day}T{hhmm}:00").replace(tzinfo=M.TPE).timestamp() * M.US)

    def last_before(self, t_us: int) -> int:
        i = bisect.bisect_left(self.t, t_us) - 1
        if i < 0:
            raise ValueError(f"{self.day} {M.us_to_dt(t_us)} 之前沒有成交")
        return i

    def fill(self, t_us: int, side: int):
        """t_us 送出市價單：延遲後的第一筆成交價，買 +1 點、賣 −1 點。"""
        i = bisect.bisect_left(self.t, t_us + LATENCY_US)
        if i >= len(self.t):
            raise ValueError(f"{self.day} {M.us_to_dt(t_us)} 之後沒有成交可以成交")
        return self.t[i], self.p[i] + side * SLIP, i


def trade(d: Day, rule, side, signal_t, entry, exit_, reason, stop=None):
    (et, ep), (xt, xp) = entry, exit_
    points = side * (xp - ep)
    cost = 2 * COMMISSION + TAX * (ep + xp) * POINT_VALUE
    net = points * POINT_VALUE - cost
    return {"day": d.day, "rule": rule, "side": side, "contract": d.contract,
            "signal_time": M.us_to_dt(signal_t).isoformat(), "entry_time": M.us_to_dt(et).isoformat(),
            "entry_price": ep, "exit_time": M.us_to_dt(xt).isoformat(), "exit_price": xp, "exit_reason": reason,
            "stop_price": stop, "points": round(points, 2), "gross_points": round(points + 2 * SLIP, 2),
            "cost": round(cost, 1), "net_pnl": round(net, 1),
            "hold_minutes": round((xt - et) / M.US / 60, 2)}


def run_orb(d: Day):
    end = d.at(ORB_MINUTES)
    seg = d.p[0:d.last_before(end) + 1]
    side = 1 if seg[-1] > seg[0] else -1 if seg[-1] < seg[0] else 0
    if not side:
        return [], {}
    stop = min(seg) if side > 0 else max(seg)
    et, ep, ei = d.fill(end, side)
    risk = side * (ep - stop)
    if risk <= 0:
        return [], {}
    target = ep + side * ORB_TARGET_R * risk
    eod, reason, trigger = d.eod, "EOD", d.eod
    for k in range(ei + 1, len(d.t)):
        if d.t[k] >= eod:
            break
        if side * (d.p[k] - stop) <= 0:
            reason, trigger = "STOP", d.t[k]
            break
        if side * (d.p[k] - target) >= 0:
            reason, trigger = "TARGET", d.t[k]
            break
    xt, xp, _ = d.fill(trigger, -side)
    return [trade(d, "ORB5", side, end, (et, ep), (xt, xp), reason, stop)], {"stop": stop, "target": target}


def run_targets(d: Day, rule, decisions):
    """decisions = [(t_us, 目標部位 +1/0/−1)]；部位變就平倉／反手，13:44 全平。"""
    trades, pos, entry, signal_t = [], 0, None, None
    for t_us, target in decisions:
        if target == pos:
            continue
        if pos:
            xt, xp, _ = d.fill(t_us, -pos)
            trades.append(trade(d, rule, pos, signal_t, entry, (xt, xp), "FLIP" if target else "EXIT"))
        if target:
            et, ep, _ = d.fill(t_us, target)
            entry, signal_t = (et, ep), t_us
        pos = target
    if pos:
        xt, xp, _ = d.fill(d.eod, -pos)
        trades.append(trade(d, rule, pos, signal_t, entry, (xt, xp), "EOD"))
    return trades


def run_vwap(d: Day):
    decisions, pos = [], 0
    minute = 1
    while d.at(minute) < d.eod:
        t_us = d.at(minute)
        i = d.last_before(t_us)
        pos = 1 if d.p[i] > d.vwap[i] else -1 if d.p[i] < d.vwap[i] else pos
        decisions.append((t_us, pos))
        minute += 1
    return run_targets(d, "VWAP", decisions), {}


def run_noise(d: Day, history):
    sigma = {m: statistics.fmean(abs(h.p[h.last_before(h.at(m))] / h.open - 1) for h in history) for m in NOISE_CHECKS}
    up_base, down_base = max(d.open, d.prev_close), min(d.open, d.prev_close)
    decisions, bands = [], []
    for m in NOISE_CHECKS:
        t_us = d.at(m)
        if t_us >= d.eod:
            break
        i = d.last_before(t_us)
        ub, lb = up_base * (1 + sigma[m]), down_base * (1 - sigma[m])
        price, vwap = d.p[i], d.vwap[i]
        decisions.append((t_us, 1 if price > max(ub, vwap) else -1 if price < min(lb, vwap) else 0))
        bands.append({"minute": m, "ub": round(ub, 1), "lb": round(lb, 1), "sigma_pct": round(sigma[m] * 100, 4)})
    return run_targets(d, "NOISE", decisions), {"bands": bands}


def ci95(xs, iterations=2000, seed=7):
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(xs, k=len(xs))) for _ in range(iterations))
    return means[int(0.025 * iterations)], means[int(0.975 * iterations) - 1]


def describe(trades, days):
    per_day = {d: 0.0 for d in days}
    for t in trades:
        per_day[t["day"]] += t["net_pnl"]
    daily = list(per_day.values())
    lo, hi = ci95(daily)
    out = {"days": len(days), "n": len(trades), "daily_mean": round(statistics.fmean(daily), 0),
           "daily_ci": [round(lo, 0), round(hi, 0)], "total_net": round(sum(daily), 0),
           "win_days": sum(v > 0 for v in daily), "loss_days": sum(v < 0 for v in daily)}
    if trades:
        out.update({"win_rate": round(sum(t["net_pnl"] > 0 for t in trades) / len(trades) * 100, 1),
                    "net_points": round(statistics.fmean(t["net_pnl"] / POINT_VALUE for t in trades), 2),
                    "gross_points": round(statistics.fmean(t["gross_points"] for t in trades), 2),
                    "cost_points": round(statistics.fmean(t["gross_points"] - t["net_pnl"] / POINT_VALUE
                                                          for t in trades), 2),
                    "hold_minutes": round(statistics.fmean(t["hold_minutes"] for t in trades), 1)})
    out["passed"] = out["daily_mean"] > 0 and lo > 0
    out["cumulative"] = [round(sum(daily[:k + 1]), 0) for k in range(len(daily))]
    return out


def chart_rows(d: Day):
    rows, minute = [], 1
    while d.at(minute) <= d.clock("13:45"):
        i = d.last_before(d.at(minute))
        rows.append((minute, d.p[i], d.vwap[i]))
        minute += 1
    return rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--taifex", required=True)
    p.add_argument("--product", default="MTX")
    p.add_argument("--out", required=True)
    args = p.parse_args()
    out = Path(args.out)
    raw = F.read_taifex(Path(args.taifex), args.product)
    names = sorted(raw)
    days = []
    for k, name in enumerate(names):
        prev = raw[names[k - 1]]["closes"].get(raw[name]["contract"]) if k else None
        if k and prev is None:
            raise ValueError(f"{name} 主力 {raw[name]['contract']} 前一天沒有收盤價")
        days.append(Day(name, raw[name], prev))

    trades, extras = [], defaultdict(dict)
    for k, d in enumerate(days):
        for rule, runner in (("ORB5", run_orb), ("VWAP", run_vwap)):
            ts, extra = runner(d)
            trades += ts
            extras[rule][d.day] = extra
        if k >= NOISE_LOOKBACK:
            ts, extra = run_noise(d, days[k - NOISE_LOOKBACK:k])
            trades += ts
            extras["NOISE"][d.day] = extra
    tested = {"ORB5": names, "VWAP": names, "NOISE": names[NOISE_LOOKBACK:]}
    result = {"product": args.product, "first": names[0], "last": names[-1], "rules": {}}
    for rule in RULES:
        rows = [t for t in trades if t["rule"] == rule]
        result["rules"][rule] = {
            "name": RULES[rule], "days": tested[rule],
            "both": describe(rows, tested[rule]),
            "long": describe([t for t in rows if t["side"] > 0], tested[rule]),
            "short": describe([t for t in rows if t["side"] < 0], tested[rule]),
            "exit_reasons": dict(sorted({r: sum(1 for t in rows if t["exit_reason"] == r)
                                         for r in {t["exit_reason"] for t in rows}}.items())),
        }
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    M.write_csv(out / "trades.csv", trades)
    (out / "chart_data.json").write_text(json.dumps(
        {"days": {d.day: {"contract": d.contract, "open": d.open, "prev_close": d.prev_close, "rows": chart_rows(d)}
                  for d in days}, "extras": extras}, ensure_ascii=False), encoding="utf-8")

    lines = ["# 小台指期日內多空雙向：三條文獻策略結果", "",
             f"照 PREREGISTRATION.md 一次跑完。{names[0]}～{names[-1]}，小台 1 口，已扣滑價（每邊 1 點）、手續費、期交稅。", "",
             "| 策略 | 邊 | 測試天數 | 筆數 | 勝率 | 每筆毛點數 | 每筆成本點數 | 每筆淨點數 | 每日平均淨損益 | 以天 bootstrap 95% CI | 合計淨損益 | 及格 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for rule, r in result["rules"].items():
        for key, label in (("both", "多空都做"), ("long", "只做多"), ("short", "只放空")):
            s = r[key]
            if not s["n"]:
                lines.append(f"| {r['name']} | {label} | {s['days']} | 0 | — | — | — | — | 0 | — | 0 | ❌ |")
                continue
            lines.append(f"| {r['name']} | {label} | {s['days']} | {s['n']} | {s['win_rate']}% | {s['gross_points']:+.1f} | "
                         f"{s['cost_points']:.1f} | {s['net_points']:+.1f} | {s['daily_mean']:+,.0f} 元 | "
                         f"{s['daily_ci'][0]:+,.0f} ～ {s['daily_ci'][1]:+,.0f} | {s['total_net']:+,.0f} 元 | "
                         f"{'✅' if s['passed'] else '❌'} |")
    lines += ["", "出場原因：" + "；".join(f"{RULES[k]} {v['exit_reasons']}" for k, v in result["rules"].items()), ""]
    (out / "RESULT.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
