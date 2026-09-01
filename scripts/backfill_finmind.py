"""FinMind 逐檔全期回補（T-010／D-044）。

用法：
    python scripts/backfill_finmind.py <dataset> <out_dir> [--start 2020-01-01] [--end 2026-09-01]

為什麼是腳本而非 CLI 指令：這是**一次性大量歷史回補**，D-014 已預留「專屬回補指令、
非每日編排迴圈」的路徑；每日增量仍走官方端點（D-015 第 1 點）。

設計要點：
- **可續傳**：每檔一個 parquet，已存在即跳過。被限流或中斷後直接重跑即可。
- **自動等額度**：HTTP 402（額度用盡）不中止，等 `--cooldown` 後重試同一檔，
  上限 `--max-wait`。這是流量控制不是資料問題，故不適用「壞資料即 raise」。
- **限流**：免費註冊層 600 次/小時 → 預設 6.2 秒/請求，留 3% 餘裕。
- **不靜默失敗**：HTTP 非 200、status 非 200、或回應缺 data 欄一律記錄並停下，
  不寫出半套檔案（黃金規則 5）。
- token 只從環境變數 FINMIND_TOKEN 讀，永不寫入輸出或日誌（黃金規則 9）。
- **進度寫 ingest_log**，讓資料後台前端看得到（每 25 檔一筆進度；失敗逐筆記錄）。
  不逐檔記錄是因為 `record()` 每次呼叫寫一個 parquet part，2,064 筆會把日誌灌爆。
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time

import httpx
import polars as pl
import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from twquant.reporting.ingest_log import record

_API = "https://api.finmindtrade.com/api/v4/data"
_DSN = "postgresql://twquant:twquant@localhost:5433/twquant"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"


def _stocks() -> list[str]:
    """四位數普通股清單（本專案的因子宇宙）。"""
    with psycopg.connect(os.environ.get("TWQUANT_PG_DSN", _DSN)) as conn:
        rows = conn.execute(
            "SELECT DISTINCT stock_id FROM serving.prices_daily "
            "WHERE date >= '2020-01-01' AND stock_id ~ '^[0-9]{4}$' ORDER BY stock_id"
        ).fetchall()
    return [r[0] for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset")
    ap.add_argument("out_dir")
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default="2026-09-01")
    ap.add_argument("--sleep", type=float, default=6.2, help="每請求間隔秒（600/hr 留餘裕）")
    ap.add_argument("--data-dir", default="D:/project/twquant/data", help="ingest_log 落點")
    ap.add_argument("--every", type=int, default=25, help="每幾檔寫一筆進度到 ingest_log")
    ap.add_argument("--cooldown", type=float, default=600.0, help="撞到 402 後等待秒數")
    ap.add_argument("--max-wait", type=float, default=21600.0, help="單檔累計等待上限秒")
    args = ap.parse_args()

    token = os.environ.get("FINMIND_TOKEN")
    if not token:
        raise SystemExit("FINMIND_TOKEN 未設（放 .env，黃金規則 9）")
    headers = {"User-Agent": _UA, "Authorization": f"Bearer {token}"}

    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ids = _stocks()
    todo = [s for s in ids if not (out / f"{s}.parquet").exists()]
    print(f"{args.dataset}: 共 {len(ids):,} 檔，待抓 {len(todo):,} 檔", flush=True)

    done = empty = 0
    with httpx.Client(headers=headers, timeout=120) as client:
        for i, sid in enumerate(todo, 1):
            params = {
                "dataset": args.dataset,
                "data_id": sid,
                "start_date": args.start,
                "end_date": args.end,
            }
            r = client.get(_API, params=params)
            # 402＝額度用盡。這是**可預期**的節流，不是資料問題：等額度回補再續，
            # 而不是中止整批（黃金規則 5 針對的是「壞資料靜默通過」，非流量控制）。
            waited = 0
            while r.status_code == 402 and waited < args.max_wait:
                record(
                    args.data_dir,
                    "finmind",
                    args.dataset,
                    args.end,
                    "success",
                    rows=done,
                    detail=f"額度用盡，等待 {args.cooldown:.0f}s 後續抓（stock_id={sid}）",
                )
                print(f"  [{sid}] 額度用盡，等待 {args.cooldown:.0f}s…", flush=True)
                time.sleep(args.cooldown)
                waited += args.cooldown
                r = client.get(_API, params=params)
            problem = ""
            if r.status_code != 200:
                problem = f"HTTP {r.status_code}：{r.text[:150]}"
            else:
                payload = r.json()
                if payload.get("status") != 200:
                    problem = f"status={payload.get('status')} msg={str(payload.get('msg'))[:120]}"
                elif payload.get("data") is None:
                    problem = f"回應缺 data 欄：{str(payload)[:150]}"
            if problem:
                # 失敗逐筆記錄，前端的「近期失敗」看得到是哪一檔卡住
                record(
                    args.data_dir,
                    "finmind",
                    args.dataset,
                    args.end,
                    "failed",
                    detail=f"stock_id={sid} {problem}",
                )
                raise SystemExit(f"[{sid}] {problem}")
            data = payload["data"]
            if data:
                pl.DataFrame(data).write_parquet(out / f"{sid}.parquet")
                done += 1
            else:
                # 無資料（未上市/已下市期間）也要留痕，否則每次重跑都會重抓
                pl.DataFrame({"stock_id": [sid]}).write_parquet(out / f"{sid}.parquet")
                empty += 1
            if i % args.every == 0 or i == len(todo):
                msg = f"{i:,}/{len(todo):,}  有資料 {done:,}  空 {empty:,}"
                print(f"  {msg}", flush=True)
                record(
                    args.data_dir,
                    "finmind",
                    args.dataset,
                    args.end,
                    "success",
                    rows=done,
                    detail=f"進度 {msg}（最後 stock_id={sid}）",
                )
            if i < len(todo):
                time.sleep(args.sleep)
    record(
        args.data_dir,
        "finmind",
        args.dataset,
        args.end,
        "success",
        rows=done,
        detail=f"完成：有資料 {done:,} 檔、空 {empty:,} 檔",
    )
    print(f"完成：有資料 {done:,} 檔、空 {empty:,} 檔 → {out}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
