#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ms_minute_test.py — studies/ms_tmf：網友原版 Momentum_Scalping 用富邦 1 分 K 重測（照 PREREGISTRATION.md）

引擎完全沿用 futures_replay.simulate_day（原版 Intraday／Trading，預設參數、大盤濾網全開）；
這裡只做兩件事：把 1 分 K 還原成逐筆、把每天的結果彙總成判定。

用法：
  保真度：python ms_minute_test.py --mode fidelity --product TMF --repo C:/t/Momentum_Scalping \
            --bars D:/.../fubon_1m --ticks D:/.../taifex_ticks/TMF --out D:/.../ms_tmf
  判定：  python ms_minute_test.py --mode oos --product TMF --repo ... --bars ... --out D:/.../ms_tmf
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import futures_replay as FR  # noqa: E402
import intraday_bidir_test as B  # noqa: E402
import minute_bidir_test as MB  # noqa: E402
import ms_replay as M  # noqa: E402

# 商品：富邦代號 → (期交所代號, 每點元, 每邊手續費)
PRODUCTS = {"TMF": ("TMF", 10, 15.0), "MXF": ("MTX", 50, 30.0)}
MIN_BARS = MB.MIN_BARS
PATH_SECONDS = (0, 20, 40, 59)      # 開 → 極值 → 極值 → 收
STEP_SECONDS = 2                    # 每 2 秒內插一筆
FIDELITY = {"TMF": ("2026-09-09", "2026-10-27"), "MXF": ("2026-09-09", "2026-10-22")}
FID_COUNT, FID_CORR, FID_POINTS = 0.25, 0.7, 1.5
JUDGE = {"TMF": ("2024-07-29", "2026-09-08"), "MXF": ("2017-05-22", "2026-09-08")}
EARLY_END = {"TMF": "2025-12-31", "MXF": "2025-12-31"}
SEEN = ("2026-09-09", "2026-10-22")
TOP_DAYS = 5


# --------------------------------------------------------------------------- 資料
def load_minute(folder: Path, product: str):
    """{day: {contract: {minute: (open, high, low, close, volume)}}}，只留 08:45～13:45 的月合約。"""
    out = defaultdict(lambda: defaultdict(dict))
    files = sorted(folder.glob(f"{product}_*.json"))
    if not files:
        raise SystemExit(f"{folder} 底下沒有 {product}_*.json")
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        default_contract = str(payload.get("contractMonth") or "")
        for c in payload.get("data") or []:
            ts = datetime.fromisoformat(c["date"])
            if ts.tzinfo is None:
                raise ValueError(f"{path.name} 的時間沒有時區：{c['date']}")
            ts = ts.astimezone(MB.TPE)
            minute = ts.hour * 60 + ts.minute
            contract = str(c.get("contractMonth") or default_contract)
            if not (len(contract) == 6 and contract.isdigit()) or not MB.OPEN_MIN <= minute <= MB.CLOSE_MIN:
                continue
            bar = (float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"]), int(float(c.get("volume") or 0)))
            day = ts.date().isoformat()
            old = out[day][contract].get(minute)
            if old is not None and old != bar:
                raise ValueError(f"{day} {contract} {MB.hhmm(minute)} 兩個檔案的 K 不一樣：{old} vs {bar}")
            out[day][contract][minute] = bar
    return out


def main_contract_days(raw):
    """主力合約與排除的日子。"""
    days, excluded = {}, {}
    for day in sorted(raw):
        contract = max(raw[day], key=lambda c: sum(b[4] for b in raw[day][c].values()))
        bars = raw[day][contract]
        if len(bars) < MIN_BARS:
            excluded[day] = f"主力 {contract} 只有 {len(bars)} 根 K"
            continue
        days[day] = (contract, bars)
    return days, excluded


def synth_ticks(day: str, bars) -> list:
    """1 分 K → 逐筆 [(t_us, serial, price, size)]（還原法見 PREREGISTRATION.md）。"""
    ticks, serial = [], 0
    for minute in sorted(bars):
        o, h, low, c, v = bars[minute]
        x1, x2 = (low, h) if c >= o else (h, low)
        points = list(zip(PATH_SECONDS, (o, x1, x2, c)))
        seconds = list(range(0, PATH_SECONDS[-1], STEP_SECONDS)) + [PATH_SECONDS[-1]]
        prices = []
        for s in seconds:
            for (s0, p0), (s1, p1) in zip(points, points[1:]):
                if s0 <= s <= s1:
                    prices.append(float(round(p0 + (p1 - p0) * (s - s0) / (s1 - s0))))
                    break
        n = len(prices)
        sizes = [v // n + (1 if k < v % n else 0) for k in range(n)]
        base = datetime.fromisoformat(f"{day}T{minute // 60:02d}:{minute % 60:02d}:00").replace(tzinfo=M.TPE)
        base_us = int(base.timestamp() * M.US)
        for s, price, size in zip(seconds, prices, sizes):
            ticks.append((base_us + s * M.US, serial, price, size))
            serial += 1
    return ticks


def minute_data(days, day):
    contract, bars = days[day]
    return {"contract": contract, "ticks": synth_ticks(day, bars), "closes": {}}


# --------------------------------------------------------------------------- 引擎（每個 worker 載入一次原版）
_W = {}


def _init(repo: str, product: str):
    mods = M.load_modules(repo)
    params = M.load_parameters(mods, None, [])
    code, pv, fee = PRODUCTS[product]
    cfg = FR.Config(product=code, point_value=pv, contracts=1, commission=fee, tax_rate=0.00002, slip_ticks=1,
                    tick=1.0, latency_us=500_000, eval_interval_us=1_000_000, refresh_us=30 * M.US)
    _W.update(mods=mods, params=params, cfg=cfg)


def _run(job):
    day, data = job
    result = FR.simulate_day(_W["mods"], _W["params"], day, data, {}, _W["cfg"])
    return day, result["trades"], result["signals"], result["blocked"]


def run_days(jobs, repo, product, workers):
    out = {}
    signals, blocked = Counter(), Counter()
    started = time.time()
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(repo, product)) as pool:
        for k, (day, trades, sig, blk) in enumerate(pool.map(_run, jobs, chunksize=1), 1):
            out[day] = trades
            signals.update(sig)
            blocked.update(blk)
            if k % 25 == 0 or k == len(jobs):
                print(f"  {k}/{len(jobs)} 天（{time.time() - started:,.0f} 秒）", flush=True)
    return out, signals, blocked


# --------------------------------------------------------------------------- 統計
def net_points(t, pv):
    return t["net_pnl"] / pv / t["quantity"]


def daily(by_day, days):
    return {d: sum(t["net_pnl"] for t in by_day.get(d, [])) for d in days}


def summary(by_day, days, pv):
    per_day = daily(by_day, days)
    vals = [per_day[d] for d in days]
    trades = [t for d in days for t in by_day.get(d, [])]
    lo, hi = B.ci95(vals)
    top = sorted(vals, reverse=True)[:TOP_DAYS]
    out = {"days": len(days), "trades": len(trades),
           "win_rate_pct": round(100 * sum(t["net_pnl"] > 0 for t in trades) / len(trades), 1) if trades else None,
           "net_points_per_trade": round(statistics.fmean(net_points(t, pv) for t in trades), 2) if trades else None,
           "daily_mean": round(statistics.fmean(vals), 0), "daily_ci": [round(lo, 0), round(hi, 0)],
           "total": round(sum(vals), 0), "top5_total": round(sum(top), 0), "rest_total": round(sum(vals) - sum(top), 0)}
    return out


def yearly(by_day, days):
    per_day = daily(by_day, days)
    out = {}
    for year in sorted({d[:4] for d in days}):
        ds = [d for d in days if d[:4] == year]
        vals = [per_day[d] for d in ds]
        out[year] = {"days": len(ds), "trades": sum(len(by_day.get(d, [])) for d in ds),
                     "daily_mean": round(statistics.fmean(vals), 0), "total": round(sum(vals), 0)}
    return out


def grouped(by_day, days, key, pv):
    groups = defaultdict(list)
    for d in days:
        for t in by_day.get(d, []):
            groups[t[key]].append(t)
    return {k: {"trades": len(v), "win_rate_pct": round(100 * sum(t["net_pnl"] > 0 for t in v) / len(v), 1),
                "net_points_per_trade": round(statistics.fmean(net_points(t, pv) for t in v), 2),
                "total": round(sum(t["net_pnl"] for t in v), 0)}
            for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))}


def corr(xs, ys):
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy) if sx and sy else float("nan")


# --------------------------------------------------------------------------- 模式
def fidelity(args, out: Path):
    code, pv, _ = PRODUCTS[args.product]
    start, end = FIDELITY[args.product]
    tick_data = FR.read_taifex(Path(args.ticks), code)
    days, excluded = main_contract_days(load_minute(Path(args.bars), args.product))
    test = [d for d in sorted(tick_data) if start <= d <= end and d in days]
    if not test:
        raise SystemExit("保真度期間沒有逐筆和 1 分 K 都有的日子")
    print(f"保真度：{args.product} {test[0]}～{test[-1]} 共 {len(test)} 天", flush=True)
    print("逐筆版：", flush=True)
    tick_res, tick_sig, _ = run_days([(d, tick_data[d]) for d in test], args.repo, args.product, args.workers)
    print("1 分 K 版：", flush=True)
    min_res, min_sig, _ = run_days([(d, minute_data(days, d)) for d in test], args.repo, args.product, args.workers)
    ts, ms = summary(tick_res, test, pv), summary(min_res, test, pv)
    dt, dm = daily(tick_res, test), daily(min_res, test)
    count_diff = abs(ms["trades"] - ts["trades"]) / ts["trades"] if ts["trades"] else float("inf")
    c = corr([dt[d] for d in test], [dm[d] for d in test])
    pts_diff = abs((ms["net_points_per_trade"] or 0) - (ts["net_points_per_trade"] or 0))
    checks = {"count_diff": round(count_diff, 4), "count_ok": count_diff <= FID_COUNT,
              "daily_corr": round(c, 4), "corr_ok": c >= FID_CORR,
              "points_diff": round(pts_diff, 2), "points_ok": pts_diff <= FID_POINTS}
    checks["passed"] = checks["count_ok"] and checks["corr_ok"] and checks["points_ok"]
    report = {"product": args.product, "days": test, "tick": ts, "minute": ms, "checks": checks,
              "per_day": {d: {"tick": round(dt[d], 0), "minute": round(dm[d], 0),
                              "tick_n": len(tick_res.get(d, [])), "minute_n": len(min_res.get(d, []))} for d in test},
              "tick_signals": dict(tick_sig), "minute_signals": dict(min_sig)}
    (out / f"fidelity_{args.product}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    M.write_csv(out / f"fidelity_{args.product}_tick_trades.csv", [t for d in test for t in tick_res.get(d, [])])
    M.write_csv(out / f"fidelity_{args.product}_minute_trades.csv", [t for d in test for t in min_res.get(d, [])])
    print(json.dumps({"tick": ts, "minute": ms, "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if checks["passed"] else 2


def oos(args, out: Path):
    _, pv, _ = PRODUCTS[args.product]
    fid = out / f"fidelity_{args.product}.json"
    if not fid.exists():
        raise SystemExit(f"{args.product} 保真度還沒跑")
    fid_checks = json.loads(fid.read_text(encoding="utf-8"))["checks"]
    if not fid_checks["passed"] and not args.override_fidelity:
        raise SystemExit(f"{args.product} 保真度沒過，照事前登記不跑判定（要照跑請給 --override-fidelity 和理由）")
    start, end = JUDGE[args.product]
    days, excluded = main_contract_days(load_minute(Path(args.bars), args.product))
    judged = [d for d in sorted(days) if start <= d <= end and not SEEN[0] <= d <= SEEN[1]]
    after = [d for d in sorted(days) if d > SEEN[1]]
    run = judged + after
    print(f"判定：{args.product} {judged[0]}～{judged[-1]} 共 {len(judged)} 天；之後 {len(after)} 天", flush=True)
    res, signals, blocked = run_days([(d, minute_data(days, d)) for d in run], args.repo, args.product, args.workers)
    early = [d for d in judged if d <= EARLY_END[args.product]]
    main = summary(res, judged, pv)
    early_s = summary(res, early, pv)
    passed = main["daily_ci"][0] > 0 and early_s["daily_mean"] > 0
    result = {"fidelity_passed": fid_checks["passed"], "fidelity_checks": fid_checks, "override_fidelity": args.override_fidelity,
              "product": args.product, "judged_first": judged[0], "judged_last": judged[-1],
              "excluded": {d: r for d, r in excluded.items() if start <= d}, "main": main, "early": early_s,
              "early_range": [early[0], early[-1]] if early else None, "passed": passed,
              "yearly": yearly(res, judged), "by_strategy": grouped(res, judged, "strategy_setup", pv),
              "by_exit": grouped(res, judged, "final_exit_reason", pv),
              "after": summary(res, after, pv) if after else None, "after_days": after,
              "signals": dict(signals), "blocked": dict(blocked.most_common(30))}
    (out / f"result_{args.product}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    M.write_csv(out / f"trades_{args.product}.csv", [t for d in run for t in res.get(d, [])])
    print(json.dumps({k: result[k] for k in ("main", "early", "passed", "yearly")}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["fidelity", "oos"], required=True)
    p.add_argument("--product", choices=sorted(PRODUCTS), required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--bars", required=True)
    p.add_argument("--ticks", help="期交所逐筆 Daily_*.csv 資料夾（保真度用）")
    p.add_argument("--out", required=True)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--dates", nargs="*", help="只跑這幾天（除錯用，不寫判定）")
    p.add_argument("--override-fidelity", help="保真度沒過仍照跑：寫明誰、何時、為什麼要求（會寫進結果檔）")
    args = p.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.dates:
        days, _ = main_contract_days(load_minute(Path(args.bars), args.product))
        res, sig, blk = run_days([(d, minute_data(days, d)) for d in args.dates], args.repo, args.product, 1)
        for d in args.dates:
            print(d, len(res[d]), round(sum(t["net_pnl"] for t in res[d])))
        print(dict(sig))
        return 0
    if args.mode == "fidelity":
        if not args.ticks:
            raise SystemExit("保真度要 --ticks")
        return fidelity(args, out)
    return oos(args, out)


if __name__ == "__main__":
    raise SystemExit(main())
