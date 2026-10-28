#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_minute_download.py — 用富邦 API 下載上市櫃普通股與加權、櫃買指數的 1 分 K（個股當沖回測用），可中斷續傳。

母體：富邦全市場快照（type=COMMONSTOCK）的四碼普通股，加上 tickers 的產業代號；寫成 universe_<日>.json。
  注意：用的是「下載當天」還在交易的股票，期間內下市的股票不在裡面（倖存者偏差），回測報告要寫明。
每檔每段查一次（富邦限制查詢區間 < 1 年），查詢間隔 1.1 秒（歷史行情每分鐘最多約 60 次），429 就等 65 秒再試。
原始回應原封不動存成 JSON，只存本機（富邦行情使用規範禁止轉傳）。量的單位：分 K 是「張」。

用法：python stock_minute_download.py --env D:/project/twquant/.env --out D:/project/twquant/data/research_ms/stock_1m \
          --from 2026-10-27 --to 2026-10-28
      富邦個股 1 分 K 自 2023-05-23 起。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fubon_check as C  # noqa: E402

COMMON = re.compile(r"^[1-9]\d{3}$")
INDEXES = ("IX0001", "IX0043")  # 加權、櫃買
PAUSE, RATE_LIMIT_WAIT, ATTEMPTS = 1.1, 65.0, 6
CHUNK_DAYS = 360  # 每段少於一年


def chunks(start: date, end: date):
    cur = start
    while cur <= end:
        stop = min(end, cur + timedelta(days=CHUNK_DAYS - 1))
        yield cur, stop
        cur = stop + timedelta(days=1)


def call(fn, what: str, log):
    for attempt in range(1, ATTEMPTS + 1):
        try:
            payload = fn()
        except Exception as exc:  # noqa: BLE001  # 網路錯誤重試；ATTEMPTS 次都失敗就停下來（不跳過）
            log(f"  {what} 第 {attempt} 次失敗：{type(exc).__name__}: {str(exc)[:160]}")
            time.sleep(PAUSE * attempt * 3)
            continue
        limited = (isinstance(payload, dict) and (payload.get("statusCode") == 429 or "Rate limit" in str(payload.get("message", ""))))             or (isinstance(payload, str) and "Rate limit" in payload)
        if limited:
            log(f"  {what} 被限速，等 {RATE_LIMIT_WAIT:.0f} 秒")
            time.sleep(RATE_LIMIT_WAIT)
            continue
        return payload
    raise SystemExit(f"{what} 連續 {ATTEMPTS} 次失敗，停止（之後重跑會從這裡續傳）")


def universe(stock, out: Path, log) -> list:
    rows = []
    for market in ("TSE", "OTC"):
        snap = call(lambda: stock.snapshot.quotes(market=market, type="COMMONSTOCK"), f"{market} 快照", log)
        for r in snap.get("data") or []:
            sym = str(r.get("symbol") or "")
            if COMMON.match(sym):
                rows.append({"symbol": sym, "name": r.get("name"), "market": "TWSE" if market == "TSE" else "TPEX"})
        time.sleep(PAUSE)
    industry = {}
    for exchange in ("TWSE", "TPEx"):
        t = call(lambda: stock.intraday.tickers(type="EQUITY", exchange=exchange, isNormal=True), f"{exchange} 代號表", log)
        for r in t.get("data") or []:
            industry[str(r.get("symbol"))] = str(r.get("industry") or "")
        time.sleep(PAUSE)
    for r in rows:
        r["industry"] = industry.get(r["symbol"], "")
    path = out / f"universe_{date.today():%Y%m%d}.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    log(f"母體：普通股 {len(rows)} 檔（上市 {sum(r['market'] == 'TWSE' for r in rows)}、上櫃 {sum(r['market'] == 'TPEX' for r in rows)}）→ {path.name}")
    return rows


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--from", dest="start", required=True)
    p.add_argument("--to", dest="end", required=True)
    p.add_argument("--symbols", nargs="*", help="只抓這幾檔（測試用）")
    args = p.parse_args()
    out = Path(args.out)
    (out / "bars").mkdir(parents=True, exist_ok=True)
    os.chdir(out)  # 富邦 SDK 在執行目錄寫 log/（含個資），不能是 repo
    logf = (out / "download.log").open("a", encoding="utf-8")

    def log(msg: str) -> None:
        print(msg, flush=True)
        logf.write(msg + "\n")
        logf.flush()

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if start < date(2023, 5, 23):
        raise SystemExit("富邦個股 1 分 K 自 2023-05-23 起")
    stock = C.connect(Path(args.env)).marketdata.rest_client.stock
    symbols = [{"symbol": s, "market": "", "name": "", "industry": ""} for s in args.symbols] if args.symbols else universe(stock, out, log)
    jobs = [(s["symbol"], a, b) for s in symbols for a, b in chunks(start, end)]
    jobs += [(ix, a, b) for ix in INDEXES for a, b in chunks(start, end)]
    jobs += [(f"{ix}_D", start - timedelta(days=300), end) for ix in INDEXES]  # 指數日 K（昨收、日線均線；查詢區間要 < 1 年）
    done = 0
    t0 = time.time()
    for k, (sym, a, b) in enumerate(jobs, 1):
        path = out / "bars" / f"{sym}_{a:%Y%m%d}_{b:%Y%m%d}.json"
        if path.exists():
            continue
        daily = sym.endswith("_D")
        params = {"symbol": sym[:-2] if daily else sym, "timeframe": "D" if daily else "1",
                  "from": str(a), "to": str(b), "sort": "asc"}
        payload = call(lambda: stock.historical.candles(**params), f"{sym} {a}～{b}", log)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        done += 1
        if done % 100 == 0:
            log(f"  {k}/{len(jobs)}（{time.time() - t0:,.0f} 秒）")
        time.sleep(PAUSE)
    log(f"完成：新抓 {done} 份，共 {len(jobs)} 份")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
