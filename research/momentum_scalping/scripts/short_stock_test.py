#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
short_stock_test.py — 測試 A：個股過熱池「隔天開盤放空、收盤回補」（照 short_stock/PREREGISTRATION.md）

用法：python short_stock_test.py --raw stock_raw --calendar tx_daily --out short_stock
"""
from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stock_download as D  # noqa: E402

CODE = re.compile(r"^[1-9]\d{3}$")
LIMIT_MIN, LIMIT_MAX = 0.095, 0.105
MIN_PRICE, MIN_VOLUME = 10.0, 500_000  # 原 repo Scanner 預設
COST = 0.001425 * 0.28 * 2 + 0.0015 + 0.0005 * 2
LOCK_PENALTY = 0.07
GAP_UP, GAP_BIG = 0.02, 0.05
SPLIT, START, END = "2022-01-01", "2016-01-01", "2026-10-22"


def num(text):
    text = re.sub(r"<[^>]+>", "", str(text)).replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def twse_quotes(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for table in payload.get("tables") or []:
        fields = table.get("fields") or []
        if "證券代號" not in fields or "收盤價" not in fields:
            continue
        i = {f: k for k, f in enumerate(fields)}
        for row in table.get("data") or []:
            code = row[i["證券代號"]].strip()
            if not CODE.match(code):
                continue
            sign_text = re.sub(r"<[^>]+>", "", row[i["漲跌(+/-)"]]).strip()
            change = num(row[i["漲跌價差"]])
            o, h, low, c, v = (num(row[i[k]]) for k in ("開盤價", "最高價", "最低價", "收盤價", "成交股數"))
            if None in (o, h, low, c, v, change) or sign_text not in {"+", "-", ""}:
                continue  # 沒成交、或「X 不比價」（參考價特殊）
            ref = c - (change if sign_text == "+" else -change if sign_text == "-" else 0.0)
            out[code] = {"open": o, "high": h, "low": low, "close": c, "volume": v, "ref": ref}
    return out


def tpex_quotes(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for table in payload.get("tables") or []:
        fields = [f.replace(" ", "") for f in table.get("fields") or []]
        if "代號" not in fields or "收盤" not in fields:
            continue
        i = {f: k for k, f in enumerate(fields)}
        for row in table.get("data") or []:
            code = str(row[i["代號"]]).strip()
            if not CODE.match(code):
                continue
            o, h, low, c, v, change = (num(row[i[k]]) for k in ("開盤", "最高", "最低", "收盤", "成交股數", "漲跌"))
            if None in (o, h, low, c, v, change):
                continue  # 沒成交、或除權息等文字
            out[code] = {"open": o, "high": h, "low": low, "close": c, "volume": v, "ref": c - change}
    return out


def eligibility(path: Path, market: str):
    """回傳 {代號: 當沖成交股數 or None}；名單上且沒有「暫停先賣後買」註記才算可先賣。"""
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for table in payload.get("tables") or []:
        fields = table.get("fields") or []
        if "證券代號" not in fields or not any("暫停" in f for f in fields):
            continue
        i = {f: k for k, f in enumerate(fields)}
        flag = next(f for f in fields if "暫停" in f)
        shares = next((f for f in fields if "成交股數" in f), None)
        for row in table.get("data") or []:
            code = str(row[i["證券代號"]]).strip()
            if CODE.match(code) and not str(row[i[flag]]).strip():
                out[code] = num(row[i[shares]]) if shares else None
    return out


def limit_close(bar) -> bool:
    r = bar["close"] / bar["ref"] - 1
    return LIMIT_MIN <= r <= LIMIT_MAX and bar["close"] >= bar["high"]


def build_events(raw: Path, days):
    events, missing = [], defaultdict(int)
    prev = {}
    for k, day in enumerate(days):
        cur = {}
        for market, reader in (("twse", twse_quotes), ("tpex", tpex_quotes)):
            qp = raw / market / f"{day}_quotes.json"
            if not qp.exists():
                missing[f"{market}_quotes"] += 1
                continue
            quotes = reader(qp)
            elig = eligibility(raw / market / f"{day}_daytrade.json", market)
            if elig is None:
                missing[f"{market}_daytrade"] += 1
            for code, bar in quotes.items():
                cur[(market, code)] = {**bar, "eligible": None if elig is None else code in elig,
                                       "dt_shares": None if elig is None else elig.get(code)}
        if k and START <= day <= END:
            for key, bar in cur.items():
                y = prev.get(key)
                if not y or y["ref"] <= 0 or bar["ref"] <= 0:
                    continue
                y_ret = y["close"] / y["ref"] - 1
                if y_ret > LIMIT_MAX or y["close"] < MIN_PRICE or y["volume"] < MIN_VOLUME:
                    continue  # 參考價特殊（減資、新股）或流動性不足
                gap = bar["open"] / bar["ref"] - 1
                if gap >= LIMIT_MIN or bar["eligible"] is not True:
                    continue
                locked = limit_close(bar)
                gross = (bar["open"] - bar["close"]) / bar["open"]
                events.append({
                    "day": day, "market": key[0], "code": key[1], "y_limit": limit_close(y), "y_ret": y_ret, "gap": gap,
                    "y_dt_ratio": (y["dt_shares"] / y["volume"]) if y.get("dt_shares") is not None and y["volume"] else None,
                    "locked": locked, "gross": gross,
                    "net": gross - COST - (LOCK_PENALTY if locked else 0.0), "net_no_penalty": gross - COST})
        prev = cur
    return events, dict(missing)


def cluster_ci(trades, key="net", iterations=2000, seed=7):
    by_day = defaultdict(list)
    for t in trades:
        by_day[t["day"]].append(t[key])
    days = list(by_day)
    rng = random.Random(seed)
    means = []
    for _ in range(iterations):
        pick = [x for d in rng.choices(days, k=len(days)) for x in by_day[d]]
        means.append(statistics.fmean(pick))
    means.sort()
    return means[int(0.025 * iterations)], means[int(0.975 * iterations) - 1]


def describe(trades):
    if not trades:
        return {"n": 0}
    lo, hi = cluster_ci(trades)
    return {"n": len(trades), "days": len({t["day"] for t in trades}),
            "win_rate": round(sum(t["net"] > 0 for t in trades) / len(trades) * 100, 1),
            "mean_net_pct": round(statistics.fmean(t["net"] for t in trades) * 100, 3),
            "ci_low": round(lo * 100, 3), "ci_high": round(hi * 100, 3),
            "mean_gross_pct": round(statistics.fmean(t["gross"] for t in trades) * 100, 3),
            "mean_no_penalty_pct": round(statistics.fmean(t["net_no_penalty"] for t in trades) * 100, 3),
            "locked_pct": round(sum(t["locked"] for t in trades) / len(trades) * 100, 2)}


def passes(s) -> bool:
    return s["n"] >= 100 and s["mean_net_pct"] > 0 and s["ci_low"] > 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--raw", required=True)
    p.add_argument("--calendar", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    out = Path(args.out)
    days = D.calendar(args.calendar, "2015-12-30", END)
    events, missing = build_events(Path(args.raw), days)
    a1_explore_twse = [e["y_dt_ratio"] for e in events
                       if e["y_limit"] and e["market"] == "twse" and e["day"] < SPLIT and e["y_dt_ratio"] is not None]
    dt_median = statistics.median(a1_explore_twse) if a1_explore_twse else None
    rules = {
        "A1 漲停隔日（主要）": lambda e: e["y_limit"],
        "A2 漲停隔日且開高 ≥2%": lambda e: e["y_limit"] and e["gap"] >= GAP_UP,
        "A3 漲停隔日且開平盤以下": lambda e: e["y_limit"] and e["gap"] <= 0,
        "A4 非漲停隔日、開高 5%～9.5%": lambda e: not e["y_limit"] and e["gap"] >= GAP_BIG,
        "A5 漲停隔日、上市、前日當沖比 ≥ 中位數": lambda e: (e["y_limit"] and e["market"] == "twse" and dt_median is not None
                                                 and e["y_dt_ratio"] is not None and e["y_dt_ratio"] >= dt_median),
    }
    result = {"cost_pct": round(COST * 100, 3), "dt_ratio_median": dt_median, "missing_files": missing,
              "events": len(events), "rules": {}}
    for name, rule in rules.items():
        picked = [e for e in events if rule(e)]
        ex = [e for e in picked if e["day"] < SPLIT]
        va = [e for e in picked if e["day"] >= SPLIT]
        s = describe(va)
        yearly = defaultdict(list)
        for e in va:
            yearly[e["day"][:4]].append(e["net"])
        result["rules"][name] = {
            "explore": describe(ex), "validate": s,
            "validate_twse": describe([e for e in va if e["market"] == "twse"]),
            "validate_tpex": describe([e for e in va if e["market"] == "tpex"]),
            "yearly": {y: (len(v), round(statistics.fmean(v) * 100, 3)) for y, v in sorted(yearly.items())}}
        # 補充（2026-10-26 09:21）：主要判定改用上櫃；A5 只有上市。合併結果照報，只當參考。
        judged_on = "validate_twse" if name.startswith("A5") else "validate_tpex"
        result["rules"][name]["judged_on"] = judged_on
        result["rules"][name]["passed"] = passes(result["rules"][name][judged_on])
        result["rules"][name]["passed_combined"] = passes(s)
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# 測試 A：個股過熱池「隔天開盤放空、收盤回補」", "",
             f"照 PREREGISTRATION.md 一次跑完。每筆成本 {result['cost_pct']}%；收盤鎖漲停時另扣 7%（回補不到）。",
             f"缺檔：{missing or '無'}。A5 的當沖比門檻（探索期中位數）：{dt_median}",
             "及格與否：A1～A4 看驗證期**上櫃**，A5 看驗證期**上市**（見 PREREGISTRATION.md 補充）；表中「合併」只當參考。", "",
             "| 規則 | 探索期 n / 平均淨% | 驗證期合併 n（天數）/ 勝率 / 平均淨% | 合併 95% CI（以天群集） | 不扣 7% 時 | 收盤鎖漲停比例 | 上市 / 上櫃 平均淨% | 判定用的那一段 n / 平均淨% / 95% CI | 及格 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for name, r in result["rules"].items():
        e, v, tw, tp = r["explore"], r["validate"], r["validate_twse"], r["validate_tpex"]
        j = r[r["judged_on"]]
        judged = (f"{'上市' if r['judged_on'] == 'validate_twse' else '上櫃'} {j['n']} / {j['mean_net_pct']:+.2f} / "
                  f"{j['ci_low']:+.2f} ～ {j['ci_high']:+.2f}") if j["n"] else "0"
        if not v["n"]:
            lines.append(f"| {name} | {e['n']} | 0 | — | — | — | — | {judged} | ❌ |")
            continue
        lines.append(f"| {name} | {e['n']} / {e.get('mean_net_pct', 0):+.2f} | {v['n']}（{v['days']}）/ {v['win_rate']}% / {v['mean_net_pct']:+.2f} | "
                     f"{v['ci_low']:+.2f} ～ {v['ci_high']:+.2f} | {v['mean_no_penalty_pct']:+.2f} | {v['locked_pct']}% | "
                     f"{tw.get('mean_net_pct', float('nan')):+.2f}（{tw['n']}）/ {tp.get('mean_net_pct', float('nan')):+.2f}（{tp['n']}） | "
                     f"{judged} | {'✅' if r['passed'] else '❌'} |")
    lines += ["", "## 驗證期分年度（筆數, 平均淨%）", ""]
    for name, r in result["rules"].items():
        lines.append(f"- {name}：" + "；".join(f"{y} {n} 筆 {m:+.2f}" for y, (n, m) in r["yearly"].items()))
    (out / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
