#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
forward_paper.py — 固定組合「ORB 只做多 + 噪音區只放空」的小台紙上交易（只算 2026-10-22 之後的新交易日）

規則在 2026-10-26 凍結，與 intraday_bidir_test.py 完全相同（直接呼叫同一套函數），不得再改。
每次執行：下載期交所近 30 日逐筆 → 找出還沒記帳、且一般盤已收完的新交易日 → 記入 ledger → 更新 FORWARD.md。

用法：python forward_paper.py --work work_dir --ledger research/paper/ledger.csv --report research/paper/FORWARD.md
"""
from __future__ import annotations

import argparse
import csv
import re
import statistics
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import futures_replay as F  # noqa: E402
import intraday_bidir_test as B  # noqa: E402
import ms_replay as M  # noqa: E402

FROZEN_AFTER = "2026-10-22"
LIST_URL = "https://www.taifex.com.tw/cht/3/dlFutPrevious30DaysSalesData"
ZIP_URL = "https://www.taifex.com.tw/file/taifex/Dailydownload/DailydownloadCSV/{name}"
PRODUCT = "MTX"
FIELDS = ["day", "sleeve", "side", "entry_time", "entry_price", "exit_time", "exit_price", "exit_reason", "points", "net_pnl"]
IN_SAMPLE = {"ORB_LONG": (29, 11, 10039), "NOISE_SHORT": (15, 2, 53643)}


def curl(url: str, path: Path) -> str:
    r = subprocess.run(["curl", "-sS", "-m", "180", "-A", "Mozilla/5.0", "-o", str(path), "-w", "%{http_code}", url],
                       capture_output=True, text=True)
    return r.stdout.strip()


def sync_ticks(work: Path) -> Path:
    zips, csvs = work / "zips", work / "csv"
    zips.mkdir(parents=True, exist_ok=True)
    csvs.mkdir(parents=True, exist_ok=True)
    listing = work / "list.html"
    if curl(LIST_URL, listing) != "200":
        raise SystemExit("期交所近 30 日清單下載失敗")
    names = sorted(set(re.findall(r"Daily_\d{4}_\d{2}_\d{2}\.zip", listing.read_text(encoding="utf-8", errors="replace"))))
    refreshed = set()
    for name in names:
        target = zips / name
        # 最新一天的檔案可能只含夜盤、之後才補一般盤，所以最近 2 個檔每次重抓
        if target.exists() and name not in names[-2:]:
            continue
        if curl(ZIP_URL.format(name=name), target) != "200":
            print(f"  {name} 下載失敗，下次再試", flush=True)
            target.unlink(missing_ok=True)
            continue
        refreshed.add(name)
    for target in sorted(zips.glob("Daily_*.zip")):
        if target.name not in refreshed and (csvs / target.with_suffix(".csv").name).exists():
            continue
        with zipfile.ZipFile(target) as archive:
            for member in archive.namelist():
                base = Path(member).name
                if base.lower().endswith(".csv") and base.startswith("Daily_"):
                    (csvs / base).write_text(only_product(archive.read(member), PRODUCT), encoding="utf-8")
    return csvs


def only_product(raw: bytes, product: str) -> str:
    """整份逐筆約 40MB，只留表頭與該商品的列（約 1/10），省磁碟。"""
    for encoding in ("utf-8-sig", "big5", "cp950"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SystemExit("逐筆檔無法解碼")
    lines = text.splitlines()
    keep = [line for line in lines[1:] if line.count(",") >= 5 and line.split(",", 2)[1].strip() == product]
    return "\n".join([lines[0]] + keep) + "\n"


def load_ledger(path: Path):
    if not path.exists():
        return [], set()
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    return [r for r in rows if r["sleeve"] != "NO_TRADE"], {r["day"] for r in rows}


def session_complete(d: B.Day) -> bool:
    last = M.us_to_dt(d.t[-1])
    return last.strftime("%H:%M") >= "13:25"


def write_report(path: Path, trades, days):
    by = {"ORB_LONG": [t for t in trades if t["sleeve"] == "ORB_LONG"],
          "NOISE_SHORT": [t for t in trades if t["sleeve"] == "NOISE_SHORT"]}
    by["COMBO"] = trades
    lines = ["# 固定組合紙上交易（樣本外、往前走）", "",
             f"規則 2026-10-26 凍結：ORB 只做多 + 噪音區只放空，各 1 口小台；只記 {FROZEN_AFTER} 之後的新交易日。",
             "已扣滑價每邊 1 點、手續費每邊 30 元、期交稅十萬分之二。資料：期交所逐筆成交。", "",
             f"已記帳交易日：{len(days)} 天（{min(days) if days else '—'} ～ {max(days) if days else '—'}）", "",
             "| 子帳戶 | 筆數 | 勝率 | 合計淨損益 | 每日平均 | 挑選期參考（天 / 筆 / 元） |", "|---|---|---|---|---|---|"]
    for key, rows in by.items():
        total = sum(float(t["net_pnl"]) for t in rows)
        win = f"{sum(float(t['net_pnl']) > 0 for t in rows) / len(rows) * 100:.0f}%" if rows else "—"
        ref = IN_SAMPLE.get(key)
        ref_text = f"{ref[0]} / {ref[1]} / {ref[2]:+,}" if ref else "29 / 13 / +63,682"
        lines.append(f"| {key} | {len(rows)} | {win} | {total:+,.0f} | {total / len(days) if days else 0:+,.0f} | {ref_text} |")
    lines += ["", "## 逐日", "", "| 日期 | ORB 只做多 | 噪音區只放空 | 當日合計 | 累計 |", "|---|---|---|---|---|"]
    cumulative = 0.0
    for day in sorted(days):
        orb = sum(float(t["net_pnl"]) for t in by["ORB_LONG"] if t["day"] == day)
        noise = sum(float(t["net_pnl"]) for t in by["NOISE_SHORT"] if t["day"] == day)
        cumulative += orb + noise
        lines.append(f"| {day} | {orb:+,.0f} | {noise:+,.0f} | {orb + noise:+,.0f} | {cumulative:+,.0f} |")
    lines += ["", "樣本太小前不要下結論：至少累積 60 個交易日再看。", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--work", required=True)
    p.add_argument("--ledger", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--after", default=FROZEN_AFTER, help="只記這天之後的交易日（驗證用可改早）")
    args = p.parse_args()
    work, ledger, report = Path(args.work), Path(args.ledger), Path(args.report)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    csvs = sync_ticks(work)
    raw = F.read_taifex(csvs, PRODUCT)
    names = sorted(raw)
    days = []
    for k, name in enumerate(names):
        prev = raw[names[k - 1]]["closes"].get(raw[name]["contract"]) if k else None
        days.append(B.Day(name, raw[name], prev))
    trades, done = load_ledger(ledger)
    new_rows = []
    for k, d in enumerate(days):
        if d.day <= args.after or d.day in done:
            continue
        if not session_complete(d):
            print(f"{d.day} 一般盤資料還不完整，下次再記", flush=True)
            continue
        if k < B.NOISE_LOOKBACK or d.prev_close is None:
            raise SystemExit(f"{d.day} 之前不足 {B.NOISE_LOOKBACK} 天逐筆資料，噪音區無法計算（期交所只保留 30 天，請勿中斷超過兩週）")
        orb, _ = B.run_orb(d)
        noise, _ = B.run_noise(d, days[k - B.NOISE_LOOKBACK:k])
        picked = [("ORB_LONG", t) for t in orb if t["side"] > 0] + [("NOISE_SHORT", t) for t in noise if t["side"] < 0]
        rows = [{"day": d.day, "sleeve": s, **{f: t[f] for f in FIELDS if f in t}} for s, t in picked]
        new_rows += rows or [{"day": d.day, "sleeve": "NO_TRADE"}]
        total = sum(t["net_pnl"] for _, t in picked)
        print(f"{d.day}：{len(picked)} 筆，淨損益 {total:+,.0f} 元", flush=True)
    if new_rows:
        exists = ledger.exists()
        with ledger.open("a", encoding="utf-8", newline="") as handle:
            w = csv.DictWriter(handle, fieldnames=FIELDS)
            if not exists:
                w.writeheader()
            w.writerows(new_rows)
    trades, done = load_ledger(ledger)
    write_report(report, trades, done)
    print(f"已記帳 {len(done)} 天、{len(trades)} 筆", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
