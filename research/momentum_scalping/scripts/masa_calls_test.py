#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""masa_calls_test.py：麻紗事前說法對照行情（studies/masa_calls/PREREGISTRATION.md）。只跑一次。

用法：python masa_calls_test.py --fubon <fubon_1m> --calls <calls.json> --sample <sample.json> --out <studies/masa_calls>
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import masa_levels_test as L  # noqa: E402
import minute_bidir_test as M  # noqa: E402

TPE = timezone(timedelta(hours=8))
CUTOFF, UNTIL, AFTER, N_FAKE, GAP, SPREAD = 13 * 60 + 45, 13 * 60, 6, 5, 0.5, 0.3
SEED, ITER, END = 7, 2000, "2026-09-08"


def target_day(ts, names):
    t = datetime.fromtimestamp(ts, TPE)
    day = t.date().isoformat()
    after = t.hour * 60 + t.minute >= CUTOFF
    for n in names:
        if n > day or (n == day and not after):
            return n
    return None


def trigger(bars, price, sign):
    """第一根收盤穿過該價且方向符合的 5 分 K；回 6 根後的順向點數。"""
    for k, b in enumerate(bars):
        if b[0] >= UNTIL:
            return None
        if (b[4] > price) if sign > 0 else (b[4] < price):
            if k + AFTER >= len(bars):
                return None
            return (bars[k + AFTER][4] - b[4]) * sign
    return None


def fake_prices(price, prev, atr, rng):
    dist, out, tries = abs(price - prev), [], 0
    side = 1 if price >= prev else -1
    while len(out) < N_FAKE and tries < 200:
        tries += 1
        c = prev + side * dist * (1 + rng.uniform(-SPREAD, SPREAD))
        if abs(c - price) >= GAP * atr:
            out.append(c)
    return out


def boot(xs):
    rng = random.Random(SEED)
    m = sorted(statistics.fmean(rng.choices(xs, k=len(xs))) for _ in range(ITER))
    return m[int(0.025 * ITER)], m[int(0.975 * ITER) - 1]


def main() -> int:
    p = argparse.ArgumentParser()
    for name in ("fubon", "calls", "sample", "out"):
        p.add_argument(f"--{name}", required=True)
    a = p.parse_args()
    calls = json.load(open(a.calls, encoding="utf-8"))
    ts_of = {x["id"]: x.get("ts") for x in json.load(open(a.sample, encoding="utf-8"))}
    raw = M.load_bars(Path(a.fubon))
    kind = M.average_kind(raw, sorted(raw)[:30])
    days, _ = M.build_days(raw, kind)
    by = {d.day: d for d in days}
    names = sorted(by)
    rng = random.Random(SEED)
    per_video, rows, skip, dir_rows = {}, [], {}, []
    index_calls = 0
    for v in calls:
        vid, ts = v["id"], ts_of.get(v["id"])
        if v.get("status") != "ok" or not ts:
            skip[vid] = v.get("status") if ts else "no_timestamp"
            continue
        day = target_day(ts, names)
        if day is None or day > END or by[day].prev_close is None:
            skip[vid] = "no_session"
            continue
        d = by[day]
        k = names.index(day)
        prev_d = by[names[k - 1]]
        today = L.bars5(d)
        atr = statistics.fmean(b[2] - b[3] for b in L.bars5(prev_d))
        dc = v.get("direction") or {}
        if dc.get("call") in ("long", "short"):
            move = today[-1][4] - d.prev_close
            dir_rows.append({"id": vid, "day": day, "call": dc["call"], "up": move > 0, "hit": (move > 0) == (dc["call"] == "long")})
        diffs = []
        for c in v.get("calls") or []:
            if c.get("instrument") == "index":
                index_calls += 1
                continue
            if c.get("instrument") != "futures" or not c.get("price"):
                continue
            sign = 1 if c.get("dir") == "up" else -1
            price = float(c["price"])
            if abs(price / d.prev_close - 1) > 0.1:  # 超過一天漲跌幅上限：價位摘錄有誤，不計分
                skip[f"{vid}:{price}"] = "implausible_price"
                continue
            real = trigger(today, price, sign)
            fk = [trigger(today, x, sign) for x in fake_prices(price, d.prev_close, atr, rng)]
            fk = [x for x in fk if x is not None]
            rows.append({"id": vid, "day": day, "price": price, "dir": sign, "real": real, "fake": statistics.fmean(fk) if fk else None})
            if real is not None and fk:
                diffs.append(real - statistics.fmean(fk))
        if diffs:
            per_video[vid] = statistics.fmean(diffs)
    scored = [r for r in rows if r["real"] is not None]
    out = {"videos_total": len(calls), "videos_scored": len(per_video), "futures_calls": len(rows), "triggered": len(scored), "index_calls_unscored": index_calls,
           "skipped": skip, "direction": None, "primary": None}
    if dir_rows:
        out["direction"] = {"n": len(dir_rows), "hit_pct": round(statistics.fmean(r["hit"] for r in dir_rows) * 100, 1), "up_share_pct": round(statistics.fmean(r["up"] for r in dir_rows) * 100, 1),
                            "long_calls": sum(r["call"] == "long" for r in dir_rows)}
    if len(per_video) >= 3:
        xs = list(per_video.values())
        lo, hi = boot(xs)
        pos = statistics.fmean(r["real"] > 0 for r in scored)
        out["primary"] = {"real_mean_pts": round(statistics.fmean(r["real"] for r in scored), 2), "fake_mean_pts": round(statistics.fmean(r["fake"] for r in scored if r["fake"] is not None), 2),
                          "diff_mean_pts": round(statistics.fmean(xs), 2), "ci_low": round(lo, 2), "ci_high": round(hi, 2), "real_positive_pct": round(pos * 100, 1),
                          "long": len([r for r in scored if r["dir"] > 0]), "short": len([r for r in scored if r["dir"] < 0]), "passed": lo > 0 and pos > 0.5}
    Path(a.out, "result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "skipped"}, ensure_ascii=False, indent=0))
    print("skipped", len(skip))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
