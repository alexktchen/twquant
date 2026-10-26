#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_futures_chips.py — 測試 B 的籌碼資料：大額交易人（臺股期貨）、三大法人（TXF／MXF）、小台日行情未平倉。

只留需要的列，原始大檔下載後立刻刪除。
用法：python fetch_futures_chips.py --out short_futures/data
"""
from __future__ import annotations

import argparse
import calendar
import csv
import io
import subprocess
import time
from pathlib import Path

UA = "Mozilla/5.0"


def post(url: str, data: str, tmp: Path) -> str:
    for attempt in range(5):
        r = subprocess.run(["curl", "-sS", "-m", "90", "-A", UA, "-o", str(tmp), "-w", "%{http_code}", "-X", "POST", url, "-d", data],
                           capture_output=True, text=True)
        raw = tmp.read_bytes() if tmp.exists() else b""
        if r.stdout.strip() == "200" and not raw.lstrip().startswith(b"<"):
            for enc in ("utf-8-sig", "cp950", "big5"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("cp950", "replace")
        if raw.lstrip().startswith(b"<") and ("DateTime error".encode() in raw or "查無資料".encode() in raw):
            return ""
        time.sleep(10 * (attempt + 1))
    raise SystemExit(f"下載失敗：{url} {data}")


def months(start: tuple, end: tuple):
    y, m = start
    while (y, m) <= end:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def window(y: int, m: int) -> str:
    return f"queryStartDate={y}%2F{m:02d}%2F01&queryEndDate={y}%2F{m:02d}%2F{calendar.monthrange(y, m)[1]:02d}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    args = p.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / "_tmp.bin"

    # 1) 大額交易人：只留 臺股期貨 TX、所有月份合計 999999
    rows = []
    for y, m in months((2004, 1), (2026, 10)):
        text = post("https://www.taifex.com.tw/cht/3/largeTraderFutDown", window(y, m), tmp)
        for cells in csv.reader(io.StringIO(text)):
            if len(cells) >= 10 and cells[1].strip() == "TX" and cells[3].strip() == "999999":
                rows.append([c.strip() for c in (cells[0], cells[4], *cells[5:10])])
        time.sleep(1.0)
    with (out / "large_trader_tx.csv").open("w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(["date", "trader_type", "top5_buy", "top5_sell", "top10_buy", "top10_sell", "total_oi"])
        w.writerows(rows)
    print("large trader rows", len(rows), rows[0][0] if rows else None, rows[-1][0] if rows else None, flush=True)

    # 2) 三大法人（近 3 年）：TXF、MXF
    inst = []
    for commodity in ("TXF", "MXF"):
        for y, m in months((2023, 10), (2026, 10)):
            text = post("https://www.taifex.com.tw/cht/3/futContractsDateDown", window(y, m) + f"&commodityId={commodity}", tmp)
            reader = list(csv.reader(io.StringIO(text)))
            for cells in reader[1:]:
                if len(cells) >= 14:
                    inst.append([commodity] + [c.strip() for c in cells])
            time.sleep(1.0)
    with (out / "institutional.csv").open("w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(["commodity", "date", "product", "identity", "long_vol", "long_amt", "short_vol", "short_amt", "net_vol", "net_amt",
                    "long_oi", "long_oi_amt", "short_oi", "short_oi_amt", "net_oi", "net_oi_amt"])
        w.writerows(inst)
    print("institutional rows", len(inst), flush=True)

    # 3) 小台日行情：一般盤未沖銷契約數（不含價差、週契約）
    oi = {}
    for y, m in months((2023, 10), (2026, 10)):
        data = (f"down_type=1&commodity_id=MTX&commodity_id2=&queryStartDate={y}%2F{m:02d}%2F01"
                f"&queryEndDate={y}%2F{m:02d}%2F{calendar.monthrange(y, m)[1]:02d}")
        text = post("https://www.taifex.com.tw/cht/3/futDataDown", data, tmp)
        reader = list(csv.reader(io.StringIO(text)))
        header = [h.strip() for h in reader[0]] if reader else []
        idx = {h: i for i, h in enumerate(header)}
        for cells in reader[1:]:
            if len(cells) < len(header) - 3:
                continue
            month = cells[idx["到期月份(週別)"]].strip()
            if cells[idx["交易時段"]].strip() != "一般" or "/" in month or "W" in month.upper():
                continue
            value = cells[idx["未沖銷契約數"]].strip().replace(",", "")
            if value.isdigit():
                oi[cells[idx["交易日期"]].strip()] = oi.get(cells[idx["交易日期"]].strip(), 0) + int(value)
        time.sleep(1.0)
    with (out / "mtx_oi.csv").open("w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(["date", "mtx_total_oi"])
        w.writerows(sorted(oi.items()))
    print("mtx oi days", len(oi), flush=True)
    tmp.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
