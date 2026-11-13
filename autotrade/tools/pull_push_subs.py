#!/usr/bin/env python3
"""
把你在網站 App 開了通知的裝置（推播訂閱）從雲端資料庫拉成本機檔案，給 autotrade 的第二管道用。

交易程式執行時只讀這個檔，不碰資料庫（網站或資料庫壞了，交易與 Telegram 照常）。
只讀資料庫（SELECT），不改任何東西。只拉環境變數 AUTOTRADE_PUSH_EMAIL 那個帳號的裝置。
連線字串與帳號只從環境變數讀，或用 --env-file 從 .env 讀同名的變數；畫面只印幾台裝置與推播服務，不印網址與帳號。
換了手機、重新開通知、或 autotrade 說「這台裝置的通知已經失效」時，重新拉一次。需要 psycopg（pip install "autotrade[web]"）。

用法（--out 請給絕對路徑，放在主 checkout 的 data/ 底下，不進版控）：
  python tools/pull_push_subs.py --out D:/project/twquant/data/autotrade/push_subscriptions.json --env-file D:/project/twquant/.env
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autotrade.cli import read_env, write_text_atomic
from autotrade.config import load_config
from autotrade.delivery import parse_push_subscriptions, push_file_text

ENV_EMAIL = "AUTOTRADE_PUSH_EMAIL"  # 拉哪個帳號的裝置（網站登入用的 email）
QUERY = (
    "SELECT endpoint, p256dh, auth FROM web.push_subscriptions "
    "WHERE lower(email) = %s ORDER BY created_at"
)
DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "tmf_sim.toml"


def main(argv: Sequence[str] | None = None) -> int:
    """進入點。"""
    parser = argparse.ArgumentParser(description="把網站推播的訂閱拉成本機檔案")
    parser.add_argument("--out", type=Path, required=True, help="訂閱檔要寫到哪（絕對路徑）")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="autotrade 設定檔")
    parser.add_argument(
        "--dsn-env", default="TWQUANT_SUPABASE_DSN", help="放雲端資料庫連線字串的環境變數名稱"
    )
    parser.add_argument("--env-file", type=Path, help="環境變數沒設時，從這個 .env 讀")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    if cfg.notify_push is None:
        raise SystemExit("設定檔沒有 [notify_push]")
    env = read_env((args.dsn_env, ENV_EMAIL), args.env_file)
    for name in (args.dsn_env, ENV_EMAIL):
        if not env.get(name):
            where = f"，{args.env_file} 裡也沒有" if args.env_file is not None else ""
            raise SystemExit(f"環境變數 {name} 沒有設定{where}")
    email = env[ENV_EMAIL].strip().lower()
    psycopg = importlib.import_module("psycopg")
    with psycopg.connect(env[args.dsn_env]) as conn:
        exists = conn.execute(
            "SELECT to_regclass(%s) IS NOT NULL", ("web.push_subscriptions",)
        ).fetchone()
        rows = conn.execute(QUERY, (email,)).fetchall() if exists and exists[0] else []
    text = push_file_text([(str(e), str(k), str(a)) for e, k, a in rows])
    subs = parse_push_subscriptions(text, cfg.notify_push.allowed_hosts)  # 不合格就不寫檔
    write_text_atomic(args.out, text)
    hosts = "、".join(s.host for s in subs)
    print(f"已寫入 {len(subs)} 台裝置的訂閱（{hosts}）")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())
