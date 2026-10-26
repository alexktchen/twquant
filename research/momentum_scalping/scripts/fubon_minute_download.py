#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fubon_minute_download.py — 用富邦 API 逐月下載小台（MXF）各月合約的一般盤 1 分 K（studies/fubon_9y 用），可中斷續傳。

每個月合約 M 抓「M 的前一個月 1 號」到「M 的到期日（第三個星期三）」：
  主力合約大約在前一個月結算日換到 M，所以這段涵蓋 M 當主力的每一天，以及換月前一天的昨收。
每次查詢只查一個日曆月（富邦限制查詢區間 < 1 年），查詢間隔 1.1 秒（歷史行情每分鐘最多 60 次）。
原始回應原封不動存成 JSON，只存本機（富邦行情使用規範禁止轉傳）。

用法：python fubon_minute_download.py --env D:/project/twquant/.env --out D:/project/twquant/data/research_ms/fubon_1m
      [--first 201706] [--last 202612] [--continuous]   # --continuous：過期合約查不到時的退路，改抓 1! 與 2!
"""
from __future__ import annotations

import argparse
import calendar
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fubon_check as C  # noqa: E402

PRODUCT, FIELDS = "MXF", "open,high,low,close,volume,average,transaction"
PAUSE, RATE_LIMIT_WAIT, ATTEMPTS, EXPIRY_GRACE_DAYS = 1.1, 65.0, 6, 7


def third_wednesday(y: int, m: int) -> date:
    first = date(y, m, 1)
    return first + timedelta(days=(2 - first.weekday()) % 7 + 14)


def add_month(y: int, m: int, k: int):
    n = y * 12 + (m - 1) + k
    return n // 12, n % 12 + 1


def months(first: str, last: str):
    y, m = int(first[:4]), int(first[4:])
    while f"{y}{m:02d}" <= last:
        yield y, m
        y, m = add_month(y, m, 1)


def windows(y: int, m: int, today: date):
    """合約 yyyyMM 要查的區間 [(from, to)]，不超過今天。

    到期日遇假日會順延，所以第二段多抓 7 天；合約到期後本來就沒有資料。"""
    py, pm = add_month(y, m, -1)
    spans = [(date(py, pm, 1), date(py, pm, calendar.monthrange(py, pm)[1])),
             (date(y, m, 1), third_wednesday(y, m) + timedelta(days=EXPIRY_GRACE_DAYS))]
    return [(a, min(b, today)) for a, b in spans if a <= today]


def query(futopt, params: dict):
    for attempt in range(ATTEMPTS):
        try:
            payload = futopt.historical.candles(**params)
        except Exception as exc:  # SDK 把 HTTP 錯誤包成例外；只對流量限制重試，其他照原樣丟出
            if "429" not in str(exc) and "rate" not in str(exc).lower():
                raise
            payload = None
        time.sleep(PAUSE)
        if isinstance(payload, dict) and str(payload.get("statusCode", "")) != "429":
            return payload
        print(f"  流量限制，等 {RATE_LIMIT_WAIT:.0f} 秒（第 {attempt + 1} 次）：{str(payload)[:120]}", flush=True)
        time.sleep(RATE_LIMIT_WAIT)
    raise SystemExit(f"一直被限制流量：{params}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--env", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--first", default="201706", help="第一個月合約 yyyyMM")
    p.add_argument("--last", default=None, help="最後一個月合約 yyyyMM（預設：今天往後 1 個月）")
    p.add_argument("--continuous", action="store_true", help="改抓連續合約 1! 與 2!（退路）")
    args = p.parse_args()
    today = date.today()
    last = args.last or "{}{:02d}".format(*add_month(today.year, today.month, 1))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    futopt = C.connect(Path(args.env)).marketdata.rest_client.futopt

    jobs = []
    if args.continuous:
        for y, m in months(args.first, f"{today.year}{today.month:02d}"):
            span = (date(y, m, 1), min(date(y, m, calendar.monthrange(y, m)[1]), today))
            for code, tag in (("1!", "C1"), ("2!", "C2")):
                jobs.append((code, f"{tag}_{y}{m:02d}", span))
    else:
        for y, m in months(args.first, last):
            for a, b in windows(y, m, today):
                jobs.append((f"{y}{m:02d}", f"{y}{m:02d}_{a:%Y%m}", (a, b)))

    empty = 0
    for k, (contract, name, (a, b)) in enumerate(jobs, 1):
        path = out / f"{PRODUCT}_{name}.json"
        # 還沒到期的合約，最後一段每次重抓（資料還在長）
        if path.exists() and b < today - timedelta(days=1):
            continue
        payload = query(futopt, {"product": PRODUCT, "contractMonth": contract, "timeframe": "1", "session": "regular",
                                 "from": f"{a:%Y-%m-%d}", "to": f"{b:%Y-%m-%d}", "sort": "asc", "fields": FIELDS})
        bars = payload.get("data") or []
        empty += not bars
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(f"[{k}/{len(jobs)}] {PRODUCT} {contract} {a}～{b}：{len(bars)} 根", flush=True)
    print(f"done，空回應 {empty} 個", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
