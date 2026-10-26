#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_download.py — 逐日下載上市／上櫃的每日行情與現股當沖標的（測試 A 用），可中斷續傳。

用法：python stock_download.py --market twse --start 2015-12-30 --end 2026-10-22 --calendar tx_daily --out stock_raw
"""
from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
import time
from pathlib import Path


def calendar(folder: str, start: str, end: str):
    days = set()
    for path in glob.glob(f"{folder}/TX_*.csv"):
        for line in open(path, "rb").read().decode("big5", "replace").splitlines()[1:]:
            cells = line.split(",")
            if len(cells) > 17 and cells[17].strip() == "一般":
                day = cells[0].strip().replace("/", "-")
                if start <= day <= end:
                    days.add(day)
    return sorted(days)


def urls(market: str, day: str):
    y, m, d = day.split("-")
    if market == "twse":
        return {"quotes": f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={y}{m}{d}&type=ALLBUT0999&response=json",
                "daytrade": f"https://www.twse.com.tw/rwd/zh/dayTrading/TWTB4U?date={y}{m}{d}&response=json"}
    roc = f"{int(y) - 1911}/{m}/{d}"
    return {"quotes": f"https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={y}%2F{m}%2F{d}&id=&response=json",
            "daytrade": f"https://www.tpex.org.tw/web/stock/trading/intraday_trading/intraday_trading_list_result.php?l=zh-tw&d={roc}"}


def fetch(url: str, path: Path, pause: float) -> str:
    for attempt in range(6):
        tmp = path.with_suffix(".part")
        r = subprocess.run(["curl", "-sS", "-m", "60", "-A", "Mozilla/5.0", "-o", str(tmp), "-w", "%{http_code}", url],
                           capture_output=True, text=True)
        time.sleep(pause)
        code = r.stdout.strip()
        if code == "200":
            try:
                payload = json.loads(tmp.read_bytes())
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                tmp.replace(path)
                return str(payload.get("stat", ""))[:20]
        wait = 60 * (attempt + 1)
        print(f"  retry {path.name} code={code} wait {wait}s", flush=True)
        time.sleep(wait)
    raise SystemExit(f"下載失敗：{url}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--market", choices=["twse", "tpex"], required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--calendar", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--pause", type=float, default=2.2)
    args = p.parse_args()
    out = Path(args.out) / args.market
    out.mkdir(parents=True, exist_ok=True)
    days = calendar(args.calendar, args.start, args.end)
    print(f"{args.market}: {len(days)} 個交易日", flush=True)
    for k, day in enumerate(days, 1):
        for kind, url in urls(args.market, day).items():
            path = out / f"{day}_{kind}.json"
            if path.exists():
                continue
            stat = fetch(url, path, args.pause)
            if kind == "quotes" and stat.lower() not in {"ok", ""}:
                print(f"  {day} {kind} stat={stat}", flush=True)
        if k % 100 == 0:
            print(f"{args.market} {k}/{len(days)} {day}", flush=True)
    print(f"{args.market} done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
