#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shadow_books.py — 小台動能順勢「影子帳」：收盤後用期交所逐筆重算三本帳，往前累積判定。

事前登記：studies/shadow_books/PREREGISTRATION.md（2026-11-11）。下面的規則與門檻不得事後修改。

    現行（對照）：只做多、原出場、每天最多 2 筆
    影子一      ：只做多、只留結構停損、每天最多 1 筆
    影子二      ：多空都做、初始停損抱到 13:00、每天最多 2 筆

已算過的日子會快取在 --out/events/，每天重跑只算新的日子。

用法：python shadow_books.py --repo C:/t/Momentum_Scalping --taifex D:/.../taifex_30d/csv --out D:/.../shadow_books
"""
from __future__ import annotations

import argparse
import csv
import multiprocessing
import random
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
import futures_replay as F  # noqa: E402
import futures_signal_dataset as D  # noqa: E402
import ms_replay as M  # noqa: E402

START = "2026-11-11"
MS_COMMIT = "1dea51b"  # 網友 Momentum_Scalping 版本（同 fubon_live.MS_COMMIT）
PRODUCT = "MTX"
PV = F.PRODUCTS[PRODUCT]
LOOKS = (40, 80)
DROP_BEST = 3
BOOT, SEED = 2000, 7
COOLDOWN_MIN, LOSS_N, LOSS_COOLDOWN_MIN = 10.0, 3, 30.0
BIG_MOVE_PCT = 1.5
LATEST_LAST_TICK = "13:25"  # 結算日近月合約 13:30 收盤
SETS = [("long", "system"), ("long", "structure"), ("long", "stop_close"), ("short", "stop_close")]
BOOKS = {
    "現行": {"sets": [("long", "system")], "n_max": 2},
    "影子一": {"sets": [("long", "structure")], "n_max": 1},
    "影子二": {"sets": [("long", "stop_close"), ("short", "stop_close")], "n_max": 2},
}
SHADOWS = ("影子一", "影子二")


def secs(iso: str) -> float:
    d = datetime.fromisoformat(iso)
    return d.hour * 3600 + d.minute * 60 + d.second + d.microsecond / 1e6


def cache_path(out: Path, day: str, side: str, mode: str) -> Path:
    return out / "events" / day / f"{side}_{mode}.csv"


def save_events(path: Path, events: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        if events:
            w = csv.DictWriter(fh, fieldnames=list(events[0]))
            w.writeheader()
            w.writerows(events)
    tmp.replace(path)


def load_events(path: Path, side: str) -> List[Dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["label_exit_reason"] in ("NOT_FILLED", "") or r["label_net_points"] == "":
                continue
            rows.append({"day": r["day"], "t": secs(r["time"]), "xt": secs(r["label_exit_time"]), "side": side,
                         "gate": r["gate_pass"] == "True", "time": r["time"][11:19],
                         "entry": float(r["label_entry_fill"]), "exit": float(r["label_exit_fill"]),
                         "exit_time": r["label_exit_time"][11:19], "reason": r["label_exit_reason"],
                         "net": float(r["label_net_points"])})
    return rows


def account(sigs: List[Dict[str, Any]], n_max: int) -> List[Dict[str, Any]]:
    """一次只持有一筆；每日上限、平倉後冷卻、連虧冷卻（同 2026-11-11 探索腳本）。"""
    by_day = defaultdict(list)
    for e in sigs:
        by_day[e["day"]].append(e)
    out = []
    for _, es in sorted(by_day.items()):
        es.sort(key=lambda e: e["t"])
        taken, last_exit, streak, block_until = [], None, 0, 0.0
        for e in es:
            if not e["gate"]:
                continue
            if len(taken) >= n_max:
                break
            if last_exit is not None and e["t"] < last_exit + COOLDOWN_MIN * 60:
                continue
            if e["t"] < block_until:
                continue
            taken.append(e)
            last_exit = e["xt"]
            streak = streak + 1 if e["net"] < 0 else 0
            if streak >= LOSS_N:
                block_until = e["xt"] + LOSS_COOLDOWN_MIN * 60
        out.extend(taken)
    return out


def stats(trades: List[Dict[str, Any]], days: List[str], moves: Dict[str, float]) -> Dict[str, Any]:
    daily = {d: 0.0 for d in days}
    for e in trades:
        if e["day"] in daily:
            daily[e["day"]] += e["net"] * PV
    vals = [daily[d] for d in days]
    n = sum(1 for e in trades if e["day"] in daily)
    wins = sum(1 for e in trades if e["day"] in daily and e["net"] > 0)
    rng = random.Random(SEED)
    boots = sorted(sum(rng.choice(vals) for _ in vals) / len(vals) for _ in range(BOOT)) if vals else [0.0]
    lo_i, hi_i = int(BOOT * 0.025), int(BOOT * 0.975) - 1
    return {"n": n, "win": 100 * wins / n if n else 0.0, "total": sum(vals), "per": sum(vals) / n if n else 0.0,
            "drop": sum(vals) - sum(sorted(vals, reverse=True)[:DROP_BEST]),
            "lo": boots[lo_i] if vals else 0.0, "hi": boots[hi_i] if vals else 0.0,
            "worst": min(vals) if vals else 0.0,
            "down": sum(daily[d] for d in days if moves[d] <= -BIG_MOVE_PCT),
            "other": sum(daily[d] for d in days if moves[d] > -BIG_MOVE_PCT), "daily": daily}


def verdict(s: Dict[str, Any], base: Dict[str, Any], final: bool) -> tuple:
    checks = [("合計 > 0", s["total"] > 0), ("合計 > 現行", s["total"] > base["total"]),
              (f"拿掉最好 {DROP_BEST} 天仍 > 0", s["drop"] > 0), ("每日平均 95% CI 下界 > 0", s["lo"] > 0)]
    if all(ok for _, ok in checks):
        word = "通過"
    elif all(ok for _, ok in checks[:3]):
        word = "未通過（第二次看是最後一次）" if final else "再看一次（累積到 80 天）"
    else:
        word = "未通過"
    return word, checks


def weekday_gaps(days: List[str]) -> List[str]:
    out = []
    for a, b in zip(days, days[1:]):
        d = date.fromisoformat(a) + timedelta(days=1)
        while d < date.fromisoformat(b):
            if d.weekday() < 5:
                out.append(str(d))
            d += timedelta(days=1)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", required=True, help="網友 Momentum_Scalping repo")
    p.add_argument("--taifex", required=True, help="期交所逐筆 Daily_*.csv 資料夾（絕對路徑）")
    p.add_argument("--out", required=True, help="輸出資料夾（絕對路徑，repo 外）")
    p.add_argument("--start", default=START, help="檢查用：從更早的日子重算，對照探索結果；判定一律從 2026-11-11")
    p.add_argument("--workers", type=int, default=4)
    args = p.parse_args()
    out = Path(args.out)
    head = subprocess.run(["git", "-C", args.repo, "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
    if not head.stdout.startswith(MS_COMMIT):
        raise SystemExit(f"[錯誤] {args.repo} 在 {head.stdout.strip()[:7]}，事前登記凍結的是 {MS_COMMIT}：規則會跟著變，先切回去")
    out.mkdir(parents=True, exist_ok=True)

    data = F.read_taifex(Path(args.taifex), PRODUCT)
    all_days = sorted(data)
    daily_feat: Dict[str, Dict[str, Any]] = {}
    for i, day in enumerate(all_days):  # 同 futures_signal_dataset.main
        if i == 0:
            daily_feat[day] = {"prev_close": None, "ma5": None}
            continue
        previous = data[all_days[i - 1]]
        prev_close = previous["closes"].get(data[day]["contract"]) or previous["closes"].get(previous["contract"])
        history = [data[d]["closes"][data[d]["contract"]] for d in all_days[max(0, i - 5):i]]
        daily_feat[day] = {"prev_close": prev_close, "ma5": statistics.fmean(history) if len(history) == 5 else None}
    days = [d for d in all_days[1:] if d >= args.start]
    if not days:
        print(f"[等待] {args.taifex} 還沒有 {args.start} 以後的小台一般時段逐筆")
        return 0
    for day in days:
        last = M.us_to_iso(data[day]["ticks"][-1][0])[11:16]
        if last < LATEST_LAST_TICK:
            raise SystemExit(f"[錯誤] {day} 逐筆最後一筆是 {last}，早於 {LATEST_LAST_TICK}：檔案可能不完整，請重新下載")

    cfg = F.Config(product=PRODUCT, point_value=PV, contracts=1, commission=30.0, tax_rate=0.00002, slip_ticks=1,
                   tick=1.0, latency_us=500_000, eval_interval_us=1_000_000, refresh_us=30 * M.US)
    jobs = [(day, data[day], daily_feat[day], cfg, side, mode)
            for day in days for side, mode in SETS if not cache_path(out, day, side, mode).exists()]
    if jobs:
        print(f"[計算] {len(jobs)} 個（日 × 方向 × 出場）", flush=True)
        with multiprocessing.Pool(max(1, args.workers), initializer=D._init, initargs=(args.repo, None, [])) as pool:
            for job, result in zip(jobs, pool.imap(D.run_day, jobs)):
                day, side, mode = job[0], job[4], job[5]
                save_events(cache_path(out, day, side, mode), result["events"])
                print(f"  {day} {side}/{mode}：{len(result['events'])} 個訊號", flush=True)

    moves = {}
    for day in days:
        close = data[day]["closes"][data[day]["contract"]]
        prev = daily_feat[day]["prev_close"]
        moves[day] = (close / prev - 1) * 100 if prev else 0.0
    sigs = {(s, m): [e for day in days for e in load_events(cache_path(out, day, s, m), s)] for s, m in SETS}
    trades = {name: account([e for key in b["sets"] for e in sigs[key]], b["n_max"]) for name, b in BOOKS.items()}

    with (out / "trades.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["帳", "日期", "方向", "訊號時間", "進場價", "出場時間", "出場價", "出場原因", "淨點數", "淨損益"])
        for name, ts in trades.items():
            for e in ts:
                w.writerow([name, e["day"], "多" if e["side"] == "long" else "空", e["time"], e["entry"],
                            e["exit_time"], e["exit"], e["reason"], round(e["net"], 2), round(e["net"] * PV)])
    full = {name: stats(ts, days, moves) for name, ts in trades.items()}
    with (out / "daily.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["日期", "合約", "漲跌%", *BOOKS])
        for d in days:
            w.writerow([d, data[d]["contract"], round(moves[d], 2), *(round(full[n]["daily"][d]) for n in BOOKS)])

    lines = ["# 小台動能順勢：影子帳往前判定", "",
             f"產生時間：{datetime.now():%Y-%m-%d %H:%M}；事前登記 studies/shadow_books/PREREGISTRATION.md", "",
             f"交易日 {len(days)} 天（{days[0]}～{days[-1]}）"
             + ("" if args.start == START else f"；**檢查模式：從 {args.start} 起，含已看過的日子，不是判定**"), ""]
    gaps = weekday_gaps(days)
    if gaps:
        lines += [f"⚠ 這些平日沒有資料（休市就沒事，不是休市要補下載）：{'、'.join(gaps)}", ""]
    hdr = ["| 帳 | 筆數 | 勝率 | 每筆 | 合計 | 拿掉最好 3 天 | 大跌日 | 其他日 | 最差一天 | 每日平均 95% CI |",
           "|---|---|---|---|---|---|---|---|---|---|"]

    def row(name: str, s: Dict[str, Any]) -> str:
        return (f"| {name} | {s['n']} | {s['win']:.0f}% | {s['per']:+,.0f} | {s['total']:+,.0f} | {s['drop']:+,.0f} | "
                f"{s['down']:+,.0f} | {s['other']:+,.0f} | {s['worst']:+,.0f} | {s['lo']:+,.0f}～{s['hi']:+,.0f} |")

    lines += ["## 到目前為止（只是進度，不是判定）", "", *hdr, *(row(n, s) for n, s in full.items()), ""]
    alive = list(SHADOWS)
    for k, look in enumerate(LOOKS):
        if args.start != START or not alive:
            break
        if len(days) < look:
            lines += [f"## 第 {k + 1} 次判定：累積中 {len(days)} / {look} 天", ""]
            break
        sub = days[:look]
        part = {name: stats(ts, sub, moves) for name, ts in trades.items()}
        lines += [f"## 第 {k + 1} 次判定（前 {look} 天：{sub[0]}～{sub[-1]}）", "", *hdr,
                  *(row(n, s) for n, s in part.items()), ""]
        still = []
        for name in alive:
            word, checks = verdict(part[name], part["現行"], final=k == len(LOOKS) - 1)
            lines.append(f"- **{name}：{word}**（" + "；".join(f"{c} {'✓' if ok else '✗'}" for c, ok in checks) + "）")
            if word.startswith("再看一次"):
                still.append(name)
        lines.append("")
        alive = still
    lines += ["", "逐筆：trades.csv；逐日：daily.csv"]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
