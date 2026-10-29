#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ms_halfyear_upload.py — 把 ms_halfyear.py 算好的網友原版 1 分 K 估算寫進雲端 serving.futures_ms_estimate（網站台指期日曆，T-462）。

一個商品（MTX／TX／TMF）、一天一列：total、n、trades（每筆進出場、原因、淨損益）、source＝minute_estimate。
網站的日期清單只在那天沒有逐筆模擬單時才用它（真實資料優先），所以重傳不會蓋掉真實的日子。

用法：python ms_halfyear_upload.py --env D:/project/twquant/.env --dir D:/project/twquant/data/research_ms/ms_halfyear
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DSN_ENV = "TWQUANT_SUPABASE_DSN"
DDL = ('CREATE TABLE IF NOT EXISTS "serving"."futures_ms_estimate" (product text NOT NULL, day date NOT NULL, '
       'source text NOT NULL, total double precision NOT NULL, n integer NOT NULL, trades jsonb NOT NULL, '
       'updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (product, day))')
PRODUCTS = ("MTX", "TX", "TMF")


def env_value(path: Path, key: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(f"{path} 沒有 {key}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env", required=True)
    p.add_argument("--dir", required=True)
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import psycopg

    folder = Path(args.dir)
    files = [folder / f"{c}.json" for c in PRODUCTS]
    missing = [f.name for f in files if not f.exists()]
    if missing:
        raise SystemExit(f"{folder} 少了 {missing}（先跑 ms_halfyear.py）")
    with psycopg.connect(env_value(Path(args.env), DSN_ENV), prepare_threshold=None, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute('CREATE SCHEMA IF NOT EXISTS "serving"')
            cur.execute(DDL)
            for f in files:
                data = json.loads(f.read_text(encoding="utf-8"))
                code, source = data["product"], data["source"]
                rows = [(code, day, source, float(v["total"]), int(v["n"]), json.dumps(v["trades"], ensure_ascii=False))
                        for day, v in sorted(data["days"].items())]
                cur.executemany(
                    'INSERT INTO "serving"."futures_ms_estimate" (product, day, source, total, n, trades, updated_at) '
                    "VALUES (%s, %s, %s, %s, %s, %s::jsonb, now()) ON CONFLICT (product, day) DO UPDATE SET "
                    "source = EXCLUDED.source, total = EXCLUDED.total, n = EXCLUDED.n, trades = EXCLUDED.trades, updated_at = now()",
                    rows)
                tot = sum(r[3] for r in rows)
                print(f"{code}：{rows[0][1]}～{rows[-1][1]} 共 {len(rows)} 天、{sum(r[4] for r in rows)} 筆、合計 {tot:+,.0f} 元，已寫進雲端")
        conn.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
