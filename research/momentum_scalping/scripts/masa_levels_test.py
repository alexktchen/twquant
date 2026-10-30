#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""masa_levels_test.py：撐壓與軌道線對照隨機假線（studies/masa_levels/PREREGISTRATION.md）。只跑一次，不調參數。

用法：python masa_levels_test.py --fubon <fubon_1m> --out <studies/masa_levels>
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import intraday_bidir_test as B  # noqa: E402
import minute_bidir_test as M  # noqa: E402

START, END = "2017-05-22", "2026-09-08"
STEP, SWING, MAX_ZONES, HOLD_BARS, N_FAKE = 5, 2, 3, 6, 5
EVENT_UNTIL, ENTRY_UNTIL, TIME_EXIT = 13 * 60, 10 * 60, 11 * 60
THIRD_TOL, LINE_HALF, MIN_HALF, FAKE_GAP, FAKE_SPREAD, RR = 0.2, 0.1, 0.1, 0.5, 0.3, 2.0
SEED, ITER, MIN_T, TOP_SHARE = 7, 2000, 3.0, 0.5


@dataclass
class Level:
    """價位 = base + slope × (K 棒序號 − i0)；水平線 slope=0。"""
    kind: str
    base: float
    slope: float
    i0: int
    half: float

    def at(self, i: int) -> float:
        return self.base + self.slope * (i - self.i0)


def bars5(d):
    """[(開始分鐘, 開, 高, 低, 收, 量)]"""
    groups = defaultdict(list)
    for i, minute in enumerate(d.m):
        groups[(minute - M.OPEN_MIN) // STEP].append(i)
    out = []
    for k in sorted(groups):
        idx = groups[k]
        out.append((M.OPEN_MIN + k * STEP, d.o[idx[0]], max(d.h[i] for i in idx), min(d.l[i] for i in idx), d.c[idx[-1]], sum(d.v[i] for i in idx)))
    return out


def zones(prev, atr):
    cand = []
    for a, b in zip(prev, prev[1:]):
        if (a[4] - a[1]) * (b[4] - b[1]) >= 0:
            continue
        lo, hi = max(min(a[1], a[4]), min(b[1], b[4])), min(max(a[1], a[4]), max(b[1], b[4]))
        if hi < lo:
            continue
        cand.append((a[5] + b[5], (lo + hi) / 2, max((hi - lo) / 2, MIN_HALF * atr)))
    out = []
    for _, c, h in sorted(cand, reverse=True):
        if all(abs(c - x.base) > h + x.half for x in out):
            out.append(Level("zone", c, 0.0, 0, h))
        if len(out) == MAX_ZONES:
            break
    return out


def swings(prev):
    hi, lo = [], []
    for i in range(SWING, len(prev) - SWING):
        side = [*range(i - SWING, i), *range(i + 1, i + SWING + 1)]
        if all(prev[i][2] > prev[j][2] for j in side):
            hi.append((i, prev[i][2]))
        if all(prev[i][3] < prev[j][3] for j in side):
            lo.append((i, prev[i][3]))
    return hi, lo


def lines(prev, atr):
    hi, lo = swings(prev)
    out = []
    for pts, falling in ((hi, True), (lo, False)):
        if len(pts) < 3:
            continue
        (i1, p1), (i2, p2) = pts[-2:]
        if (falling and p2 >= p1) or (not falling and p2 <= p1):
            continue
        slope = (p2 - p1) / (i2 - i1)
        if any(abs(p1 + slope * (i - i1) - p) <= THIRD_TOL * atr for i, p in pts[:-2]):
            out.append(Level("line", p1, slope, i1, LINE_HALF * atr))
    return out


def fakes(real, open_, n0, atr, rng):
    out = []
    for lv in real:
        made, tries = 0, 0
        center = lv.at(n0)
        dist = abs(center - open_)
        while made < N_FAKE and tries < 100:
            tries += 1
            c = open_ + rng.choice((-1, 1)) * dist * (1 + rng.uniform(-FAKE_SPREAD, FAKE_SPREAD))
            if all(abs(c - r.at(n0)) >= FAKE_GAP * atr for r in real):
                out.append(Level(lv.kind, lv.base + (c - center), lv.slope, lv.i0, lv.half))
                made += 1
    return out


def event(lv, today, n0):
    """回 (事件序號, 方向, 守住, 收回)；沒碰到回 None。"""
    op = today[0][1]
    c0 = lv.at(n0)
    if abs(op - c0) <= lv.half:
        return None
    side = 1 if op > c0 else -1
    for k, bar in enumerate(today):
        if bar[0] >= EVENT_UNTIL:
            return None
        c = lv.at(n0 + k)
        near = c + side * lv.half
        touched = bar[3] <= near if side > 0 else bar[2] >= near
        if not touched:
            continue
        far = c - side * lv.half
        after = today[k + 1 : k + 1 + HOLD_BARS]
        if len(after) < HOLD_BARS:
            return None
        held = all((x[4] >= far) if side > 0 else (x[4] <= far) for x in after)
        back = bar[4] > near if side > 0 else bar[4] < near
        return k, side, held, back
    return None


def trade_from(d, today, k, side):
    """訊號 K 收完後下一根開盤進場；停損事件 K 極值，2R 停利，11:00 時間出場。"""
    if k + 1 >= len(today):
        return None
    start = today[k + 1][0]
    if start >= ENTRY_UNTIL:
        return None
    et, ep, _ = d.fill(start, side)
    stop = today[k][3] if side > 0 else today[k][2]
    risk = (ep - stop) * side
    if risk <= 0:
        return None
    target = ep + side * RR * risk
    for i, minute in enumerate(d.m):
        if minute < et:
            continue
        if minute >= TIME_EXIT or minute >= d.eod:
            xt, xp, _ = d.fill(minute, -side)
            return M.trade(d, "LEVEL", side, start, (et, ep), (xt, xp), "TIME")
        if (d.l[i] <= stop) if side > 0 else (d.h[i] >= stop):
            return M.trade(d, "LEVEL", side, start, (et, ep), (minute, stop - side * B.SLIP), "STOP")
        if (d.h[i] >= target) if side > 0 else (d.l[i] <= target):
            return M.trade(d, "LEVEL", side, start, (et, ep), (minute, target), "TARGET")
    return None


def day_trades(d, today, events):
    """同時最多 1 口、先到先做。events=[(k, side)]"""
    out, busy_until = [], -1
    for k, side in sorted(events):
        if today[k][0] <= busy_until:
            continue
        t = trade_from(d, today, k, side)
        if t is not None:
            out.append(t)
            h, m = t["exit_time"].split(":")
            busy_until = int(h) * 60 + int(m)
    return out


def boot(xs):
    rng = random.Random(SEED)
    m = sorted(statistics.fmean(rng.choices(xs, k=len(xs))) for _ in range(ITER))
    return m[int(0.025 * ITER)], m[int(0.975 * ITER) - 1]


def tstat(xs):
    sd = statistics.stdev(xs)
    return statistics.fmean(xs) / (sd / math.sqrt(len(xs))) if sd else 0.0


def pnl_stats(trades, names):
    daily = defaultdict(float)
    for t in trades:
        daily[t["day"]] += t["net_pnl"]
    xs = [daily.get(n, 0.0) for n in names]
    lo, hi = boot(xs)
    no26 = sum(v for k, v in daily.items() if k[:4] != "2026")
    total = sum(xs)
    top5 = sum(sorted(xs, reverse=True)[:5])
    years = defaultdict(float)
    for k, v in daily.items():
        years[k[:4]] += v
    n = len(trades)
    return {"trades": n, "win_rate": round(sum(t["net_pnl"] > 0 for t in trades) / max(1, n) * 100, 1), "avg_trade": round(sum(t["net_pnl"] for t in trades) / max(1, n), 1),
            "mean_day": round(statistics.fmean(xs), 1), "ci_low": round(lo, 1), "ci_high": round(hi, 1), "t": round(tstat(xs), 2), "total": round(total, 0), "no_2026": round(no26, 0),
            "top5": round(top5, 0), "yearly": {k: round(v, 0) for k, v in sorted(years.items())}, "daily": xs}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--fubon", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    raw = M.load_bars(Path(a.fubon))
    kind = M.average_kind(raw, sorted(raw)[:30])
    days, _ = M.build_days(raw, kind)
    rng = random.Random(SEED)
    hold = {k: {"real": [], "fake": []} for k in ("zone", "line")}
    back_split = {True: [], False: []}
    ev = {("zone", "real"): [], ("zone", "fake"): [], ("line", "real"): [], ("line", "fake"): []}
    trades = {k: [] for k in ev}
    names, trend = [], []
    for prev, d in zip(days, days[1:]):
        if not START <= d.day <= END:
            continue
        pb = [b for b in bars5(prev)]
        same = M.MinuteDay(prev.day, d.contract, raw[prev.day][d.contract], None, kind) if d.contract in raw[prev.day] else None
        if same is None:
            continue
        pb = bars5(same)
        today = bars5(d)
        if len(pb) < 40 or len(today) < 40:
            continue
        names.append(d.day)
        rng_o = (today[0][1], max(b[2] for b in today), min(b[3] for b in today), today[-1][4])
        trend.append(abs(rng_o[3] - rng_o[0]) / max(1e-9, rng_o[1] - rng_o[2]) >= 0.5)
        atr = statistics.fmean(b[2] - b[3] for b in pb)
        n0, op = len(pb), today[0][1]
        for group, real in (("zone", zones(pb, atr)), ("line", lines(pb, atr))):
            for tag, levels in (("real", real), ("fake", fakes(real, op, n0, atr, rng))):
                sig = []
                for lv in levels:
                    e = event(lv, today, n0)
                    if e is None:
                        continue
                    k, side, held, back = e
                    hold[group][tag].append((d.day, held))
                    if group == "zone" and tag == "real":
                        back_split[back].append(held)
                    if back:
                        sig.append((k, side))
                ev[(group, tag)].append(len(sig))
                trades[(group, tag)] += day_trades(d, today, sig)

    def rate(rows):
        by = defaultdict(list)
        for day, h in rows:
            by[day].append(h)
        return by

    info = {}
    for group in ("zone", "line"):
        r, f = rate(hold[group]["real"]), rate(hold[group]["fake"])
        pair = [statistics.fmean(r[x]) - statistics.fmean(f[x]) for x in sorted(set(r) & set(f))]
        lo, hi = boot(pair)
        info[group] = {"real_events": len(hold[group]["real"]), "fake_events": len(hold[group]["fake"]), "real_hold_pct": round(statistics.fmean(h for _, h in hold[group]["real"]) * 100, 1),
                       "fake_hold_pct": round(statistics.fmean(h for _, h in hold[group]["fake"]) * 100, 1), "diff_pct": round(statistics.fmean(pair) * 100, 2),
                       "ci_low": round(lo * 100, 2), "ci_high": round(hi * 100, 2), "t": round(tstat(pair), 2), "paired_days": len(pair)}
    res = {k: pnl_stats(v, names) for k, v in (("zone_real", trades[("zone", "real")]), ("zone_fake", trades[("zone", "fake")]), ("line_real", trades[("line", "real")]), ("line_fake", trades[("line", "fake")]),
                                               ("zone_long", [t for t in trades[("zone", "real")] if t["side"] > 0]), ("zone_short", [t for t in trades[("zone", "real")] if t["side"] < 0]))}
    # 假線一天有 5 倍的條數，比「每筆平均」；配對差＝真線每日損益 − 假線每日損益 ÷ 訊號筆數比無法直接比，所以用每日每筆平均的配對差
    def per_trade_day(tr):
        by = defaultdict(list)
        for t in tr:
            by[t["day"]].append(t["net_pnl"])
        return {k: statistics.fmean(v) for k, v in by.items()}
    rd, fd = per_trade_day(trades[("zone", "real")]), per_trade_day(trades[("zone", "fake")])
    diff = [rd[x] - fd[x] for x in sorted(set(rd) & set(fd))]
    dlo, dhi = boot(diff)
    s = res["zone_real"]
    checks = {"ci_low_above_0": s["ci_low"] > 0, "without_2026_above_0": s["no_2026"] > 0, "top5_share_below_50pct": s["total"] > 0 and s["top5"] / s["total"] < TOP_SHARE,
              "t_at_least_3": s["t"] >= MIN_T, "beats_fake_lines": dlo > 0}
    passed = all(checks.values())
    out = {"days": len(names), "sample": [names[0], names[-1]], "trend_day_pct": round(statistics.fmean(trend) * 100, 1), "info": info, "checks": checks, "passed": passed,
           "real_minus_fake_per_trade": {"mean": round(statistics.fmean(diff), 1), "ci_low": round(dlo, 1), "ci_high": round(dhi, 1), "t": round(tstat(diff), 2), "days": len(diff)},
           "hold_if_back_pct": round(statistics.fmean(back_split[True]) * 100, 1), "hold_if_not_back_pct": round(statistics.fmean(back_split[False]) * 100, 1),
           "pnl": {k: {x: y for x, y in v.items() if x != "daily"} for k, v in res.items()}}
    dest = Path(a.out)
    (dest / "result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
