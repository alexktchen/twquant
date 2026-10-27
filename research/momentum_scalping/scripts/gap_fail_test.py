#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gap_fail_test.py — studies/gap_fail：小台「開高走低」三條放空規則（照 PREREGISTRATION.md）

A 開高失守、B 開高跌破開盤區間、C 補缺口，加上對照 G（開高日 08:50 直接放空、收盤回補）。
K 棒、主力合約、昨收、成交、成本、統計全部沿用 minute_bidir_test.py（fubon_9y），只有規則是新的。

用法：
  富邦九年（判定）：python gap_fail_test.py --mode oos --bars D:/.../fubon_1m --calendar D:/.../tx_daily --out D:/.../gap_fail
  期交所 30 天（已污染，只檢查程式）：
    python gap_fail_test.py --mode seen --taifex D:/.../taifex_30d/csv --calendar D:/.../tx_daily --out D:/.../gap_fail_seen
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import intraday_bidir_test as B  # noqa: E402
import minute_bidir_test as MB  # noqa: E402
import minute_engine_selftest as ST  # noqa: E402
import ms_replay as M  # noqa: E402
import stock_download as S  # noqa: E402

GAP_MIN = 0.00526            # studies/gap_strategy/result.json 的 thresholds_pct.q80：探索期 1998-07～2015-12 跳空 80 分位
A_FROM = B.ORB_MINUTES       # A、G 從開盤後第 5 分鐘（08:50）起
RANGE_MINUTES = 15           # B 的開盤區間：08:45～08:59
MIN_TRADES = 100
JUDGE_END, SEEN = "2026-09-08", ("2026-09-09", "2026-10-27")
RULES = {"A": "A 開高失守", "B": "B 開高跌破開盤區間", "C": "C 補缺口", "G": "對照 G：開高就空"}
SENS_RULES = ("A", "B", "C")  # G 不設停損，敏感度和原本一樣


def gap(d: MB.MinuteDay) -> float:
    return d.open / d.prev_close - 1


def first_below(d: MB.MinuteDay, level: float, start: int):
    """start 起每分鐘檢查一次，價格第一次 < level 的時點；收盤平倉前都沒有就回 None。"""
    t = start
    while t < d.eod:
        if d.price_at(t) < level:
            return t
        t += 1
    return None


def short(d: MB.MinuteDay, rule, signal_t, stop=None, target=None, stop_fill="level"):
    """signal_t 結束後放空 1 口；stop、target 是價位（None = 不設），碰到就回補，否則收盤平倉。"""
    et, ep, ei = d.fill(signal_t, -1)
    if et >= d.eod or (stop is not None and stop - ep <= 0):
        return []
    for j in range(ei, len(d.m)):
        if d.m[j] >= d.eod:
            break
        if stop is not None and d.h[j] >= stop:
            reason, level = "STOP", stop
        elif target is not None and d.l[j] <= target:
            reason, level = "TARGET", target
        else:
            continue
        if stop_fill == "next_open":
            xt, xp, _ = d.fill(d.m[j] + 1, 1)
        else:
            # 碰到價位就回補；開盤就已經越過價位時，用開盤價
            crossed = d.o[j] >= level if reason == "STOP" else d.o[j] <= level
            xt, xp = d.m[j], (d.o[j] if crossed else level) + B.SLIP
        return [MB.trade(d, rule, -1, signal_t, (et, ep), (xt, xp), reason, stop)]
    xt, xp, _ = d.fill(d.eod, 1)
    return [MB.trade(d, rule, -1, signal_t, (et, ep), (xt, xp), "EOD", stop)]


def run_day(d: MB.MinuteDay, stop_fill="level"):
    if d.prev_close is None:
        raise ValueError(f"{d.day} 沒有昨收")
    g = gap(d)
    if g < GAP_MIN:
        return []
    trades = []
    t = first_below(d, d.open, d.at(A_FROM))
    if t is not None:
        stop = max(d.h[:d.last_before(t) + 1])
        trades += short(d, "A", t, stop=stop, stop_fill=stop_fill)
        _, _, ei = d.fill(t, -1)
        if d.o[ei] > d.prev_close:  # 進場那根開盤就已經補完缺口，C 不做
            trades += short(d, "C", t, stop=stop, target=d.prev_close, stop_fill=stop_fill)
    k = d.last_before(d.at(RANGE_MINUTES)) + 1
    t = first_below(d, min(d.l[:k]), d.at(RANGE_MINUTES))
    if t is not None:
        trades += short(d, "B", t, stop=max(d.h[:k]), stop_fill=stop_fill)
    trades += short(d, "G", d.at(A_FROM), stop_fill=stop_fill)
    return [{**x, "gap_pct": round(g * 100, 3), "prev_close": d.prev_close, "day_open": d.open} for x in trades]


def evaluate(days, tested, stop_fill="level"):
    index = {d.day: d for d in days}
    trades = []
    for day in tested:
        trades += run_day(index[day], stop_fill)
    return trades


def cells(trades, tested, gap_days, rules=RULES):
    out = {}
    for rule in rules:
        rows = [t for t in trades if t["rule"] == rule]
        s = B.describe(rows, tested)
        s.pop("cumulative")
        s["gap_days"] = gap_days
        s["exit_reasons"] = dict(sorted({r: sum(t["exit_reason"] == r for t in rows)
                                         for r in {t["exit_reason"] for t in rows}}.items()))
        s["passed"] = s["passed"] and s["n"] >= MIN_TRADES
        out[rule] = s
    return out


def versus_g(trades, tested):
    """同一批日子，每天 規則損益 − G 損益 的平均與 CI。"""
    g = MB.daily_pnl([t for t in trades if t["rule"] == "G"], tested)
    out = {}
    for rule in SENS_RULES:
        r = MB.daily_pnl([t for t in trades if t["rule"] == rule], tested)
        diff = [r[d] - g[d] for d in tested]
        lo, hi = B.ci95(diff)
        out[rule] = {"daily_mean": round(statistics.fmean(diff), 0), "daily_ci": [round(lo, 0), round(hi, 0)]}
    return out


def gap_days_of(days, tested):
    index = {d.day: d for d in days}
    return sum(gap(index[day]) >= GAP_MIN for day in tested)


def prepare(raw, calendar_dir):
    names = sorted(raw)
    calendar_days = [d for d in S.calendar(calendar_dir, "2017-01-01", "2099-12-31") if names[0] <= d <= names[-1]]
    days, excluded = MB.build_days(raw, "typical", calendar_days)  # 這三條規則用不到 VWAP
    return names, calendar_days, days, excluded


def oos(raw, args):
    names, calendar_days, days, excluded = prepare(raw, args.calendar)
    valid = [d.day for d in days if d.prev_close is not None]
    judged = [d for d in valid if d <= JUDGE_END]
    after = [d for d in valid if d > SEEN[1]]
    if not judged:
        raise SystemExit(f"判定期沒有任何一天（富邦 {names[0]}～{names[-1]}）")
    trades = evaluate(days, judged)
    sens = evaluate(days, judged, "next_open")
    later = evaluate(days, after) if after else []
    n_gap = gap_days_of(days, judged)
    result = {
        "first": names[0], "last": names[-1], "judged_days": len(judged), "judged_first": judged[0], "judged_last": judged[-1],
        "gap_min_pct": GAP_MIN * 100, "missing_vs_calendar": [d for d in calendar_days if d not in raw], "excluded": excluded,
        "cells": cells(trades, judged, n_gap), "versus_g": versus_g(trades, judged),
        "yearly": {r: MB.yearly([t for t in trades if t["rule"] == r], judged) for r in RULES},
        "top_days": {r: MB.top_share([t for t in trades if t["rule"] == r], judged) for r in RULES},
        "after": {"days": after, "cells": cells(later, after, gap_days_of(days, after)) if after else {}},
        "sensitivity_next_open": cells(sens, judged, n_gap, SENS_RULES),
    }
    c = result["cells"]
    result["verdict"] = {"A_passed": c["A"]["passed"], "B_lead": c["B"]["passed"], "C_lead": c["C"]["passed"],
                         "G_passed": c["G"]["passed"]}
    return result, trades + later


def seen(args):
    bars = Path(args.out) / "bars"
    n = ST.ticks_to_bars(Path(args.taifex), "MTX", bars)
    print(f"期交所逐筆合成 1 分 K：{n} 根", flush=True)
    raw = MB.load_bars(bars)
    names, _, days, excluded = prepare(raw, args.calendar)
    tested = [d.day for d in days if d.prev_close is not None]
    trades = evaluate(days, tested)
    result = {"first": names[0], "last": names[-1], "tested_days": len(tested), "tested_first": tested[0],
              "tested_last": tested[-1], "gap_min_pct": GAP_MIN * 100, "excluded": excluded,
              "gaps": {d.day: round(gap(d) * 100, 3) for d in days if d.prev_close is not None},
              "cells": cells(trades, tested, gap_days_of(days, tested))}
    return result, trades


def cell_table(c, judged=True):
    lines = ["| 規則 | 天數 | 開高日 | 筆數 | 勝率 | 每筆毛點數 | 每筆成本點數 | 每筆淨點數 | 平均持有分鐘 | 每日平均淨損益 | 以天 95% CI | "
             "合計淨損益 | 出場原因 |" + (" 達標 |" if judged else ""),
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|" + ("---|" if judged else "")]
    for rule, s in c.items():
        mark = (" ✅ |" if s["passed"] else " ❌ |") if judged else ""
        if not s["n"]:
            lines.append(f"| {RULES[rule]} | {s['days']} | {s['gap_days']} | 0 | — | — | — | — | — | 0 | — | 0 | — |{mark}")
            continue
        reasons = "、".join(f"{k} {v}" for k, v in s["exit_reasons"].items())
        lines.append(f"| {RULES[rule]} | {s['days']} | {s['gap_days']} | {s['n']} | {s['win_rate']}% | {s['gross_points']:+.1f} | "
                     f"{s['cost_points']:.1f} | {s['net_points']:+.1f} | {s['hold_minutes']:.0f} | {s['daily_mean']:+,.0f} | "
                     f"{s['daily_ci'][0]:+,.0f} ～ {s['daily_ci'][1]:+,.0f} | {s['total_net']:+,.0f} | {reasons} |{mark}")
    return lines


def trade_table(trades):
    lines = ["| 日期 | 規則 | 跳空 | 訊號 | 進場 | 停損 | 出場 | 原因 | 點數 | 淨損益（元） |", "|---|---|---|---|---|---|---|---|---|---|"]
    for t in sorted(trades, key=lambda t: (t["day"], t["rule"])):
        stop = "—" if t["stop_price"] is None else f"{t['stop_price']:,.0f}"
        lines.append(f"| {t['day']} | {RULES[t['rule']]} | {t['gap_pct']:+.2f}% | {t['signal_time']} | {t['entry_time']} {t['entry_price']:,.0f} | "
                     f"{stop} | {t['exit_time']} {t['exit_price']:,.0f} | {t['exit_reason']} | {t['points']:+.0f} | {t['net_pnl']:+,.0f} |")
    return lines


def write_seen_md(out: Path, r, trades):
    gaps = "、".join(f"{d} {g:+.2f}%" for d, g in r["gaps"].items() if g >= GAP_MIN * 100) or "無"
    lines = ["# 開高走低三條規則：期交所 30 天逐筆（已污染，只檢查程式）", "",
             f"**這段每一天都看過，結果不算數**（見 PREREGISTRATION.md）。小台逐筆合成 1 分 K，{r['tested_first']}～{r['tested_last']}，"
             f"有昨收的 {r['tested_days']} 天。", f"開高門檻 +{r['gap_min_pct']:.3f}%；開高日：{gaps}。",
             f"資料不完整而排除：{r['excluded'] or '無'}", "", *cell_table(r["cells"], judged=False), "", "## 每一筆", "", *trade_table(trades)]
    (out / "SEEN.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def write_oos_md(out: Path, r):
    lines = ["# 開高走低三條規則：富邦小台 1 分 K 結果", "",
             f"照 PREREGISTRATION.md 一次跑完。富邦資料 {r['first']}～{r['last']}；判定期 {r['judged_days']} 天"
             f"（{r['judged_first']}～{r['judged_last']}）。開高門檻 +{r['gap_min_pct']:.3f}%。",
             f"富邦缺的交易日 {len(r['missing_vs_calendar'])} 天；資料不完整而排除 {len(r['excluded'])} 天。", "",
             *cell_table(r["cells"]), ""]
    v = r["verdict"]
    lines += [f"**主要判定（A 開高失守）**：{'及格' if v['A_passed'] else '不及格'}。"
              f"B：{'線索' if v['B_lead'] else '不及格'}；C：{'線索' if v['C_lead'] else '不及格'}。"
              f"對照 G（開高就空）：{'及格（A 及格也要算成開高就空本身賺）' if v['G_passed'] else '不及格'}。", "",
              "## 等失守再空 vs 開盤就空（每天 規則 − G）", "", "| 規則 | 每日平均差 | 95% CI |", "|---|---|---|"]
    lines += [f"| {RULES[k]} | {s['daily_mean']:+,.0f} | {s['daily_ci'][0]:+,.0f} ～ {s['daily_ci'][1]:+,.0f} |" for k, s in r["versus_g"].items()]
    for rule in RULES:
        lines += ["", f"## {RULES[rule]} 分年度", "", "| 年 | 天數 | 筆數 | 每日平均 | 合計 |", "|---|---|---|---|---|"]
        lines += [f"| {y} | {s['days']} | {s['n']} | {s['daily_mean']:+,.0f} | {s['total']:+,.0f} |" for y, s in r["yearly"][rule].items()]
        t = r["top_days"][rule]
        lines.append(f"\n最好的 {MB.TOP_DAYS} 天合計 {t['top_days_total']:+,.0f}，其餘合計 {t['rest_total']:+,.0f}（全部 {t['total']:+,.0f}）。")
    lines += ["", "## 2026-10-28 之後（登記之後，只報告）", ""]
    lines += cell_table(r["after"]["cells"], judged=False) if r["after"]["days"] else ["還沒有資料。"]
    lines += ["", "## 敏感度（不列入判定）：停損、停利改成下一根開盤成交", "", *cell_table(r["sensitivity_next_open"], judged=False)]
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
