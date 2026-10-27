#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
minute_engine_selftest.py — 檢查 minute_bidir_test.py 本身寫得對不對（不需要富邦資料）

把期交所逐筆合成 1 分 K（存成富邦回應的格式），再用同一批逐筆跑逐筆版 intraday_bidir_test.py，
最後用 minute_bidir_test.py 的保真度模式比較兩者。合成 K 和逐筆版用的是同一批成交，
所以這裡比出來的差距，只來自「1 分 K 版的查價與成交規則」，不含富邦資料本身的差異。

用法：python minute_engine_selftest.py --taifex D:/.../taifex_30d/csv --work D:/.../fubon_9y_selftest
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent


def ticks_to_bars(folder: Path, product: str, out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    count = 0
    for path in sorted(folder.glob("Daily_*.csv")):
        bars = defaultdict(dict)  # (day, contract) -> {minute: [o, h, l, c, v, pv]}
        for row in csv.reader(path.read_text(encoding="utf-8").splitlines()[1:]):
            if len(row) < 6 or row[1].strip() != product:
                continue
            day, contract, hhmmss = row[0].strip(), row[2].strip(), row[3].strip().zfill(6)
            if not (len(contract) == 6 and contract.isdigit()) or not ("084500" <= hhmmss <= "134500"):
                continue
            price, size = float(row[4]), int(row[5])
            minute = hhmmss[:4]
            bar = bars[(day, contract)].get(minute)
            if bar is None:
                bars[(day, contract)][minute] = [price, price, price, price, size, price * size]
            else:
                bar[1], bar[2], bar[3] = max(bar[1], price), min(bar[2], price), price
                bar[4] += size
                bar[5] += price * size
        for (day, contract), by_minute in bars.items():
            iso = f"{day[:4]}-{day[4:6]}-{day[6:]}"
            data = [{"date": f"{iso}T{m[:2]}:{m[2:]}:00.000+08:00", "open": b[0], "high": b[1], "low": b[2], "close": b[3],
                     "volume": b[4], "average": round(b[5] / b[4], 4)} for m, b in sorted(by_minute.items())]
            (out / f"MXF_{contract}_{day}.json").write_text(
                json.dumps({"product": "MXF", "contractMonth": contract, "data": data}), encoding="utf-8")
            count += len(data)
    return count


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--taifex", required=True, help="期交所逐筆 Daily_*.csv 資料夾（只留小台列也可以）")
    p.add_argument("--work", required=True)
    p.add_argument("--product", default="MTX")
    args = p.parse_args()
    work = Path(args.work)
    n = ticks_to_bars(Path(args.taifex), args.product, work / "bars")
    print(f"合成 1 分 K：{n} 根", flush=True)
    (work / "tick").mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(HERE / "intraday_bidir_test.py"), "--taifex", args.taifex,
                    "--product", args.product, "--out", str(work / "tick")], check=True, stdout=subprocess.DEVNULL)
    r = subprocess.run([sys.executable, str(HERE / "minute_bidir_test.py"), "--bars", str(work / "bars"), "--mode", "fidelity",
                        "--tick-result", str(work / "tick" / "result.json"), "--out", str(work / "minute")])
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
