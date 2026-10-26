#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_tx_daily.py — 下載期交所「期貨每日交易行情」TX 月檔（Big5 CSV），可中斷續傳。

gap_strategy_test.py、futures_chips_test.py、stock_download.py（交易日曆）都讀這個資料夾。
用法：python fetch_tx_daily.py --out tx_daily --start 1998-07 --end 2026-10
"""
from __future__ import annotations

import argparse
import calendar
import subprocess
import time
from datetime import date
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--start", default="1998-07")
    p.add_argument("--end", default=date.today().strftime("%Y-%m"))
    p.add_argument("--product", default="TX")
    args = p.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    y, m = (int(x) for x in args.start.split("-"))
    end = tuple(int(x) for x in args.end.split("-"))
    today = date.today()
    while (y, m) <= end:
        last = min(date(y, m, calendar.monthrange(y, m)[1]), today)
        path = out / f"{args.product}_{y}{m:02d}.csv"
        current_month = (y, m) == (today.year, today.month)
        if current_month or not (path.exists() and path.stat().st_size > 100):
            data = (f"down_type=1&commodity_id={args.product}&commodity_id2=&queryStartDate={y}%2F{m:02d}%2F01"
                    f"&queryEndDate={last.year}%2F{last.month:02d}%2F{last.day:02d}")
            for attempt in range(3):
                r = subprocess.run(["curl", "-sS", "-m", "60", "-A", "Mozilla/5.0", "-o", str(path), "-w", "%{http_code}",
                                    "-X", "POST", "https://www.taifex.com.tw/cht/3/futDataDown", "-d", data],
                                   capture_output=True, text=True)
                if r.stdout.strip() == "200" and path.stat().st_size > 100 and not path.read_bytes().lstrip().startswith(b"<"):
                    break
                time.sleep(5 * (attempt + 1))
            else:
                print(f"{path.name} 下載失敗", flush=True)
            time.sleep(1.0)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
