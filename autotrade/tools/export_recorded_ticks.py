#!/usr/bin/env python3
"""
把富邦即時錄下來的某一天逐筆（含買賣價）從錄製程式的資料庫匯出成 CSV，給 `autotrade replay --feed recorded` 用（A-013）。

錄製程式（fubon_live.py --local）把逐筆寫在本機資料庫的 market_live.ticks；這支只讀、不改任何東西。
連線字串只從環境變數讀（預設 TWQUANT_PG_DSN），不印出來。

用法：
  python tools/export_recorded_ticks.py --day 2026-11-13 --symbol TMFK6 --out D:/project/twquant/data/autotrade/recorded/TMF_2026-11-13.csv
--symbol 是富邦的商品代碼（微台 11 月合約＝TMFK6；K＝11 月、6＝2026）。
"""

from __future__ import annotations

import argparse
import csv
import importlib
import os
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

SQL = (
    "SELECT to_char(t AT TIME ZONE 'Asia/Taipei', 'YYYY-MM-DD\"T\"HH24:MI:SS.US') || '+08:00', "
    "price, size, bid, ask FROM market_live.ticks WHERE day = %s AND symbol = %s ORDER BY t"
)


def main(argv: Sequence[str] | None = None) -> int:
    """進入點。"""
    parser = argparse.ArgumentParser(description="匯出富邦即時錄下來的逐筆（含買賣價）")
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    parser.add_argument("--symbol", required=True, help="富邦商品代碼，例：TMFK6")
    parser.add_argument("--out", type=Path, required=True, help="輸出的 CSV（絕對路徑）")
    parser.add_argument("--dsn-env", default="TWQUANT_PG_DSN", help="放連線字串的環境變數名稱")
    args = parser.parse_args(argv)
    dsn = os.environ.get(args.dsn_env)
    if not dsn:
        print(f"環境變數 {args.dsn_env} 沒有設定", file=sys.stderr)
        return 2
    psycopg = importlib.import_module("psycopg")
    with psycopg.connect(dsn, prepare_threshold=None, connect_timeout=30) as conn:
        conn.read_only = True
        rows = conn.execute(SQL, (args.day, args.symbol)).fetchall()
    if not rows:
        print(f"資料庫裡沒有 {args.day} {args.symbol} 的逐筆", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_name(args.out.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["t", "price", "size", "bid", "ask"])
        for t, price, size, bid, ask in rows:
            writer.writerow(
                [
                    t,
                    f"{price:g}",
                    size,
                    "" if bid is None else f"{bid:g}",
                    "" if ask is None else f"{ask:g}",
                ]
            )
    os.replace(tmp, args.out)
    print(f"匯出 {len(rows)} 筆：{args.day} {args.symbol} → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
