#!/usr/bin/env python3
"""
futures_sync.py — 本機 PG → 雲端 Supabase：把 fubon_live.py --local 寫在本機的模擬單狀態同步上網站（2026-10-29）。

同步的表（兩邊同名同結構）：serving.futures_paper、futures_paper_seconds、futures_quotes、futures_quotes_seconds。
逐筆與五檔（market_live）只留本機，不上雲。

規則：
  - 只推「本機比雲端新」的列（看 updated_at）；雲端比較新（例如有人直接 --upload 過）就不蓋。
  - 期交所重算（taifex_replay）不蓋富邦即時（fubon_live）那一天，和 fubon_live.py 的 Uploader 同一條規則。
  - 一開始先讀雲端每列的 updated_at（只讀鍵和時間，流出很少），所以電腦或網路斷過，下次跑會把漏的補上。

用法：
  盤中一直跑（開盤前啟動，跑到 --until 後最後同步一次就結束）：
    python futures_sync.py --env D:/project/twquant/.env --until 14:05
  只同步一次（例如收盤後重建了某天）：
    python futures_sync.py --env D:/project/twquant/.env --once
  測試：--target-env-key TWQUANT_PG_DSN --target-schema serving_sync_test（同步到本機另一個 schema，不碰雲端）
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

TPE = timezone(timedelta(hours=8))
LOCAL_DSN_ENV = "TWQUANT_PG_DSN"
CLOUD_DSN_ENV = "TWQUANT_SUPABASE_DSN"
SOURCE_SCHEMA = "serving"
# 表 → (主鍵欄, 其他欄, 有沒有 source 欄)。建表語句跟 fubon_live.py 的 *_DDL 一字不差（tests/test_futures_paper_store.py 守著）
TABLES = {
    "futures_paper": (("day",), ("source", "status", "payload", "updated_at"), True),
    "futures_paper_seconds": (("day",), ("payload", "updated_at"), False),
    "futures_quotes": (("day", "product"), ("source", "status", "payload", "updated_at"), True),
    "futures_quotes_seconds": (("day", "product"), ("payload", "updated_at"), False),
}
DDL = {
    "futures_paper": "(day date, source text, status text, payload jsonb, updated_at timestamptz, PRIMARY KEY (day))",
    "futures_paper_seconds": "(day date, payload jsonb, updated_at timestamptz, PRIMARY KEY (day))",
    "futures_quotes": ("(day date, product text, source text, status text, payload jsonb, updated_at timestamptz, "
                       "PRIMARY KEY (day, product))"),
    "futures_quotes_seconds": "(day date, product text, payload jsonb, updated_at timestamptz, PRIMARY KEY (day, product))",
}


def env_value(path: Path, key: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(f"{path} 沒有 {key}")


def log(msg: str) -> None:
    print(f"{datetime.now(TPE):%H:%M:%S} {msg}", flush=True)


class Syncer:
    def __init__(self, local_dsn: str, target_dsn: str, target_schema: str, days_back: int):
        import psycopg

        self.psycopg = psycopg
        self.local_dsn, self.target_dsn, self.schema, self.days_back = local_dsn, target_dsn, target_schema, days_back
        self.local = self.target = None
        self.pushed: dict = {}  # (表, 主鍵) → 雲端上那列的 updated_at

    def _connect(self):
        if self.local is None or self.local.closed:
            self.local = self.psycopg.connect(self.local_dsn, prepare_threshold=None, connect_timeout=10, autocommit=True)
        if self.target is None or self.target.closed:
            self.target = self.psycopg.connect(self.target_dsn, prepare_threshold=None, connect_timeout=10)
            with self.target.cursor() as cur:
                cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
                for table, cols in DDL.items():
                    cur.execute(f'CREATE TABLE IF NOT EXISTS "{self.schema}"."{table}" {cols}')
            self.target.commit()
            self.pushed = self._target_versions()  # 重新連上就重讀雲端現況：斷線期間漏的會補

    def _since(self):
        return datetime.now(TPE).date() - timedelta(days=self.days_back)

    def _target_versions(self) -> dict:
        out = {}
        with self.target.cursor() as cur:
            for table, (keys, _, _) in TABLES.items():
                cur.execute(f'SELECT {", ".join(keys)}, updated_at FROM "{self.schema}"."{table}" WHERE day >= %s', (self._since(),))
                for *key, upd in cur.fetchall():
                    out[(table, tuple(key))] = upd
        self.target.commit()
        return out

    def once(self) -> int:
        """推一輪：本機比目標新的列。回傳推了幾列。"""
        self._connect()
        n = 0
        for table, (keys, cols, has_source) in TABLES.items():
            key_sql = ", ".join(keys)
            rows = self.local.execute(
                f'SELECT {key_sql}, updated_at FROM "{SOURCE_SCHEMA}"."{table}" WHERE day >= %s', (self._since(),)).fetchall()
            newer = [(tuple(k), upd) for *k, upd in rows
                     if (table, tuple(k)) not in self.pushed or upd > self.pushed[(table, tuple(k))]]
            for key, upd in newer:
                where = " AND ".join(f"{k} = %s" for k in keys)
                sel = ", ".join(keys + tuple("payload::text" if c == "payload" else c for c in cols))
                row = self.local.execute(f'SELECT {sel} FROM "{SOURCE_SCHEMA}"."{table}" WHERE {where}', key).fetchone()
                allc = keys + cols
                vals = ", ".join("%s::jsonb" if c == "payload" else "%s" for c in allc)
                sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
                guard = f'EXCLUDED.updated_at >= "{table}".updated_at'
                if has_source:  # 期交所重算不蓋富邦即時
                    guard += f''' AND NOT ("{table}".source = 'fubon_live' AND EXCLUDED.source = 'taifex_replay')'''
                with self.target.cursor() as cur:
                    cur.execute(
                        f'INSERT INTO "{self.schema}"."{table}" ({", ".join(allc)}) VALUES ({vals}) '
                        f"ON CONFLICT ({key_sql}) DO UPDATE SET {sets} WHERE {guard}", row)
                self.target.commit()
                self.pushed[(table, key)] = upd
                n += 1
        return n

    def safe_once(self) -> int:
        try:
            return self.once()
        except Exception as exc:  # noqa: BLE001  # 記錄後下一輪重連再試；不讓一次網路問題停掉整天的同步
            log(f"⚠ 同步失敗（下一輪重試）：{exc}")
            for c in (self.local, self.target):
                if c is not None and not c.closed:
                    c.close()
            self.local = self.target = None
            return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--env", required=True, help=".env 絕對路徑")
    p.add_argument("--once", action="store_true", help="只同步一次")
    p.add_argument("--until", default="14:05", help="台北時間幾點後最後同步一次就結束（預設 14:05，收盤定稿之後）")
    p.add_argument("--every", type=float, default=5.0, help="幾秒同步一輪")
    p.add_argument("--days-back", type=int, default=30, help="看最近幾天的列")
    p.add_argument("--target-env-key", default=CLOUD_DSN_ENV, help="目標 DSN 在 .env 的名字（測試可指本機）")
    p.add_argument("--target-schema", default="serving", help="目標 schema（測試用別的名字）")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    env = Path(args.env)
    if args.target_env_key == LOCAL_DSN_ENV and args.target_schema == SOURCE_SCHEMA:
        raise SystemExit("目標是本機的 serving 就是自己同步給自己；測試請換 --target-schema")
    s = Syncer(env_value(env, LOCAL_DSN_ENV), env_value(env, args.target_env_key), args.target_schema, args.days_back)
    if args.once:
        n = s.once()
        log(f"同步完成：{n} 列")
        return 0
    stop = datetime.fromisoformat(f"{datetime.now(TPE).date()}T{args.until}:00").replace(tzinfo=TPE)
    log(f"開始同步：每 {args.every:g} 秒一輪，到 {args.until}")
    total = 0
    while datetime.now(TPE) < stop:
        total += s.safe_once()
        time.sleep(args.every)
    total += s.once()  # 最後一次失敗要讓排程看到（非 0 結束）
    log(f"結束：今天共推 {total} 列")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
