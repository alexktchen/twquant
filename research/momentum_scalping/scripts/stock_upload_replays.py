#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_upload_replays.py — 把 stock_live.py --replay-1m 算好的日子上傳到網站「當沖」分頁復盤（T-458／D-221）。

讀 <root>/replay_<日>/payload.json 與 history.json，照 stock_live.Uploader 寫進 serving.stock_paper 與 stock_paper_history。
不重算；富邦即時錄到的那一天（source = fubon_live）不會被重播蓋掉（Uploader.write 的 WHERE 條件）。
使用者 2026-10-29：「放上網站復盤」（7 天：10-16、10-19～10-22、10-27、10-28）。

用法：python stock_upload_replays.py --env D:/project/twquant/.env --root D:/project/twquant/data/research_ms/stock_live [--days 2026-10-16 ...]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stock_live as L  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env", required=True)
    p.add_argument("--root", required=True)
    p.add_argument("--days", nargs="*", help="只傳這幾天；沒給＝root 底下所有 replay_<日>")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    root = Path(args.root)
    folders = [root / f"replay_{d}" for d in args.days] if args.days else sorted(root.glob("replay_*"))
    if not folders:
        raise SystemExit(f"{root} 底下沒有 replay_<日>")
    uploader = L.Uploader(L.env_value(Path(args.env), L.DSN_ENV), print)
    for folder in folders:
        payload_path, history_path = folder / "payload.json", folder / "history.json"
        if not payload_path.exists() or not history_path.exists():
            raise SystemExit(f"{folder} 少了 payload.json 或 history.json（先跑 stock_live.py --replay-1m）")
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        history = json.loads(history_path.read_text(encoding="utf-8"))
        if payload.get("source") != "minute_replay" or payload.get("day") != folder.name.removeprefix("replay_"):
            raise SystemExit(f"{folder} 的內容不是那一天的 1 分 K 重播：source={payload.get('source')} day={payload.get('day')}")
        uploader.flush(payload, history)
        a = payload["account"]
        print(f"{payload['day']}：已上傳（成交 {a['closed']} 筆、淨 {a['realized']:+,.0f} 元；復盤 {len(json.dumps(history)) // 1024} KB）")
    print("完成，網站「當沖」→「復盤」看得到")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
