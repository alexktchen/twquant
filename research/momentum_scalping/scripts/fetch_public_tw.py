#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_public_tw.py — 用公開資料組出 ms_replay.py 的輸入（近似版）

沒有逐筆資料時的替代方案：
    * 選股母體：每個交易日用「前一交易日」成交金額前 N 名普通股（不偷看當天）
    * 價格：Yahoo Finance 1 分 K，拆成每分鐘 4 筆近似成交（O→L→H→C 或 O→H→L→C）
    * 買賣價：依跳動單位假設一檔價差（上漲成交在賣價、下跌成交在買價）
    * 昨日參考價：證交所／櫃買中心當日行情的「收盤 − 漲跌」（已處理除權息）
    * 當沖資格：上市用證交所當沖標的表；上櫃查無公開清單，假設可當沖
    * 產業別：證交所／櫃買中心 OpenAPI
    * 指數：Yahoo 的加權指數 ^TWII 與櫃買指數 IX0043.TWO（1 分 K + 日收盤）

這是「1 分 K 近似」，不是真正的逐筆重播：分鐘內的高低點順序與成交量分配都是假設。
Yahoo 1 分 K 只保留最近幾個交易日。

用法：python fetch_public_tw.py --out public_data --top 300
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TPE = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0"}
COMMON = re.compile(r"^[1-9]\d{3}$")


def get_json(url: str, retries: int = 3) -> Any:
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as response:
                return json.load(response)
        except Exception as exc:  # noqa: BLE001 - 網路錯誤重試後再拋出
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"下載失敗 {url}: {last}")


def num(text: Any) -> Optional[float]:
    value = re.sub(r"<[^>]+>", "", str(text or "")).replace(",", "").strip()
    try:
        return float(value)
    except ValueError:
        return None


def tick_size(price: float) -> float:
    for limit, step in ((10, 0.01), (50, 0.05), (100, 0.1), (500, 0.5), (1000, 1.0)):
        if price < limit:
            return step
    return 5.0


# --------------------------------------------------------------------------- Yahoo
def yahoo_chart(symbol: str, interval: str, rng: str) -> Optional[Dict[str, Any]]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval={interval}&range={rng}"
    data = get_json(url)
    result = (data.get("chart") or {}).get("result")
    return result[0] if result else None


def yahoo_bars(symbol: str, interval: str, rng: str) -> List[Tuple[int, float, float, float, float, float]]:
    result = yahoo_chart(symbol, interval, rng)
    if not result or not result.get("timestamp"):
        return []
    quote = result["indicators"]["quote"][0]
    bars = []
    for i, ts in enumerate(result["timestamp"]):
        values = [quote[k][i] for k in ("open", "high", "low", "close", "volume")]
        if any(v is None for v in values[:4]):
            continue
        bars.append((int(ts), *(float(v or 0) for v in values)))
    return bars


# --------------------------------------------------------------------------- 交易所日資料
def twse_day(d: date) -> Dict[str, Dict[str, Any]]:
    url = (f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d:%Y%m%d}"
           "&type=ALLBUT0999&response=json")
    data = get_json(url)
    out: Dict[str, Dict[str, Any]] = {}
    for table in data.get("tables") or []:
        fields = table.get("fields") or []
        if "證券代號" not in fields or "成交金額" not in fields:
            continue
        idx = {name: i for i, name in enumerate(fields)}
        for row in table.get("data") or []:
            code = str(row[idx["證券代號"]]).strip()
            if not COMMON.match(code):
                continue
            close = num(row[idx["收盤價"]])
            sign_text = re.sub(r"<[^>]+>", "", str(row[idx["漲跌(+/-)"]])).strip()
            diff = num(row[idx["漲跌價差"]])
            reference = None
            if close is not None and diff is not None and sign_text in {"+", "-", ""}:
                reference = close - diff if sign_text == "+" else close + diff if sign_text == "-" else close
            out[code] = {"market": "TWSE", "name": str(row[idx["證券名稱"]]).strip(), "close": close,
                         "turnover": num(row[idx["成交金額"]]) or 0.0, "reference": reference}
    return out


def tpex_day(d: date) -> Dict[str, Dict[str, Any]]:
    url = (f"https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={d:%Y}%2F{d:%m}%2F{d:%d}"
           "&type=EW&response=json")
    data = get_json(url)
    out: Dict[str, Dict[str, Any]] = {}
    for table in data.get("tables") or []:
        fields = table.get("fields") or []
        if "代號" not in fields or "成交金額(元)" not in fields:
            continue
        idx = {name: i for i, name in enumerate(fields)}
        for row in table.get("data") or []:
            code = str(row[idx["代號"]]).strip()
            if not COMMON.match(code):
                continue
            close = num(row[idx["收盤"]])
            change = num(row[idx["漲跌"]])
            out[code] = {"market": "TPEx", "name": str(row[idx["名稱"]]).strip(), "close": close,
                         "turnover": num(row[idx["成交金額(元)"]]) or 0.0,
                         "reference": close - change if close is not None and change is not None else None}
    return out


def twse_day_trade(d: date) -> Optional[set]:
    url = f"https://www.twse.com.tw/rwd/zh/dayTrading/TWTB4U?date={d:%Y%m%d}&selectType=All&response=json"
    try:
        data = get_json(url)
    except RuntimeError:
        return None
    for table in data.get("tables") or []:
        fields = table.get("fields") or []
        if "證券代號" in fields:
            return {str(row[fields.index("證券代號")]).strip() for row in table.get("data") or []}
    return None


def tpex_index_daily() -> List[Tuple[str, float]]:
    """櫃買指數日收盤（Yahoo 的 IX0043.TWO 日線只回一筆，改用櫃買中心 OpenAPI；只涵蓋近期）。"""
    rows = []
    for row in get_json("https://www.tpex.org.tw/openapi/v1/tpex_index"):
        stamp, close = str(row.get("Date") or ""), num(row.get("Close"))
        if len(stamp) == 8 and close:
            rows.append((f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}", close))
    return sorted(rows)


def sectors() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for row in get_json("https://openapi.twse.com.tw/v1/opendata/t187ap03_L"):
        out[str(row.get("公司代號") or "").strip()] = str(row.get("產業別") or "").strip()
    for row in get_json("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"):
        out[str(row.get("SecuritiesCompanyCode") or "").strip()] = str(row.get("SecuritiesIndustryCode") or "").strip()
    return out


# --------------------------------------------------------------------------- 1 分 K → 近似逐筆
def bar_to_ticks(t0: int, o: float, h: float, low: float, c: float, volume_lots: int,
                 last_price: Optional[float], path_mode: str = "standard") -> List[Tuple[int, float, int, float, float]]:
    # standard：紅 K 先低後高、黑 K 先高後低（常見慣例）；reversed：反過來，用來量測路徑假設的影響。
    low_first = c >= o if path_mode == "standard" else c < o
    path = [o, low, h, c] if low_first else [o, h, low, c]
    offsets = [0.5, 15.0, 30.0, 59.0]
    base, rest = divmod(volume_lots, 4)
    sizes = [base + (1 if i < rest else 0) for i in range(4)]
    out = []
    previous = last_price if last_price is not None else o
    for price, offset, size in zip(path, offsets, sizes):
        step = tick_size(price)
        if price >= previous:
            bid, ask = round(price - step, 2), price
        else:
            bid, ask = price, round(price + step, 2)
        out.append((int((t0 + offset) * 1_000_000), price, size, bid, ask))
        previous = price
    return out


def write_index_daily(out: Path, index_1d: Dict[str, List[tuple]]) -> None:
    with (out / "index_daily.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "index", "close"])
        for ts, _o, _h, _l, c, _v in index_1d["TAIEX"]:
            writer.writerow([datetime.fromtimestamp(ts, TPE).date().isoformat(), "TAIEX", c])
        for day, close in tpex_index_daily():
            writer.writerow([day, "TPEX", close])


def rebuild_ticks(out: Path, path_mode: str, target: Path) -> None:
    """不重新下載：用既有 bars.csv 與 ref.csv，以另一種分鐘內路徑重建近似逐筆。"""
    markets = {}
    with (out / "ref.csv").open("r", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            markets[(row["date"], row["symbol"])] = row["market"]
    by_key: Dict[Tuple[str, str], List[List[str]]] = defaultdict(list)
    with (out / "bars.csv").open("r", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            by_key[(row["date"], row["symbol"])].append(row)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["symbol", "market", "time", "price", "size", "volume", "bid", "ask"])
        for (day, code), rows in sorted(by_key.items()):
            cumulative, last_price = 0, None
            for row in rows:
                lots = int(row["volume_lots"])
                if lots <= 0:
                    continue
                t0 = int(datetime.fromisoformat(f"{day}T{row['time']}:00").replace(tzinfo=TPE).timestamp())
                values = [float(row[k]) for k in ("open", "high", "low", "close")]
                for t_us, price, size, bid, ask in bar_to_ticks(t0, *values, lots, last_price, path_mode):
                    cumulative += size
                    writer.writerow([code, markets.get((day, code), "TWSE"), t_us, price, size, cumulative, bid, ask])
                    last_price = price


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True)
    p.add_argument("--top", type=int, default=300, help="每天的選股母體：前一交易日成交金額前 N 名")
    p.add_argument("--range", default="5d", help="Yahoo 1 分 K 期間（最多約 7 天）")
    p.add_argument("--rebuild-ticks", choices=["standard", "reversed"],
                   help="不下載，只用既有 bars.csv 以指定的分鐘內路徑重建 ticks_<mode>.csv")
    args = p.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.rebuild_ticks:
        target = out / f"ticks_{args.rebuild_ticks}.csv"
        rebuild_ticks(out, args.rebuild_ticks, target)
        print(f"已重建 {target}")
        return 0

    print("[1/5] 指數資料", flush=True)
    index_symbols = {"TAIEX": "%5ETWII", "TPEX": "IX0043.TWO"}
    index_1m = {name: yahoo_bars(sym, "1m", args.range) for name, sym in index_symbols.items()}
    index_1d = {name: yahoo_bars(sym, "1d", "3mo") for name, sym in index_symbols.items()}
    days = sorted({datetime.fromtimestamp(b[0], TPE).date() for b in index_1m["TAIEX"]})
    daily_dates = sorted({datetime.fromtimestamp(b[0], TPE).date() for b in index_1d["TAIEX"]})
    previous = [d for d in daily_dates if d < days[0]]
    if not previous:
        raise SystemExit("找不到第一個交易日的前一天")
    all_days = [previous[-1]] + days
    print(f"      交易日 {[d.isoformat() for d in days]}，母體依據日 {previous[-1]}", flush=True)

    with (out / "index_intraday.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "time", "index", "price"])
        for name, bars in index_1m.items():
            for ts, _o, _h, _l, c, _v in bars:
                dt = datetime.fromtimestamp(ts + 60, TPE)  # 1 分 K 收盤在 bar 結束時才知道
                writer.writerow([dt.date().isoformat(), dt.strftime("%H:%M:%S"), name, c])
    write_index_daily(out, index_1d)

    print("[2/5] 交易所日行情", flush=True)
    exchange: Dict[date, Dict[str, Dict[str, Any]]] = {}
    for d in all_days:
        exchange[d] = {**twse_day(d), **tpex_day(d)}
        time.sleep(0.5)
    day_trade = {d: twse_day_trade(d) for d in days}
    sector_map = sectors()

    universe: Dict[date, List[str]] = {}
    for prev_day, day in zip(all_days, days):
        ranked = sorted(exchange[prev_day].items(), key=lambda kv: -kv[1]["turnover"])
        universe[day] = [code for code, row in ranked if row["close"]][: args.top]
    symbols = sorted({s for codes in universe.values() for s in codes})
    print(f"      母體合計 {len(symbols)} 檔", flush=True)

    print("[3/5] Yahoo 1 分 K", flush=True)
    bars_by_symbol: Dict[str, List[tuple]] = {}
    market_of = {}
    for d in all_days:
        for code, row in exchange[d].items():
            market_of[code] = row["market"]
    for i, code in enumerate(symbols, 1):
        suffix = ".TW" if market_of.get(code) == "TWSE" else ".TWO"
        try:
            bars_by_symbol[code] = yahoo_bars(code + suffix, "1m", args.range)
        except RuntimeError as exc:
            print(f"      {code} 失敗：{exc}", file=sys.stderr)
            bars_by_symbol[code] = []
        if i % 50 == 0:
            print(f"      {i}/{len(symbols)}", flush=True)
        time.sleep(0.15)

    print("[4/5] 寫出近似逐筆與 1 分 K", flush=True)
    missing = 0
    with (out / "ticks.csv").open("w", encoding="utf-8", newline="") as tick_handle, \
            (out / "bars.csv").open("w", encoding="utf-8", newline="") as bar_handle, \
            (out / "ref.csv").open("w", encoding="utf-8", newline="") as ref_handle:
        ticks = csv.writer(tick_handle)
        ticks.writerow(["symbol", "market", "time", "price", "size", "volume", "bid", "ask"])
        bars_out = csv.writer(bar_handle)
        bars_out.writerow(["date", "symbol", "time", "open", "high", "low", "close", "volume_lots"])
        ref = csv.writer(ref_handle)
        ref.writerow(["date", "symbol", "market", "prev_close", "day_trade_eligible", "sector_code", "name"])
        for day in days:
            for code in universe[day]:
                row = exchange[day].get(code) or {}
                prev_row = exchange[all_days[all_days.index(day) - 1]].get(code) or {}
                reference = row.get("reference") or prev_row.get("close")
                market = market_of.get(code, "TWSE")
                listed = day_trade[day]
                eligible = (code in listed) if (market == "TWSE" and listed is not None) else True
                ref.writerow([day.isoformat(), code, market, reference, str(eligible).lower(),
                              sector_map.get(code, ""), row.get("name") or prev_row.get("name") or ""])
                day_bars = [b for b in bars_by_symbol.get(code, [])
                            if datetime.fromtimestamp(b[0], TPE).date() == day]
                if not day_bars:
                    missing += 1
                    continue
                cumulative = 0
                last_price = None
                for ts, o, h, low, c, v in day_bars:
                    lots = int(round(v / 1000))
                    bars_out.writerow([day.isoformat(), code, datetime.fromtimestamp(ts, TPE).strftime("%H:%M"),
                                       o, h, low, c, lots])
                    if lots <= 0:
                        continue
                    for t_us, price, size, bid, ask in bar_to_ticks(ts, o, h, low, c, lots, last_price):
                        cumulative += size
                        ticks.writerow([code, market, t_us, price, size, cumulative, bid, ask])
                        last_price = price
    print(f"[5/5] 完成：{out.resolve()}（{missing} 檔日沒有 1 分 K）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
