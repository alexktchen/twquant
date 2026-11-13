#!/usr/bin/env python3
"""
把 `autotrade replay --status-out` 寫出來的狀態檔寫進資料庫 serving.autotrade_status，網站「程式交易」讀它。

連線字串只從環境變數讀（預設 TWQUANT_PG_DSN；寫雲端就用 --dsn-env 指到雲端那個變數），不印出來。
環境變數沒設時可以用 --env-file 指到 .env，只讀同名的那一個變數。
可以一次給很多個狀態檔：全部先檢查過才寫，而且在同一筆交易裡寫完（有一個不合格就一列都不寫）。
同一天、同一種模式重寫是覆蓋。需要 psycopg（pip install "autotrade[web]"）。

用法：
  python tools/push_status.py --status D:/project/twquant/data/autotrade/status/sim_2026-11-13.json
  python tools/push_status.py --status <檔> <檔> … --dsn-env TWQUANT_SUPABASE_DSN --env-file D:/project/twquant/.env
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autotrade.export import STATUS_DDL, STATUS_UPSERT, env_value, status_row


def main(argv: Sequence[str] | None = None) -> int:
    """進入點。"""
    parser = argparse.ArgumentParser(description="把狀態檔寫進 serving.autotrade_status")
    parser.add_argument(
        "--status", type=Path, nargs="+", required=True, help="狀態檔（JSON），可多個"
    )
    parser.add_argument("--dsn-env", default="TWQUANT_PG_DSN", help="放連線字串的環境變數名稱")
    parser.add_argument("--env-file", type=Path, help="環境變數沒設時，改從這個 .env 讀同名的變數")
    args = parser.parse_args(argv)
    rows = [status_row(path.read_text(encoding="utf-8")) for path in args.status]
    dsn = os.environ.get(args.dsn_env)
    if not dsn and args.env_file is not None:
        dsn = env_value(args.env_file.read_text(encoding="utf-8"), args.dsn_env)
    if not dsn:
        where = f"，{args.env_file} 裡也沒有" if args.env_file is not None else ""
        raise SystemExit(f"環境變數 {args.dsn_env} 沒有設定{where}")
    psycopg = importlib.import_module("psycopg")
    with psycopg.connect(dsn) as conn:
        conn.execute('CREATE SCHEMA IF NOT EXISTS "serving"')
        conn.execute(STATUS_DDL)
        for row in rows:
            conn.execute(STATUS_UPSERT, row)
    days = sorted(row[0] for row in rows)
    print(f"已寫入 {len(rows)} 列（{days[0]}～{days[-1]}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
