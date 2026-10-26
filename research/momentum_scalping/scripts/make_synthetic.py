#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
產生「合成」逐筆成交資料，只用來確認 ms_replay.py 整條管線跑得通。

資料是亂數產生的：部分股票在盤中某個時點被注入一段放量上攻，好讓策略有機會觸發訊號。
這份資料的回測結果「沒有任何績效意義」，請勿拿來判斷策略好壞。

輸出（--out 資料夾）：
    fugle_trades.jsonl   與 api_gateway ReplayStore 相同格式
    ref.csv              昨收 / 當沖資格 / 類股
    market.csv           大盤時間序列範例
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

TPE = timezone(timedelta(hours=8))


def tick_size(price: float) -> float:
    if price < 10:
        return 0.01
    if price < 50:
        return 0.05
    if price < 100:
        return 0.1
    if price < 500:
        return 0.5
    return 1.0


def snap(price: float) -> float:
    step = tick_size(price)
    return round(round(price / step) * step, 2)


def trading_days(start: date, count: int):
    day = start
    while count:
        if day.weekday() < 5:
            yield day
            count -= 1
        day += timedelta(days=1)


def generate_day(rng: random.Random, day: date, symbol: str, market: str, prev_close: float,
                 burst: bool):
    open_dt = datetime(day.year, day.month, day.day, 9, 0, 0, tzinfo=TPE)
    end_dt = datetime(day.year, day.month, day.day, 13, 30, 0, tzinfo=TPE)
    price = snap(prev_close * (1 + rng.uniform(0.0, 0.02)))
    burst_start = open_dt + timedelta(minutes=rng.randint(20, 150)) if burst else None
    burst_len = timedelta(minutes=rng.randint(6, 15))
    volume = 0
    serial = 0
    t = open_dt
    rows = []
    # 開盤集合競價：一筆大量
    size = rng.randint(150, 400)
    volume += size
    serial += 1
    rows.append((t, serial, price, size, volume, snap(price - tick_size(price)), price))
    while True:
        in_burst = burst_start is not None and burst_start <= t < burst_start + burst_len
        after_burst = burst_start is not None and burst_start + burst_len <= t < burst_start + burst_len * 3
        gap = rng.expovariate(1 / (0.6 if in_burst else 2.5))
        t = t + timedelta(seconds=gap)
        if t >= end_dt:
            break
        drift = 0.0
        if in_burst:
            drift = 0.55
        elif after_burst:
            drift = -0.15
        up = rng.random() < 0.5 + drift / 2
        step = tick_size(price)
        if rng.random() < (0.55 if in_burst else 0.35):
            price = snap(price + step if up else price - step)
        price = min(max(price, snap(prev_close * 0.92)), snap(prev_close * 1.095))
        size = max(1, int(rng.lognormvariate(1.3 if not in_burst else 2.4, 0.9)))
        volume += size
        serial += 1
        buyer = up
        bid, ask = (snap(price - step), price) if buyer else (price, snap(price + step))
        rows.append((t, serial, price, size, volume, bid, ask))
    return rows


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True)
    p.add_argument("--days", type=int, default=3)
    p.add_argument("--symbols", type=int, default=12)
    p.add_argument("--start", default="2026-10-19")
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    rng = random.Random(args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sectors = ["半導體業", "電子零組件業", "電腦及週邊設備業"]
    universe = []
    for i in range(args.symbols):
        symbol = str(2300 + i * 7)
        market = "TWSE" if i % 3 else "TPEx"
        universe.append((symbol, market, rng.uniform(25, 95), sectors[i % len(sectors)]))

    record_id = 0
    with (out / "fugle_trades.jsonl").open("w", encoding="utf-8") as trades, \
            (out / "ref.csv").open("w", encoding="utf-8", newline="") as ref_handle:
        ref = csv.writer(ref_handle)
        ref.writerow(["date", "symbol", "market", "prev_close", "day_trade_eligible", "sector", "name"])
        closes = {s: snap(base) for s, _, base, _ in universe}
        for day in trading_days(date.fromisoformat(args.start), args.days):
            for symbol, market, _base, sector in universe:
                prev_close = closes[symbol]
                ref.writerow([day.isoformat(), symbol, market, prev_close, "true", sector, f"合成{symbol}"])
                rows = generate_day(rng, day, symbol, market, prev_close, burst=rng.random() < 0.6)
                for t, serial, price, size, volume, bid, ask in rows:
                    record_id += 1
                    event_time = int(t.timestamp() * 1_000_000)
                    trades.write(json.dumps({
                        "kind": "TRADE", "channel": "trades", "source": "FUGLE_WEBSOCKET",
                        "symbol": symbol, "market": market, "trade_date": day.isoformat(),
                        "event_time": event_time, "received_at": t.isoformat(), "serial": serial,
                        "price": price, "size": size, "volume": volume, "bid": bid, "ask": ask,
                        "classification": "NEW", "event_classification": "NEW", "id": record_id,
                    }, ensure_ascii=False) + "\n")
                closes[symbol] = rows[-1][2]

    with (out / "market.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "time", "taiex_change_pct", "tpex_change_pct", "breadth_net_ratio",
                         "market_score", "market_state", "intraday_trend"])
        for day in trading_days(date.fromisoformat(args.start), args.days):
            for minute in range(0, 271, 1):
                stamp = datetime(day.year, day.month, day.day, 9, 0, tzinfo=TPE) + timedelta(minutes=minute)
                wave = math.sin(minute / 40)
                writer.writerow([day.isoformat(), stamp.strftime("%H:%M:%S"), round(0.4 * wave, 3),
                                 round(0.5 * wave, 3), round(0.25 * wave, 3), round(35 * wave, 1),
                                 "NORMAL" if wave > -0.85 else "WEAK", "UP" if wave > 0.2 else "SIDEWAYS"])
    print(f"合成資料已寫入 {out.resolve()}（僅供測試管線，無績效意義）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
