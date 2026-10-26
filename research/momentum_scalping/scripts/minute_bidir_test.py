#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
minute_bidir_test.py — studies/fubon_9y：把 intraday_bidir 的 ORB5、VWAP、NOISE 與固定組合搬到小台 1 分 K（照 PREREGISTRATION.md）

參數、成本、統計（describe、ci95）全部直接 import intraday_bidir_test.py，只有「查價、成交」改成 1 分 K 版。
時間一律用「當天第幾分鐘」（08:45 = 525）；K 棒以開始時間標記。

用法：
  保真度：python minute_bidir_test.py --bars D:/.../fubon_1m --mode fidelity --tick-result ../studies/intraday_bidir/result.json --out D:/.../fubon_9y
  樣本外：python minute_bidir_test.py --bars D:/.../fubon_1m --mode oos --calendar D:/.../tx_daily --out D:/.../fubon_9y
"""
from __future__ import annotations

import argparse
import bisect
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import intraday_bidir_test as B  # noqa: E402
import ms_replay as M  # noqa: E402
import stock_download as S  # noqa: E402

TPE = ZoneInfo("Asia/Taipei")
OPEN_MIN = int(B.SESSION_OPEN[:2]) * 60 + int(B.SESSION_OPEN[3:])
EOD_MIN = int(B.EOD[:2]) * 60 + int(B.EOD[3:])
CLOSE_MIN = 13 * 60 + 45
MIN_BARS = 250                      # 主力合約一天少於這麼多根 K 就當資料不完整（平常 300 根、結算日約 285 根）
PER_BAR_SHARE = 0.99                # average 落在該根高低點之間的比例超過這個值，就當成單根均價
FIDELITY = ("2026-09-09", "2026-10-22")
CLEAN_END, SEEN_END = "2024-10-23", "2026-09-08"
FIDELITY_CORR, FIDELITY_TOTAL = 0.9, 0.10
TOP_DAYS = 5


def hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def load_bars(folder: Path):
    """{day: {contract: {minute: (open, high, low, close, volume, average)}}}，只留 08:45～13:45 的月合約。"""
    out = defaultdict(lambda: defaultdict(dict))
    for path in sorted(folder.glob("MXF_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        default_contract = str(payload.get("contractMonth") or "")
        for c in payload.get("data") or []:
            ts = datetime.fromisoformat(c["date"])
            if ts.tzinfo is None:
                raise ValueError(f"{path.name} 的時間沒有時區：{c['date']}")
            ts = ts.astimezone(TPE)
            minute = ts.hour * 60 + ts.minute
            contract = str(c.get("contractMonth") or default_contract)
            if not (len(contract) == 6 and contract.isdigit()) or not OPEN_MIN <= minute <= CLOSE_MIN:
                continue
            bar = (float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"]), float(c.get("volume") or 0),
                   None if c.get("average") is None else float(c["average"]))
            day = ts.date().isoformat()
            old = out[day][contract].get(minute)
            if old is not None and old != bar:
                raise ValueError(f"{day} {contract} {hhmm(minute)} 兩個檔案的 K 不一樣：{old} vs {bar}")
            out[day][contract][minute] = bar
    return out


def average_kind(raw, days) -> str:
    inside = total = 0
    for day in days:
        for bars in raw.get(day, {}).values():
            for o, h, low, c, v, avg in bars.values():
                if avg is not None:
                    total += 1
                    inside += low <= avg <= h
    if not total:
        return "typical"
    return "per_bar" if inside / total > PER_BAR_SHARE else "cumulative"


class MinuteDay:
    """一天的主力合約 1 分 K；所有查價只看 T 之前已經收完的 K。"""

    def __init__(self, day, contract, bars, prev_close, vwap_kind):
        self.day, self.contract, self.prev_close = day, contract, prev_close
        self.m = sorted(bars)
        self.o, self.h, self.l, self.c, self.v, self.a = ([bars[k][j] for k in self.m] for j in range(6))
        self.open = self.o[0]
        self.vwap, pv, vol = [], 0.0, 0.0
        for o, h, low, c, v, avg in (bars[k] for k in self.m):
            if vwap_kind == "cumulative":
                self.vwap.append(avg)
                continue
            price = avg if vwap_kind == "per_bar" else (h + low + c) / 3
            pv, vol = pv + price * v, vol + v
            self.vwap.append(pv / vol if vol else price)
        # 結算日近月合約 13:30 就收：在它最後一根 K 平倉（逐筆版 Day.eod 的 min(13:44, 最後一筆那分鐘)）
        self.eod = min(EOD_MIN, self.m[-1])

    def at(self, minutes_after_open: int) -> int:
        return OPEN_MIN + minutes_after_open

    def last_before(self, minute: int) -> int:
        i = bisect.bisect_left(self.m, minute) - 1
        if i < 0:
            raise ValueError(f"{self.day} {hhmm(minute)} 之前沒有 K")
        return i

    def price_at(self, minute: int) -> float:
        return self.c[self.last_before(minute)]

    def fill(self, minute: int, side: int):
        """minute 那一刻送出市價單：從那分鐘開始的第一根 K 的開盤價，買 +1 點、賣 −1 點。"""
        i = bisect.bisect_left(self.m, minute)
        if i >= len(self.m):
            raise ValueError(f"{self.day} {hhmm(minute)} 之後沒有 K 可以成交")
        return self.m[i], self.o[i] + side * B.SLIP, i


def trade(d: MinuteDay, rule, side, signal_t, entry, exit_, reason, stop=None):
    (et, ep), (xt, xp) = entry, exit_
    points = side * (xp - ep)
    cost = 2 * B.COMMISSION + B.TAX * (ep + xp) * B.POINT_VALUE
    return {"day": d.day, "rule": rule, "side": side, "contract": d.contract, "signal_time": hhmm(signal_t),
            "entry_time": hhmm(et), "entry_price": ep, "exit_time": hhmm(xt), "exit_price": xp, "exit_reason": reason,
            "stop_price": stop, "points": round(points, 2), "gross_points": round(points + 2 * B.SLIP, 2),
            "cost": round(cost, 1), "net_pnl": round(points * B.POINT_VALUE - cost, 1), "hold_minutes": xt - et}


def run_orb(d: MinuteDay, stop_fill: str = "level"):
    end = d.at(B.ORB_MINUTES)
    k = d.last_before(end) + 1
    side = 1 if d.c[k - 1] > d.o[0] else -1 if d.c[k - 1] < d.o[0] else 0
    if not side:
        return []
    stop = min(d.l[:k]) if side > 0 else max(d.h[:k])
    et, ep, ei = d.fill(end, side)
    risk = side * (ep - stop)
    if risk <= 0:
        return []
    target = ep + side * B.ORB_TARGET_R * risk
    for j in range(ei, len(d.m)):
        if d.m[j] >= d.eod:
            break
        worst, best = (d.l[j], d.h[j]) if side > 0 else (d.h[j], d.l[j])
        if side * (worst - stop) <= 0:
            reason, level = "STOP", stop
        elif side * (best - target) >= 0:
            reason, level = "TARGET", target
        else:
            continue
        if stop_fill == "next_open":
            xt, xp, _ = d.fill(d.m[j] + 1, -side)
        else:
            # 碰到價位就出場；開盤就已經越過價位時，用開盤價
            crossed = side * (d.o[j] - level) <= 0 if reason == "STOP" else side * (d.o[j] - level) >= 0
            xt, xp = d.m[j], (d.o[j] if crossed else level) - side * B.SLIP
        return [trade(d, "ORB5", side, end, (et, ep), (xt, xp), reason, stop)]
    xt, xp, _ = d.fill(d.eod, -side)
    return [trade(d, "ORB5", side, end, (et, ep), (xt, xp), "EOD", stop)]


def run_targets(d: MinuteDay, rule, decisions):
    """decisions = [(minute, 目標部位 +1/0/−1)]；部位變就平倉／反手，收盤前全平。與 B.run_targets 相同。"""
    trades, pos, entry, signal_t = [], 0, None, None
    for t, target in decisions:
        if target == pos:
            continue
        if pos:
            xt, xp, _ = d.fill(t, -pos)
            trades.append(trade(d, rule, pos, signal_t, entry, (xt, xp), "FLIP" if target else "EXIT"))
        if target:
            et, ep, _ = d.fill(t, target)
            entry, signal_t = (et, ep), t
        pos = target
    if pos:
        xt, xp, _ = d.fill(d.eod, -pos)
        trades.append(trade(d, rule, pos, signal_t, entry, (xt, xp), "EOD"))
    return trades


def run_vwap(d: MinuteDay):
    decisions, pos, minute = [], 0, 1
    while d.at(minute) < d.eod:
        i = d.last_before(d.at(minute))
        pos = 1 if d.c[i] > d.vwap[i] else -1 if d.c[i] < d.vwap[i] else pos
        decisions.append((d.at(minute), pos))
        minute += 1
    return run_targets(d, "VWAP", decisions)


def run_noise(d: MinuteDay, history):
    if d.prev_close is None:
        raise ValueError(f"{d.day} 沒有昨收")
    sigma = {m: statistics.fmean(abs(h.price_at(h.at(m)) / h.open - 1) for h in history) for m in B.NOISE_CHECKS}
    up_base, down_base = max(d.open, d.prev_close), min(d.open, d.prev_close)
    decisions = []
    for m in B.NOISE_CHECKS:
        t = d.at(m)
        if t >= d.eod:
            break
        i = d.last_before(t)
        ub, lb = up_base * (1 + sigma[m]), down_base * (1 - sigma[m])
        price, vwap = d.c[i], d.vwap[i]
        decisions.append((t, 1 if price > max(ub, vwap) else -1 if price < min(lb, vwap) else 0))
    return run_targets(d, "NOISE", decisions)


def build_days(raw, vwap_kind, calendar_days=None):
    """主力合約、昨收、資料不完整的日子（排除並回傳原因）。"""
    names = sorted(raw)
    prev_of = {}
    if calendar_days is not None:
        cal = sorted(set(calendar_days) | set(names))
        prev_of = {day: cal[k - 1] for k, day in enumerate(cal) if k}
    days, excluded = [], {}
    for k, day in enumerate(names):
        contract = max(raw[day], key=lambda c: sum(b[4] for b in raw[day][c].values()))
        bars = raw[day][contract]
        prev_day = names[k - 1] if k else None
        if calendar_days is not None and k and prev_of.get(day) != prev_day:
            excluded[day] = f"前一個交易日 {prev_of.get(day)} 富邦沒有資料"
            continue
        if min(bars) >= OPEN_MIN + B.ORB_MINUTES:
            excluded[day] = f"主力 {contract} 08:50 前沒有 K"
            continue
        if len(bars) < MIN_BARS:
            excluded[day] = f"主力 {contract} 只有 {len(bars)} 根 K"
            continue
        prev_bars = raw[prev_day].get(contract) if prev_day else None
        if prev_day and not prev_bars:
            raise ValueError(f"{day} 主力 {contract} 前一天 {prev_day} 沒有 K，算不出昨收")
        prev_close = prev_bars[max(prev_bars)][3] if prev_bars else None
        days.append(MinuteDay(day, contract, bars, prev_close, vwap_kind))
    return days, excluded


def run_all(days, tested, stop_fill="level"):
    """tested 裡的每一天跑三條規則；NOISE 用前 14 個（有效）交易日。"""
    index = {d.day: k for k, d in enumerate(days)}
    trades = []
    for day in tested:
        k = index[day]
        d = days[k]
        trades += run_orb(d, stop_fill)
        trades += run_vwap(d)
        if k < B.NOISE_LOOKBACK:
            raise ValueError(f"{day} 前面不足 {B.NOISE_LOOKBACK} 天，不能測 NOISE")
        trades += run_noise(d, days[k - B.NOISE_LOOKBACK:k])
    return trades


def combo(trades):
    return ([{**t, "rule": "ORB_LONG"} for t in trades if t["rule"] == "ORB5" and t["side"] > 0]
            + [{**t, "rule": "NOISE_SHORT"} for t in trades if t["rule"] == "NOISE" and t["side"] < 0])


def cells(trades, tested):
    out = {}
    for rule in B.RULES:
        rows = [t for t in trades if t["rule"] == rule]
        for key, pick in (("both", rows), ("long", [t for t in rows if t["side"] > 0]),
                          ("short", [t for t in rows if t["side"] < 0])):
            s = B.describe(pick, tested)
            s.pop("cumulative")
            s["exit_reasons"] = dict(sorted({r: sum(t["exit_reason"] == r for t in pick)
                                             for r in {t["exit_reason"] for t in pick}}.items()))
            out[f"{rule}/{key}"] = s
    rows = combo(trades)
    s = B.describe(rows, tested)
    s.pop("cumulative")
    out["COMBO"] = s
    return out


def daily_pnl(trades, days):
    per_day = {d: 0.0 for d in days}
    for t in trades:
        per_day[t["day"]] += t["net_pnl"]
    return per_day


def yearly(trades, tested):
    per_day = daily_pnl(trades, tested)
    out = {}
    for year in sorted({d[:4] for d in tested}):
        vals = [v for d, v in per_day.items() if d[:4] == year]
        out[year] = {"days": len(vals), "n": sum(t["day"][:4] == year for t in trades),
                     "daily_mean": round(statistics.fmean(vals), 0), "total": round(sum(vals), 0)}
    return out


def top_share(trades, tested):
    vals = sorted(daily_pnl(trades, tested).values(), reverse=True)
    total = sum(vals)
    return {"top_days_total": round(sum(vals[:TOP_DAYS]), 0), "total": round(total, 0),
            "rest_total": round(total - sum(vals[:TOP_DAYS]), 0)}


def segment(trades, ds):
    if not ds:
        return {"days": 0}
    keep = set(ds)
    s = B.describe([t for t in trades if t["day"] in keep], ds)
    s.pop("cumulative")
    return s


def fidelity(raw, args, vwap_kind):
    tick = json.loads(Path(args.tick_result).read_text(encoding="utf-8"))
    names = sorted(raw)
    days, excluded = build_days(raw, vwap_kind)
    by_day = {d.day: k for k, d in enumerate(days)}
    report = {"vwap_kind": vwap_kind, "excluded": {d: r for d, r in excluded.items() if FIDELITY[0] <= d <= FIDELITY[1]},
              "rules": {}}
    all_trades = []
    for rule, info in tick["rules"].items():
        tested = info["days"]
        missing = [d for d in tested if d not in by_day]
        if missing:
            raise ValueError(f"保真度檢查缺日子：{missing}（富邦有 {names[0]}～{names[-1]}）")
        trades = []
        for day in tested:
            if rule == "NOISE" and by_day[day] < B.NOISE_LOOKBACK:
                raise ValueError(f"{day} 前面不足 {B.NOISE_LOOKBACK} 天，不能測 NOISE")
            d = days[by_day[day]]
            trades += (run_orb(d) if rule == "ORB5" else run_vwap(d) if rule == "VWAP"
                       else run_noise(d, days[by_day[day] - B.NOISE_LOOKBACK:by_day[day]]))
        all_trades += trades
        cum = info["both"]["cumulative"]
        tick_daily = [cum[0]] + [cum[k] - cum[k - 1] for k in range(1, len(cum))]
        mine = daily_pnl(trades, tested)
        minute_daily = [mine[d] for d in tested]
        corr = statistics.correlation(tick_daily, minute_daily)
        gap = abs(sum(minute_daily) - sum(tick_daily))
        scale = sum(abs(x) for x in tick_daily)
        report["rules"][rule] = {
            "days": len(tested), "corr": round(corr, 3), "tick_total": round(sum(tick_daily), 0),
            "minute_total": round(sum(minute_daily), 0), "gap_share": round(gap / scale, 3),
            "n_tick": {s: info[s]["n"] for s in ("both", "long", "short")},
            "n_minute": {"both": len(trades), "long": sum(t["side"] > 0 for t in trades),
                         "short": sum(t["side"] < 0 for t in trades)},
            "passed": corr >= FIDELITY_CORR and gap <= FIDELITY_TOTAL * scale,
            "daily": [[d, round(a, 0), round(b, 0)] for d, a, b in zip(tested, tick_daily, minute_daily)]}
    report["passed"] = all(r["passed"] for r in report["rules"].values())
    return report, all_trades


def oos(raw, args, vwap_kind):
    calendar_days = S.calendar(args.calendar, "2017-01-01", "2099-12-31")
    names = sorted(raw)
    calendar_days = [d for d in calendar_days if names[0] <= d <= names[-1]]
    days, excluded = build_days(raw, vwap_kind, calendar_days)
    valid = [d.day for d in days]
    tested = [d for d in valid[B.NOISE_LOOKBACK:] if not FIDELITY[0] <= d <= FIDELITY[1]]
    trades = run_all(days, tested)
    sens = run_all(days, tested, stop_fill="next_open")
    noise_short = [t for t in trades if t["rule"] == "NOISE" and t["side"] < 0]
    segments = {"clean": [d for d in tested if d <= CLEAN_END],
                "seen": [d for d in tested if CLEAN_END < d <= SEEN_END],
                "after": [d for d in tested if d > FIDELITY[1]]}
    result = {
        "vwap_kind": vwap_kind, "first": names[0], "last": names[-1], "tested_days": len(tested),
        "tested_first": tested[0], "tested_last": tested[-1],
        "missing_vs_calendar": [d for d in calendar_days if d not in raw], "excluded": excluded,
        "cells": cells(trades, tested),
        "yearly": {"COMBO": yearly(combo(trades), tested), "NOISE/short": yearly(noise_short, tested)},
        "top_days": {"COMBO": top_share(combo(trades), tested), "NOISE/short": top_share(noise_short, tested)},
        "noise_short_segments": {k: segment(noise_short, ds) for k, ds in segments.items()},
        "sensitivity_next_open": {k: v for k, v in cells(sens, tested).items() if k.startswith("ORB5") or k == "COMBO"},
    }
    primary = result["cells"]["COMBO"]
    secondary = result["cells"]["NOISE/short"]
    clean = result["noise_short_segments"]["clean"]
    result["verdict"] = {"COMBO_passed": primary["passed"],
                         "NOISE_short_passed": secondary["passed"] and clean.get("daily_mean", 0) > 0}
    return result, trades


def write_fidelity_md(out: Path, report):
    lines = ["# 富邦 1 分 K 保真度檢查（2026-09-09～10-22，對照逐筆版）", "",
             f"VWAP 用法：{report['vwap_kind']}；排除的日子：{report['excluded'] or '無'}", "",
             "| 規則 | 天數 | 逐日相關 | 逐筆版合計 | 1 分 K 版合計 | 差距 ÷ 逐日絕對值總和 | 筆數 逐筆／1 分 K（多空都做, 多, 空） | 通過 |",
             "|---|---|---|---|---|---|---|---|"]
    for rule, r in report["rules"].items():
        nt, nm = r["n_tick"], r["n_minute"]
        lines.append(f"| {rule} | {r['days']} | {r['corr']:.3f} | {r['tick_total']:+,.0f} | {r['minute_total']:+,.0f} | "
                     f"{r['gap_share']:.1%} | {nt['both']}/{nt['long']}/{nt['short']} vs {nm['both']}/{nm['long']}/{nm['short']} | "
                     f"{'✅' if r['passed'] else '❌'} |")
    lines += ["", f"門檻：相關 ≥ {FIDELITY_CORR}，差距 ≤ {FIDELITY_TOTAL:.0%}。全部通過：{'是' if report['passed'] else '否'}", ""]
    (out / "FIDELITY.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


def write_oos_md(out: Path, r):
    lines = ["# 小台 1 分 K 九年重測：結果", "",
             f"照 PREREGISTRATION.md 一次跑完。富邦資料 {r['first']}～{r['last']}；樣本外 {r['tested_days']} 天"
             f"（{r['tested_first']}～{r['tested_last']}，排除 2026-09-09～10-22）。VWAP 用法：{r['vwap_kind']}。",
             f"富邦缺的交易日 {len(r['missing_vs_calendar'])} 天；資料不完整而排除 {len(r['excluded'])} 天。", "",
             "| 格 | 天數 | 筆數 | 勝率 | 每筆毛點數 | 每筆成本點數 | 每筆淨點數 | 每日平均淨損益 | 以天 95% CI | 合計淨損益 | 達標 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, s in r["cells"].items():
        if not s["n"]:
            lines.append(f"| {name} | {s['days']} | 0 | — | — | — | — | 0 | — | 0 | ❌ |")
            continue
        lines.append(f"| {name} | {s['days']} | {s['n']} | {s['win_rate']}% | {s['gross_points']:+.1f} | {s['cost_points']:.1f} | "
                     f"{s['net_points']:+.1f} | {s['daily_mean']:+,.0f} | {s['daily_ci'][0]:+,.0f} ～ {s['daily_ci'][1]:+,.0f} | "
                     f"{s['total_net']:+,.0f} | {'✅' if s['passed'] else '❌'} |")
    v = r["verdict"]
    lines += ["", f"**主要判定（固定組合）**：{'及格' if v['COMBO_passed'] else '不及格'}。"
              f"**次要（噪音區只放空，含乾淨段每日平均 > 0）**：{'站得住' if v['NOISE_short_passed'] else '站不住'}。", "",
              "## 噪音區只放空：分段", "", "| 段 | 天數 | 筆數 | 每日平均 | 95% CI | 合計 |", "|---|---|---|---|---|---|"]
    for k, label in (("clean", "乾淨段 ～2024-10-23"), ("seen", "現貨代理看過 2024-10-24～2026-09-08"), ("after", "2026-10-23 之後")):
        s = r["noise_short_segments"][k]
        if not s.get("days"):
            lines.append(f"| {label} | 0 | — | — | — | — |")
            continue
        lines.append(f"| {label} | {s['days']} | {s['n']} | {s['daily_mean']:+,.0f} | "
                     f"{s['daily_ci'][0]:+,.0f} ～ {s['daily_ci'][1]:+,.0f} | {s['total_net']:+,.0f} |")
    for key in ("COMBO", "NOISE/short"):
        lines += ["", f"## {key} 分年度", "", "| 年 | 天數 | 筆數 | 每日平均 | 合計 |", "|---|---|---|---|---|"]
        lines += [f"| {y} | {s['days']} | {s['n']} | {s['daily_mean']:+,.0f} | {s['total']:+,.0f} |" for y, s in r["yearly"][key].items()]
        t = r["top_days"][key]
        lines.append(f"\n最好的 {TOP_DAYS} 天合計 {t['top_days_total']:+,.0f}，其餘合計 {t['rest_total']:+,.0f}（全部 {t['total']:+,.0f}）。")
    lines += ["", "## 敏感度（不列入判定）：ORB 停損、停利改成下一根開盤成交", "", "| 格 | 筆數 | 每日平均 | 95% CI | 合計 |", "|---|---|---|---|---|"]
    lines += [f"| {k} | {s['n']} | {s['daily_mean']:+,.0f} | {s['daily_ci'][0]:+,.0f} ～ {s['daily_ci'][1]:+,.0f} | {s['total_net']:+,.0f} |"
              for k, s in r["sensitivity_next_open"].items()]
    (out / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--bars", required=True, help="fubon_minute_download.py 的輸出資料夾")
    p.add_argument("--mode", choices=["fidelity", "oos"], required=True)
    p.add_argument("--tick-result", help="intraday_bidir/result.json（保真度用）")
    p.add_argument("--calendar", help="期交所 TX 日資料資料夾（樣本外用，對照缺的交易日）")
    p.add_argument("--out", required=True)
    args = p.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    raw = load_bars(Path(args.bars))
    if not raw:
        raise SystemExit(f"{args.bars} 沒有任何 K")
    vwap_kind = average_kind(raw, [d for d in raw if FIDELITY[0] <= d <= FIDELITY[1]])
    if args.mode == "fidelity":
        if not args.tick_result:
            raise SystemExit("保真度要給 --tick-result")
        report, trades = fidelity(raw, args, vwap_kind)
        (out / "fidelity.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        M.write_csv(out / "fidelity_trades.csv", trades)
        write_fidelity_md(out, report)
        return 0 if report["passed"] else 1
    if not args.calendar:
        raise SystemExit("樣本外要給 --calendar")
    result, trades = oos(raw, args, vwap_kind)
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    M.write_csv(out / "trades.csv", trades)
    write_oos_md(out, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
