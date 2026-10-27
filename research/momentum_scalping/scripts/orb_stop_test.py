#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
orb_stop_test.py — studies/orb_stop：開盤突破做多的停損放遠一點（照 PREREGISTRATION.md）

S0 原本（5 分鐘低點）、S1 低點再留半個區間、S2 至少留四分之一個 ATR14、S3 不設停損（只報告）、
S4 被洗掉後收回再進一次、S5 等 09:00 現股開盤（後兩個是 2026-10-28 補充）。
進場、停利（原本 10R 的位置）、成交、成本全部沿用 minute_bidir_test.py（fubon_9y）的開盤突破；
S0 每一筆都要和 minute_bidir_test.run_orb 的做多一模一樣（程式自己檢查）。

用法：
  富邦九年（判定）：python orb_stop_test.py --mode oos --bars D:/.../fubon_1m --calendar D:/.../tx_daily --out D:/.../orb_stop
  期交所 30 天（已污染，只檢查程式）：
    python orb_stop_test.py --mode seen --taifex D:/.../taifex_30d/csv --calendar D:/.../tx_daily --out D:/.../orb_stop_seen
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gap_fail_test as GF  # noqa: E402
import intraday_bidir_test as B  # noqa: E402
import minute_bidir_test as MB  # noqa: E402
import minute_engine_selftest as ST  # noqa: E402
import ms_replay as M  # noqa: E402

HALF_RANGE = 0.5             # S1：5 分鐘低點再往下留半個 5 分鐘區間
ATR_SHARE = 0.25             # S2：停損至少離進場價四分之一個 ATR14
ATR_DAYS = 14
SPOT_OPEN = 15               # S5：現股 09:00 開盤＝期貨開盤後第 15 分鐘；看 09:00～09:04 這 5 根
CI_LEVEL = 0.9875            # 四個版本（S1、S2、S4、S5）一起算：95% 的 5% 平均分給四個
MIN_TRADES = 100
JUDGE_END, SEEN = "2026-09-08", ("2026-09-09", "2026-10-27")
VARIANTS = {"S0": "S0 原本（5 分鐘低點）", "S1": "S1 低點再留半個區間", "S2": "S2 至少留四分之一個日均波動",
            "S3": "S3 不設停損（只報告）", "S4": "S4 被洗掉後收回再進一次", "S5": "S5 等 09:00 現股開盤"}
JUDGED = ("S1", "S2", "S4", "S5")


def ci(xs, level, iterations=2000, seed=7):
    """同 intraday_bidir_test.ci95 的 bootstrap（2000 次、seed 7），信賴水準可以換；level=0.95 時結果和 ci95 相同。"""
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(xs, k=len(xs))) for _ in range(iterations))
    tail = (1 - level) / 2
    return means[int(round(tail * iterations))], means[int(round((1 - tail) * iterations)) - 1]


def true_range(d: MB.MinuteDay) -> float:
    h, low = max(d.h), min(d.l)
    return max(h - low, abs(h - d.prev_close), abs(low - d.prev_close))


def atr_of(days, k: int) -> float:
    """第 k 天的 ATR14：前 14 個有效交易日的真實波幅平均（不含當天）。"""
    if k < ATR_DAYS or any(d.prev_close is None for d in days[k - ATR_DAYS:k]):
        raise ValueError(f"{days[k].day} 前面不足 {ATR_DAYS} 天有昨收的資料，算不出 ATR14")
    return statistics.fmean(true_range(d) for d in days[k - ATR_DAYS:k])


def hold_long(d: MB.MinuteDay, ei: int, stop, target: float, stop_fill: str):
    """從進場那根（第 ei 根，含）起逐根檢查停損、停利（同 minute_bidir_test.run_orb）；回傳 (出場分鐘, 出場價, 原因, 觸發那根)。"""
    for j in range(ei, len(d.m)):
        if d.m[j] >= d.eod:
            break
        if stop is not None and d.l[j] <= stop:
            reason, level = "STOP", stop
        elif d.h[j] >= target:
            reason, level = "TARGET", target
        else:
            continue
        if stop_fill == "next_open":
            xt, xp, _ = d.fill(d.m[j] + 1, -1)
        else:
            # 碰到價位就出場；開盤就已經越過價位時，用開盤價
            crossed = d.o[j] <= level if reason == "STOP" else d.o[j] >= level
            xt, xp = d.m[j], (d.o[j] if crossed else level) - B.SLIP
        return xt, xp, reason, j
    xt, xp, _ = d.fill(d.eod, -1)
    return xt, xp, "EOD", None


def window(d: MB.MinuteDay, start: int, minutes: int):
    """開盤後第 start 分鐘起 minutes 分鐘的 K：(第一根, 最後一根之後) 的位置；一根都沒有 → None。"""
    first = next((i for i, m in enumerate(d.m) if m >= d.at(start)), None)
    last = d.last_before(d.at(start + minutes)) + 1
    return None if first is None or last <= first else (first, last)


def row(d, variant, signal_t, entry, exit_, stop, target, atr, leg="first"):
    (et, ep), (xt, xp, reason) = entry, exit_
    t = MB.trade(d, "ORB5", 1, signal_t, (et, ep), (xt, xp), reason, stop)
    return {**t, "variant": variant, "leg": leg, "target_price": round(target, 1), "atr14": round(atr, 1),
            "stop_distance": None if stop is None else round(ep - stop, 1), "close_1344": d.price_at(d.eod)}


def run(d: MB.MinuteDay, variant: str, atr: float, stop_fill: str = "level"):
    """一天、一個版本的開盤突破做多（小台 1 口）。"""
    start = SPOT_OPEN if variant == "S5" else 0
    span = window(d, start, B.ORB_MINUTES)
    if span is None:
        return []
    first, k = span
    if d.c[k - 1] <= d.o[first]:
        return []
    low, high = min(d.l[first:k]), max(d.h[first:k])
    signal_t = d.at(start + B.ORB_MINUTES)
    et, ep, ei = d.fill(signal_t, 1)
    risk = ep - low
    if risk <= 0 or et >= d.eod:
        return []
    target = ep + B.ORB_TARGET_R * risk
    stop = {"S0": low, "S1": low - HALF_RANGE * (high - low), "S2": min(low, ep - ATR_SHARE * atr), "S3": None,
            "S4": low, "S5": low}[variant]
    xt, xp, reason, j = hold_long(d, ei, stop, target, stop_fill)
    rows = [row(d, variant, signal_t, (et, ep), (xt, xp, reason), stop, target, atr)]
    if variant != "S4" or reason != "STOP":
        return rows
    # S4：從碰到停損那根（含）起，第一根收盤站回 5 分鐘低點之上 → 下一根開盤再買 1 口；一天只再進一次
    sweep = None
    for i in range(j, len(d.m)):
        if d.m[i] >= d.eod:
            break
        sweep = d.l[i] if sweep is None else min(sweep, d.l[i])
        if d.c[i] <= low:
            continue
        t2 = d.m[i] + 1
        et2, ep2, ei2 = d.fill(t2, 1)
        if et2 >= d.eod or ep2 <= sweep or ep2 >= target:
            break
        xt2, xp2, reason2, _ = hold_long(d, ei2, sweep, target, stop_fill)
        rows.append(row(d, variant, t2, (et2, ep2), (xt2, xp2, reason2), sweep, target, atr, leg="reentry"))
        break
    return rows


def evaluate(days, tested, stop_fill="level"):
    index = {d.day: k for k, d in enumerate(days)}
    trades = []
    for day in tested:
        k = index[day]
        atr = atr_of(days, k)
        for v in VARIANTS:
            trades += run(days[k], v, atr, stop_fill)
    return trades


def self_check(days, tested):
    """S0 的每一筆要和 minute_bidir_test.run_orb 的做多一模一樣。"""
    index = {d.day: k for k, d in enumerate(days)}
    for day in tested:
        d = days[index[day]]
        mine = [(t["entry_price"], t["exit_price"], t["exit_reason"]) for t in run(d, "S0", atr_of(days, index[day]))]
        ref = [(t["entry_price"], t["exit_price"], t["exit_reason"]) for t in MB.run_orb(d) if t["side"] > 0]
        if mine != ref:
            raise SystemExit(f"{day} S0 和 minute_bidir_test.run_orb 不一樣：{mine} vs {ref}")


def daily(trades, tested, variant):
    per = {d: 0.0 for d in tested}
    for t in trades:
        if t["variant"] == variant:
            per[t["day"]] += t["net_pnl"]
    return [per[d] for d in tested]


def cell(trades, tested, variant):
    rows = [t for t in trades if t["variant"] == variant]
    s = B.describe(rows, tested)
    s.pop("cumulative")
    vals = daily(trades, tested, variant)
    lo, hi = ci(vals, CI_LEVEL)
    stops = [t for t in rows if t["exit_reason"] == "STOP"]
    dist = [t["stop_distance"] for t in rows if t["stop_distance"] is not None]
    s.update({
        "daily_ci_strict": [round(lo, 0), round(hi, 0)],
        "exit_reasons": dict(sorted({r: sum(t["exit_reason"] == r for t in rows) for r in {t["exit_reason"] for t in rows}}.items())),
        "stops": len(stops), "stops_recovered": sum(t["close_1344"] > t["entry_price"] for t in stops),
        "stop_distance_pts": round(statistics.fmean(dist), 1) if dist else None,
        "stop_distance_pct": round(statistics.fmean(t["stop_distance"] / t["entry_price"] * 100 for t in rows
                                                    if t["stop_distance"] is not None), 3) if dist else None,
        "makes_money": s["n"] >= MIN_TRADES and s["daily_mean"] > 0 and lo > 0,
    })
    again = [t for t in rows if t.get("leg") == "reentry"]
    if again:
        s["reentries"] = {"n": len(again), "stopped_again": sum(t["exit_reason"] == "STOP" for t in again),
                          "mean_net": round(statistics.fmean(t["net_pnl"] for t in again), 0)}
    if variant == "S5":
        base = {t["day"] for t in trades if t["variant"] == "S0"}
        mine = {t["day"] for t in rows}
        s["s0_overlap"] = {"both": len(base & mine), "only_s5": len(mine - base), "only_s0": len(base - mine)}
    return s


def versus_s0(trades, tested, variant):
    base = daily(trades, tested, "S0")
    mine = daily(trades, tested, variant)
    diff = [a - b for a, b in zip(mine, base)]
    lo, hi = ci(diff, CI_LEVEL)
    mean = statistics.fmean(diff)
    return {"daily_mean": round(mean, 0), "daily_ci_strict": [round(lo, 0), round(hi, 0)], "better": mean > 0 and lo > 0}


def verdict(c, vs):
    if not vs["better"]:
        return "照舊（沒有比原本好）"
    return "可以考慮換（比原本好、本身也賺錢）" if c["makes_money"] else "放遠比原本好，但本身還是不賺，不換"


def report(days, tested, trades, sens):
    cells = {v: cell(trades, tested, v) for v in VARIANTS}
    vs = {v: versus_s0(trades, tested, v) for v in VARIANTS if v != "S0"}
    out = {"days": len(tested), "first": tested[0], "last": tested[-1], "cells": cells, "versus_s0": vs,
           "verdict": {v: verdict(cells[v], vs[v]) for v in JUDGED},
           "yearly": {v: MB.yearly([t for t in trades if t["variant"] == v], tested) for v in VARIANTS},
           "top_days": {v: MB.top_share([t for t in trades if t["variant"] == v], tested) for v in VARIANTS}}
    if sens is not None:
        out["sensitivity_next_open"] = {v: cell(sens, tested, v) for v in VARIANTS}
    return out


def oos(raw, args):
    names, calendar_days, days, excluded = GF.prepare(raw, args.calendar)
    usable = [d.day for k, d in enumerate(days) if k >= ATR_DAYS and all(x.prev_close is not None for x in days[k - ATR_DAYS:k + 1])]
    judged = [d for d in usable if d <= JUDGE_END]
    after = [d for d in usable if d > SEEN[1]]
    if not judged:
        raise SystemExit(f"判定期沒有任何一天（富邦 {names[0]}～{names[-1]}）")
    self_check(days, judged)
    trades = evaluate(days, judged)
    result = report(days, judged, trades, evaluate(days, judged, "next_open"))
    result.update({"bars_first": names[0], "bars_last": names[-1], "excluded": excluded,
                   "missing_vs_calendar": [d for d in calendar_days if d not in raw]})
    later = evaluate(days, after) if after else []
    result["after"] = report(days, after, later, None) if after else None
    return result, trades + later


def seen(args):
    bars = Path(args.out) / "bars"
    n = ST.ticks_to_bars(Path(args.taifex), "MTX", bars)
    print(f"期交所逐筆合成 1 分 K：{n} 根", flush=True)
    raw = MB.load_bars(bars)
    names, _, days, excluded = GF.prepare(raw, args.calendar)
    tested = [d.day for k, d in enumerate(days) if k >= ATR_DAYS and all(x.prev_close is not None for x in days[k - ATR_DAYS:k + 1])]
    if not tested:
        raise SystemExit("期交所逐筆不足 15 天，算不出 ATR14")
    self_check(days, tested)
    trades = evaluate(days, tested)
    result = report(days, tested, trades, None)
    result["excluded"] = excluded
    return result, trades


def cell_table(cells, judged):
    head = ("| 版本 | 天數 | 筆數 | 勝率 | 每筆淨點數 | 每日平均 | 95% CI | 98.75% CI | 合計 | 停損次數 | 停損後收盤回到進場價之上 | 平均停損距離 |"
            + (" 本身賺錢 |" if judged else ""))
    lines = [head, "|" + "---|" * (12 + judged)]
    notes = []
    for v, s in cells.items():
        dist = "—" if s["stop_distance_pts"] is None else f"{s['stop_distance_pts']:.0f} 點（{s['stop_distance_pct']:.2f}%）"
        if not s["n"]:
            lines.append(f"| {VARIANTS[v]} | {s['days']} | 0 | — | — | 0 | — | — | 0 | 0 | 0 | — |" + (" ❌ |" if judged else ""))
            continue
        lines.append(f"| {VARIANTS[v]} | {s['days']} | {s['n']} | {s['win_rate']}% | {s['net_points']:+.1f} | {s['daily_mean']:+,.0f} | "
                     f"{s['daily_ci'][0]:+,.0f} ～ {s['daily_ci'][1]:+,.0f} | {s['daily_ci_strict'][0]:+,.0f} ～ {s['daily_ci_strict'][1]:+,.0f} | "
                     f"{s['total_net']:+,.0f} | {s['stops']} | {s['stops_recovered']} | {dist} |"
                     + ((" ✅ |" if s["makes_money"] else " ❌ |") if judged else ""))
        if "reentries" in s:
            r = s["reentries"]
            notes.append(f"- {VARIANTS[v]}：再進場 {r['n']} 次，其中又被停損 {r['stopped_again']} 次（假收回），再進場那筆平均 {r['mean_net']:+,.0f} 元。")
        if "s0_overlap" in s:
            o = s["s0_overlap"]
            notes.append(f"- {VARIANTS[v]}：和 S0 都做多的 {o['both']} 天，只有 S5 做的 {o['only_s5']} 天，只有 S0 做的 {o['only_s0']} 天。")
    return lines + ([""] + notes if notes else [])


def vs_table(vs):
    lines = ["| 版本 | 每天比原本多（平均） | 98.75% CI | 比原本好 |", "|---|---|---|---|"]
    lines += [f"| {VARIANTS[v]} | {s['daily_mean']:+,.0f} | {s['daily_ci_strict'][0]:+,.0f} ～ {s['daily_ci_strict'][1]:+,.0f} | {'✅' if s['better'] else '❌'} |"
              for v, s in vs.items()]
    return lines


def trade_table(trades):
    lines = ["| 日期 | 版本 | 進場 | 停損 | 停利 | 出場 | 原因 | 淨損益 | 13:44 價 |", "|---|---|---|---|---|---|---|---|---|"]
    for t in sorted(trades, key=lambda t: (t["day"], t["variant"], t["entry_time"])):
        stop = "—" if t["stop_price"] is None else f"{t['stop_price']:,.0f}"
        lines.append(f"| {t['day']} | {t['variant']}{'（再進）' if t.get('leg') == 'reentry' else ''} | {t['entry_time']} {t['entry_price']:,.0f} | {stop} | {t['target_price']:,.0f} | "
                     f"{t['exit_time']} {t['exit_price']:,.0f} | {t['exit_reason']} | {t['net_pnl']:+,.0f} | {t['close_1344']:,.0f} |")
    return lines


def write_seen_md(out: Path, r, trades):
    lines = ["# 開盤突破停損放遠：期交所 30 天逐筆（已污染，只檢查程式）", "",
             f"**這段每一天都看過，結果不算數**（見 PREREGISTRATION.md）。ATR14 要前 14 天，所以只有 {r['first']}～{r['last']} 共 {r['days']} 天。",
             "S0 每一筆都和 minute_bidir_test.run_orb 的做多一模一樣（程式檢查過）。", "",
             *cell_table(r["cells"], False), "", *vs_table(r["versus_s0"]), "", "## 每一筆", "", *trade_table(trades)]
    (out / "SEEN.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def write_oos_md(out: Path, r):
    lines = ["# 開盤突破停損放遠：富邦小台 1 分 K 結果", "",
             f"照 PREREGISTRATION.md 一次跑完。富邦資料 {r['bars_first']}～{r['bars_last']}；判定期 {r['days']} 天（{r['first']}～{r['last']}）。",
             f"富邦缺的交易日 {len(r['missing_vs_calendar'])} 天；資料不完整而排除 {len(r['excluded'])} 天。S0 和 minute_bidir_test.run_orb 一模一樣（程式檢查過）。", "",
             *cell_table(r["cells"], True), "", "## 比原本好嗎（每天 這個版本 − S0）", "", *vs_table(r["versus_s0"]), "",
             "## 結論", ""]
    lines += [f"- {VARIANTS[v]}：{text}" for v, text in r["verdict"].items()]
    for v in VARIANTS:
        lines += ["", f"## {VARIANTS[v]} 分年度", "", "| 年 | 天數 | 筆數 | 每日平均 | 合計 |", "|---|---|---|---|---|"]
        lines += [f"| {y} | {s['days']} | {s['n']} | {s['daily_mean']:+,.0f} | {s['total']:+,.0f} |" for y, s in r["yearly"][v].items()]
        t = r["top_days"][v]
        lines.append(f"\n最好的 {MB.TOP_DAYS} 天合計 {t['top_days_total']:+,.0f}，其餘合計 {t['rest_total']:+,.0f}（全部 {t['total']:+,.0f}）。")
    lines += ["", "## 2026-10-28 之後（登記之後，只報告）", ""]
    lines += cell_table(r["after"]["cells"], False) if r["after"] else ["還沒有資料。"]
    lines += ["", "## 敏感度（不列入判定）：停損、停利改成下一根開盤成交", "", *cell_table(r["sensitivity_next_open"], False)]
    (out / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["oos", "seen"], required=True)
    p.add_argument("--bars", help="fubon_minute_download.py 的輸出資料夾（oos）")
    p.add_argument("--taifex", help="期交所逐筆 Daily_*.csv 資料夾（seen）")
    p.add_argument("--calendar", required=True, help="期交所 TX 日資料資料夾（對照缺的交易日）")
    p.add_argument("--out", required=True)
    args = p.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.mode == "seen":
        if not args.taifex:
            raise SystemExit("seen 要給 --taifex")
        result, trades = seen(args)
        (out / "seen.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        M.write_csv(out / "seen_trades.csv", trades)
        write_seen_md(out, result, trades)
        return 0
    if not args.bars:
        raise SystemExit("oos 要給 --bars")
    raw = MB.load_bars(Path(args.bars))
    if not raw:
        raise SystemExit(f"{args.bars} 沒有任何 K")
    result, trades = oos(raw, args)
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    M.write_csv(out / "trades.csv", trades)
    write_oos_md(out, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
