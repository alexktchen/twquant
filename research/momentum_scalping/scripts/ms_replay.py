#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ms_replay.py — Momentum_Scalping 逐筆重播評估器

用錄下來的逐筆成交（Fugle Trades），依事件時間重播一整天，並且「直接呼叫原系統的程式」：

    Market Context + Scanner      → scanner.scan_candidates()          （每 30 秒一輪，同 Runtime）
    Momentum / Trend 狀態機        → intraday.get_signal_context()     （每筆成交都評估，同 Runtime）
    進場風控 17 道閘門             → trading.evaluate_trading_decision()
    送單前 Revalidation           → trading.revalidate_buy_before_order()
    出場數量（整張、減碼）          → trading.evaluate_trading_decision()（SELL）
    13:00 時鐘清倉                 → 同 Runtime._run_clock_exits

本檔只負責：餵資料、模擬帳戶與成交、扣成本、統計。策略與風控邏輯一行都不重寫，
所以量到的是「這份程式碼本身」的期望值，而不是某個近似版本。

成交模型（保守、可調）：
    * 訊號通過全部閘門後，經過 --latency-ms 毫秒，用該股「下一筆成交事件」的 ask 買進 / bid 賣出。
    * 事件沒有 bid/ask 時，退回成交價 ± 1 個跳動點。
    * 不模擬五檔深度與部分成交：假設最佳一檔吃得下（1～數張的小部位才合理）。
    * 手續費 0.1425% × 折扣（有最低手續費），當沖證交稅 0.15%（賣出時）。

只用 Python 標準函式庫；Windows / Linux 都能跑。

用法：python ms_replay.py --help
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import heapq
import importlib
import json
import math
import multiprocessing
import os
import random
import re
import statistics
import sys
import time as time_module
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

TPE = timezone(timedelta(hours=8))
US = 1_000_000
OWNER = "MOMENTUM_SCALPING"

# Tick 欄位索引：(event_time_us, serial, price, size, volume, bid, ask)
T_TIME, T_SERIAL, T_PRICE, T_SIZE, T_VOLUME, T_BID, T_ASK = range(7)


# =============================================================================
# 載入原系統模組
# =============================================================================


def _latest_module(repo: Path, prefix: str) -> str:
    best: Optional[Tuple[Tuple[int, ...], str]] = None
    for path in repo.glob(f"{prefix}_v*.py"):
        match = re.fullmatch(rf"{prefix}_v(\d+(?:_\d+)*)", path.stem)
        if not match:
            continue
        version = tuple(int(x) for x in match.group(1).split("_"))
        if best is None or version > best[0]:
            best = (version, path.stem)
    if best is None:
        if (repo / f"{prefix}.py").exists():
            return prefix
        raise SystemExit(f"在 {repo} 找不到 {prefix}_v*.py")
    return best[1]


def load_modules(repo: str) -> Dict[str, Any]:
    root = Path(repo).resolve()
    if not root.is_dir():
        raise SystemExit(f"--repo 不是資料夾：{root}")
    names = {key: _latest_module(root, key)
             for key in ("intraday", "market", "scanner", "trading", "api_gateway")}
    # 讓各模組互相動態載入時都指到同一份檔案。
    os.environ["INTRADAY_MARKET_MODULE"] = names["market"]
    os.environ["INTRADAY_API_GATEWAY_MODULE"] = names["api_gateway"]
    os.environ["TRADING_MARKET_MODULE"] = names["market"]
    os.environ["TRADING_SCANNER_MODULE"] = names["scanner"]
    os.environ["TRADING_INTRADAY_MODULE"] = names["intraday"]
    os.environ["TRADING_API_GATEWAY_MODULE"] = names["api_gateway"]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    modules = {key: importlib.import_module(name) for key, name in names.items()}
    modules["_names"] = names
    return modules


def apply_momentum5m_fix(intraday: Any) -> None:
    """修正 Momentum 出場條件讀不到 momentum_5m 的問題（intraday_v2_1.py:1969 / :1442）。

    原本 `_post_fill_evidence` 拿 `_dual_strategy()` 回傳的 metrics 判斷 1/3/5 分動能，
    但那個 dict 從來沒有 momentum_5m，使「跌破 VWAP 且 1/3/5 分動能皆負 → 全出」永遠不成立。
    這裡只在回傳值補上同一公式的 momentum_5m，其他邏輯不動。
    """
    original = intraday._dual_strategy
    if getattr(original, "_ms_replay_patched", False):
        return

    def patched(bars, market, candidate, params):
        momentum, trend, last = original(bars, market, candidate, params)
        last = dict(last)
        last["momentum_5m"] = intraday._rolling_momentum(bars, params.momentum_5m)
        return momentum, trend, last

    patched._ms_replay_patched = True  # type: ignore[attr-defined]
    intraday._dual_strategy = patched


# =============================================================================
# 參數
# =============================================================================


def _build_dataclass(cls: Any, values: Mapping[str, Any]) -> Any:
    default = cls()
    names = {f.name for f in dataclasses.fields(cls)}
    overrides = {k: v for k, v in dict(values or {}).items() if k in names}
    instance = dataclasses.replace(default, **overrides) if overrides else default
    instance.validate()
    return instance


def load_parameters(mods: Mapping[str, Any], params_json: Optional[str],
                    overrides: Sequence[str]) -> Dict[str, Any]:
    """讀 GUI 存的 gui_parameters/latest.json（或同格式檔）；沒給就用程式預設值。"""
    sections: Dict[str, Dict[str, Any]] = {"intraday": {}, "scanner": {}, "trading": {}, "market": {}}
    gui: Dict[str, Any] = {}
    if params_json:
        payload = json.loads(Path(params_json).read_text(encoding="utf-8"))
        parameters = payload.get("parameters", payload)
        for key in sections:
            if isinstance(parameters.get(key), Mapping):
                sections[key].update(parameters[key])
        if isinstance(payload.get("gui"), Mapping):
            gui = dict(payload["gui"])
        # 同 params 模組：舊版 Wave 3 根在首次載入時移轉為 5 根。
        if (parameters.get("_momentum_wave_policy") != "REARMABLE_FIVE_BAR"
                and sections["intraday"].get("max_entry_wave_bars") in (3, "3")):
            sections["intraday"]["max_entry_wave_bars"] = 5
    for text in overrides:
        # 格式：intraday.momentum_take_profit_pct=1.5
        match = re.fullmatch(r"(intraday|scanner|trading|market)\.(\w+)=(.*)", text.strip())
        if not match:
            raise SystemExit(f"--set 格式錯誤：{text!r}，應為 module.name=value")
        module, name, raw = match.groups()
        try:
            value: Any = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        sections[module][name] = value
    return {
        "intraday": _build_dataclass(mods["intraday"].IntradayParameters, sections["intraday"]),
        "scanner": _build_dataclass(mods["scanner"].ScannerParameters, sections["scanner"]),
        "trading": _build_dataclass(mods["trading"].TradingParameters, sections["trading"]),
        "market": _build_dataclass(mods["market"].MarketParameters, sections["market"]),
        "gui": gui,
    }


# =============================================================================
# 資料載入
# =============================================================================


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _to_int(value: Any, default: int = 0) -> int:
    number = _to_float(value)
    return default if number is None else int(number)


def _normalize_market(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"TWSE", "TSE", "LISTED", "上市"}:
        return "TWSE"
    if text in {"TPEX", "OTC", "上櫃"}:
        return "TPEx"
    return str(value or "").strip()


def parse_time_us(value: Any) -> Optional[int]:
    """ISO 時間（無時區視為台北）或 epoch 秒/毫秒/微秒 → epoch 微秒。"""
    number = _to_float(value)
    if number is not None and number > 0 and not re.search(r"[-:T]", str(value)):
        if number > 10_000_000_000_000:
            return int(number)
        if number > 10_000_000_000:
            return int(number * 1000)
        return int(number * US)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TPE)
    return int(round(dt.timestamp() * US))


def us_to_dt(value: int) -> datetime:
    return datetime.fromtimestamp(value / US, tz=TPE)


def us_to_iso(value: int) -> str:
    return us_to_dt(value).isoformat(timespec="microseconds")


@dataclass
class Dataset:
    # days[trade_date][(market, symbol)] = [tick, ...]（依 event_time, serial 排序）
    days: Dict[str, Dict[Tuple[str, str], List[tuple]]] = field(default_factory=dict)
    names: Dict[str, str] = field(default_factory=dict)
    size_unit: str = "LOT"
    skipped: Counter = field(default_factory=Counter)


def _iter_files(paths: Sequence[str]) -> Iterable[Path]:
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.suffix.lower() in {".jsonl", ".csv"} and child.is_file():
                    yield child
        elif path.is_file():
            yield path
        else:
            raise SystemExit(f"找不到資料：{path}")


def _session_bounds_us(day: str) -> Tuple[int, int]:
    d = date.fromisoformat(day)
    start = datetime(d.year, d.month, d.day, 9, 0, 0, tzinfo=TPE)
    end = datetime(d.year, d.month, d.day, 13, 30, 0, tzinfo=TPE)
    return int(start.timestamp() * US), int(end.timestamp() * US)


def load_dataset(paths: Sequence[str], *, dates: Optional[set], symbols: Optional[set],
                 size_unit: str) -> Dataset:
    data = Dataset(size_unit=size_unit)
    raw_days: Dict[str, Dict[Tuple[str, str], List[tuple]]] = defaultdict(lambda: defaultdict(list))

    def add(symbol: str, market: str, t_us: Optional[int], serial: int, price: Optional[float],
            size: int, volume: Optional[int], bid: Optional[float], ask: Optional[float],
            name: str = "") -> None:
        if not symbol or not market or t_us is None or price is None or price <= 0:
            data.skipped["INVALID_ROW"] += 1
            return
        if symbols and symbol not in symbols:
            return
        day = us_to_dt(t_us).date().isoformat()
        if dates and day not in dates:
            return
        start, end = _session_bounds_us(day)
        if t_us < start or t_us > end:
            data.skipped["OUTSIDE_SESSION"] += 1
            return
        if name:
            data.names[symbol] = name
        bid = bid if bid is not None and bid > 0 else None
        ask = ask if ask is not None and ask > 0 else None
        raw_days[day][(market, symbol)].append((t_us, serial, price, max(0, size), volume, bid, ask))

    for path in _iter_files(paths):
        if path.suffix.lower() == ".jsonl":
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        data.skipped["BAD_JSON"] += 1
                        continue
                    if str(row.get("kind") or "TRADE").upper() != "TRADE":
                        continue
                    classification = str(row.get("event_classification") or row.get("classification") or "NEW").upper()
                    if classification != "NEW":
                        data.skipped[f"CLASS_{classification}"] += 1
                        continue
                    payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
                    add(str(row.get("symbol") or "").strip(), _normalize_market(row.get("market")),
                        parse_time_us(row.get("event_time")), _to_int(row.get("serial"), -1),
                        _to_float(row.get("price", payload.get("price"))),
                        _to_int(row.get("size", payload.get("size"))),
                        _to_int(row.get("volume", payload.get("volume")), -1),
                        _to_float(row.get("bid", payload.get("bid"))),
                        _to_float(row.get("ask", payload.get("ask"))),
                        str(row.get("name") or ""))
        else:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    row = {str(k).strip().lower(): v for k, v in row.items() if k}
                    add(str(row.get("symbol") or "").strip(), _normalize_market(row.get("market")),
                        parse_time_us(row.get("time") or row.get("event_time") or row.get("timestamp")),
                        _to_int(row.get("serial"), -1), _to_float(row.get("price")),
                        _to_int(row.get("size")), _to_int(row.get("volume"), -1),
                        _to_float(row.get("bid")), _to_float(row.get("ask")), str(row.get("name") or ""))

    for day, by_symbol in raw_days.items():
        out: Dict[Tuple[str, str], List[tuple]] = {}
        for key, ticks in by_symbol.items():
            ticks.sort(key=lambda t: (t[T_TIME], t[T_SERIAL]))
            cleaned: List[tuple] = []
            seen = set()
            for tick in ticks:
                ident = (tick[T_TIME], tick[T_SERIAL]) if tick[T_SERIAL] >= 0 else tick[:4]
                if ident in seen:
                    data.skipped["DUPLICATE"] += 1
                    continue
                seen.add(ident)
                cleaned.append(tick)
            # 原系統送單前要求 serial >= 0；CSV 沒有 serial 時依序編號。
            if any(t[T_SERIAL] < 0 for t in cleaned):
                cleaned = [t[:1] + (i,) + t[2:] for i, t in enumerate(cleaned)]
            out[key] = cleaned
        data.days[day] = out
    return data


@dataclass
class RefInfo:
    prev_close: Optional[float] = None
    day_trade_eligible: Optional[bool] = None
    sector: str = ""
    sector_code: str = ""
    name: str = ""


def _parse_bool(value: Any) -> Optional[bool]:
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "t", "可", "eligible"}:
        return True
    if text in {"0", "false", "no", "n", "f", "不可", "ineligible"}:
        return False
    return None


def load_reference(path: Optional[str]) -> Dict[Tuple[str, str], RefInfo]:
    """參考資料 CSV：date(可空白=每天適用), symbol, prev_close, day_trade_eligible, sector, sector_code, name。"""
    ref: Dict[Tuple[str, str], RefInfo] = {}
    if not path:
        return ref
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            row = {str(k).strip().lower(): v for k, v in row.items() if k}
            symbol = str(row.get("symbol") or "").strip()
            if not symbol:
                continue
            day = str(row.get("date") or "").strip()
            ref[(day, symbol)] = RefInfo(
                prev_close=_to_float(row.get("prev_close")),
                day_trade_eligible=_parse_bool(row.get("day_trade_eligible")),
                sector=str(row.get("sector") or "").strip(),
                sector_code=str(row.get("sector_code") or "").strip(),
                name=str(row.get("name") or "").strip(),
            )
    return ref


def resolve_reference(dataset: Dataset, ref: Mapping[Tuple[str, str], RefInfo],
                      *, assume_day_trade: bool) -> Tuple[Dict[str, Dict[Tuple[str, str], RefInfo]], List[str]]:
    """每個交易日、每檔股票的昨收／當沖資格／類股。昨收優先用參考檔，否則用資料中前一交易日最後成交價。"""
    warnings: List[str] = []
    resolved: Dict[str, Dict[Tuple[str, str], RefInfo]] = {}
    last_close: Dict[str, float] = {}
    missing_prev = Counter()
    for day in sorted(dataset.days):
        out: Dict[Tuple[str, str], RefInfo] = {}
        for key, ticks in dataset.days[day].items():
            symbol = key[1]
            dated = ref.get((day, symbol))
            static = ref.get(("", symbol))
            info = RefInfo()
            for source in (static, dated):
                if source is None:
                    continue
                for name in ("prev_close", "day_trade_eligible"):
                    if getattr(source, name) is not None:
                        setattr(info, name, getattr(source, name))
                for name in ("sector", "sector_code", "name"):
                    if getattr(source, name):
                        setattr(info, name, getattr(source, name))
            if info.prev_close is None and symbol in last_close:
                info.prev_close = last_close[symbol]
            if info.prev_close is None:
                missing_prev[symbol] += 1
            if info.day_trade_eligible is None and assume_day_trade:
                info.day_trade_eligible = True
            info.name = info.name or dataset.names.get(symbol, "")
            out[key] = info
        resolved[day] = out
        for key, ticks in dataset.days[day].items():
            if ticks:
                last_close[key[1]] = ticks[-1][T_PRICE]
    if missing_prev:
        warnings.append(
            f"{len(missing_prev)} 檔股票在部分日子缺昨收（共 {sum(missing_prev.values())} 檔日），"
            "這些股票當天無法計算漲幅，Scanner 不會選它。請用 --ref 提供 prev_close。")
    return resolved, warnings


# =============================================================================
# 大盤時間序列（選配）
# =============================================================================


DEFAULT_MARKET_ROW = {
    "market_state": "NORMAL",
    "intraday_trend": "UP",
    "market_score": 40.0,
    "breadth_net_ratio": 0.3,
    "taiex_change_pct": 0.0,
    "tpex_change_pct": 0.0,
}


class MarketTimeline:
    """依時間 as-of 取大盤狀態。沒有提供 CSV 時，大盤濾網視為全開（會寫進報告的假設清單）。"""

    def __init__(self, path: Optional[str]):
        self.rows: Dict[str, List[Tuple[int, Dict[str, Any]]]] = defaultdict(list)
        self.provided = bool(path)
        if not path:
            return
        with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                row = {str(k).strip().lower(): v for k, v in row.items() if k}
                stamp = row.get("time") or ""
                if row.get("date") and not re.search(r"\d{4}-\d{2}-\d{2}", stamp):
                    stamp = f"{row['date']}T{stamp}"
                t_us = parse_time_us(stamp)
                if t_us is None:
                    continue
                item = dict(DEFAULT_MARKET_ROW)
                for name in ("market_score", "breadth_net_ratio", "taiex_change_pct", "tpex_change_pct"):
                    value = _to_float(row.get(name))
                    if value is not None:
                        item[name] = value
                for name in ("market_state", "intraday_trend"):
                    if str(row.get(name) or "").strip():
                        item[name] = str(row[name]).strip().upper()
                self.rows[us_to_dt(t_us).date().isoformat()].append((t_us, item))
        for day in self.rows:
            self.rows[day].sort(key=lambda x: x[0])

    def at(self, day: str, t_us: int) -> Tuple[Dict[str, Any], bool]:
        if not self.provided:
            return dict(DEFAULT_MARKET_ROW), False
        rows = self.rows.get(day) or []
        chosen = None
        for stamp, item in rows:
            if stamp > t_us:
                break
            chosen = item
        if chosen is None:
            return dict(DEFAULT_MARKET_ROW, market_state="UNKNOWN"), True
        return dict(chosen), True


class IndexFeed:
    """指數分價（--index-csv）+ 日收盤（--index-daily-csv）。

    有這兩份資料時，大盤狀態改由原系統 market.calculate_market_context() 計算
    （指數漲跌、相對日線 MA5 的短線方向、市場廣度、類股），不再用固定假設值。
    """

    NAMES = ("TAIEX", "TPEX")

    def __init__(self, intraday_path: Optional[str], daily_path: Optional[str]):
        self.intraday: Dict[str, Dict[str, List[Tuple[int, float]]]] = defaultdict(lambda: defaultdict(list))
        self.daily: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
        self.provided = bool(intraday_path and daily_path)
        if not self.provided:
            return
        with Path(intraday_path).open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                row = {str(k).strip().lower(): v for k, v in row.items() if k}
                name = self._name(row.get("index"))
                stamp = row.get("time") or ""
                if row.get("date") and not re.search(r"\d{4}-\d{2}-\d{2}", stamp):
                    stamp = f"{row['date']}T{stamp}"
                t_us, price = parse_time_us(stamp), _to_float(row.get("price"))
                if name and t_us is not None and price:
                    self.intraday[us_to_dt(t_us).date().isoformat()][name].append((t_us, price))
        with Path(daily_path).open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                row = {str(k).strip().lower(): v for k, v in row.items() if k}
                name, close = self._name(row.get("index")), _to_float(row.get("close"))
                if name and close and row.get("date"):
                    self.daily[name].append((str(row["date"]).strip(), close))
        for by_name in self.intraday.values():
            for rows in by_name.values():
                rows.sort()
        for rows in self.daily.values():
            rows.sort()

    @staticmethod
    def _name(value: Any) -> str:
        text = str(value or "").strip().upper()
        return "TAIEX" if text in {"TAIEX", "TWSE", "^TWII", "IX0001"} else (
            "TPEX" if text in {"TPEX", "OTC", "IX0043"} else "")

    def has_day(self, day: str) -> bool:
        return self.provided and all(self.intraday.get(day, {}).get(n) for n in self.NAMES)

    def quote(self, day: str, name: str, t_us: int, now_iso: str) -> Optional[Dict[str, Any]]:
        rows = self.intraday.get(day, {}).get(name) or []
        prices = [price for stamp, price in rows if stamp <= t_us]
        previous = [close for d, close in self.daily.get(name, []) if d < day]
        if not prices or not previous:
            return None
        # 來源時間用本輪取得時間：實盤由 REST 快照每 30 秒刷新，分 K 只決定「數值」可能晚最多一分鐘。
        return {"symbol": name, "price": prices[-1], "open": prices[0], "high": max(prices), "low": min(prices),
                "prev_close": previous[-1], "timestamp": now_iso, "trade_date": day}

    def history(self, day: str, name: str) -> List[Dict[str, Any]]:
        return [{"price": close, "trade_date": d} for d, close in self.daily.get(name, []) if d < day][-20:]


# =============================================================================
# 成本
# =============================================================================


@dataclass(frozen=True)
class CostModel:
    commission_rate: float = 0.001425
    fee_discount: float = 0.28
    min_fee: float = 20.0
    tax_rate: float = 0.0015  # 現股當沖證交稅減半

    def commission(self, amount: float) -> float:
        return max(self.min_fee, math.floor(amount * self.commission_rate * self.fee_discount))

    def tax(self, amount: float) -> float:
        return float(math.floor(amount * self.tax_rate))


# =============================================================================
# 單日模擬
# =============================================================================


@dataclass
class SymState:
    market: str
    symbol: str
    name: str
    info: RefInfo
    buffer: Any
    price: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    volume: int = 0
    turnover: float = 0.0
    bid: Optional[float] = None
    ask: Optional[float] = None
    event_time: int = 0
    serial: int = -1
    book_event_time: int = 0
    last_eval_us: int = 0


@dataclass
class Order:
    side: str
    key: Tuple[str, str]
    quantity: int
    decided_us: int
    fill_after_us: int
    signal: Dict[str, Any]
    decision: Dict[str, Any]
    reason: str = ""
    est_cost: float = 0.0
    est_risk: float = 0.0
    sector: str = "UNKNOWN"


@dataclass
class TradeRecord:
    day: str
    symbol: str
    market: str
    name: str
    strategy: str
    setup: str
    signal_time: str
    signal_price: float
    entry_time: str
    entry_price: float
    entry_mid: Optional[float]
    quantity: int
    stop_price: float
    risk_per_share: float
    day_change_at_entry: Optional[float]
    buy_fee: float
    legs: List[Dict[str, Any]] = field(default_factory=list)
    max_price: float = 0.0
    min_price: float = 0.0
    remaining: int = 0


class StubGateway:
    """只提供 revalidate_buy_before_order 會讀的兩個方法；資料全來自重播當下的狀態。"""

    def __init__(self, sim: "DaySimulator"):
        self.sim = sim

    def get_strategy_market_context(self) -> Dict[str, Any]:
        return self.sim.market_context

    def get_quote(self, symbol: str, market: Any = None) -> Dict[str, Any]:
        key = (_normalize_market(market), str(symbol))
        state = self.sim.states.get(key)
        return self.sim.quote(state) if state else {}


class DaySimulator:
    def __init__(self, mods: Mapping[str, Any], params: Mapping[str, Any], day: str,
                 ticks: Mapping[Tuple[str, str], List[tuple]], ref: Mapping[Tuple[str, str], RefInfo],
                 timeline: MarketTimeline, config: Mapping[str, Any],
                 index_feed: Optional[IndexFeed] = None):
        self.intraday = mods["intraday"]
        self.market_mod = mods["market"]
        self.scanner = mods["scanner"]
        self.trading = mods["trading"]
        self.ip = params["intraday"]
        self.sp = params["scanner"]
        self.tp = params["trading"]
        self.mp = params["market"]
        self.day = day
        self.ticks = ticks
        self.timeline = timeline
        self.index_feed = index_feed if index_feed is not None and index_feed.has_day(day) else None
        industry_names = getattr(mods.get("api_gateway"), "_INDUSTRY_NAMES", {}) or {}
        self.sector_names = {key[1]: (info.sector or industry_names.get(info.sector_code, info.sector_code))
                             for key, info in ref.items() if info.sector or info.sector_code}
        self.market_log: List[Dict[str, Any]] = []
        self.cfg = config
        self.cost = CostModel(fee_discount=config["fee_discount"], min_fee=config["min_fee"],
                              tax_rate=config["tax_rate"])
        self.size_multiplier = 1000 if config["size_unit"] == "LOT" else 1
        self.capital = float(config["capital"])
        self.latency_us = int(config["latency_ms"] * 1000)
        self.extra_ticks = int(config["extra_slippage_ticks"])
        self.eval_interval_us = int(config["eval_min_interval_ms"] * 1000)
        self.skip_scanner = bool(config["skip_scanner"])
        self.watch_count = int(config["watch_count"])
        self.refresh_us = int(config["refresh_seconds"] * US)

        d = date.fromisoformat(day)
        self.session_start_us = int(datetime(d.year, d.month, d.day, 9, 0, tzinfo=TPE).timestamp() * US)
        hh, mm, ss = (int(x) for x in self.ip.exit_time.split(":"))
        self.exit_us = int(datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=TPE).timestamp() * US)
        self.trade_date_compact = day.replace("-", "")

        self.states: Dict[Tuple[str, str], SymState] = {}
        for key, info in ref.items():
            if key not in ticks:
                continue
            self.states[key] = SymState(market=key[0], symbol=key[1], name=info.name, info=info,
                                        buffer=self.intraday.IncrementalEventBuffer(self.ip))
        self.gateway = StubGateway(self)
        self.market_context: Dict[str, Any] = {}
        self.market_row: Dict[str, Any] = {}
        self.candidates: Dict[Tuple[str, str], Dict[str, Any]] = {}

        # 帳戶
        self.positions: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.pending: Dict[Tuple[str, str], Order] = {}
        self.open_trades: Dict[Tuple[str, str], TradeRecord] = {}
        self.realized_net = 0.0
        self.strategy_pnl: Dict[str, float] = defaultdict(float)
        self.symbol_entry_counts: Dict[str, int] = defaultdict(int)
        self.symbol_last_exit: Dict[str, str] = {}
        self.closed: List[Tuple[str, float]] = []
        self.clock_exit_done: set = set()

        # 輸出
        self.trades: List[Dict[str, Any]] = []
        self.signal_log: Dict[str, Dict[str, Any]] = {}
        self.exit_rejections: Counter = Counter()
        self.scan_stats: Counter = Counter()
        self.eval_count = 0
        self.warnings: Counter = Counter()

        # 同一 process 連跑多天時，清掉上一天的狀態機快取。
        self.intraday._STRATEGY_STATE_CACHE.clear()
        self.intraday.prune_calculation_cache()

    # ------------------------------------------------------------------ 行情狀態
    def _update_state(self, state: SymState, tick: tuple) -> None:
        t_us, serial, price, size, volume, bid, ask = tick
        record = {
            "symbol": state.symbol, "market": state.market, "trade_date": self.day,
            "event_time": t_us, "serial": serial, "price": price, "size": size,
            "volume": volume if volume is not None and volume >= 0 else None,
            "bid": bid, "ask": ask, "event_classification": "NEW", "source": "FUGLE_WEBSOCKET",
        }
        state.buffer.append_sequence([record], self.ip)
        if state.open is None:
            state.open = price
        state.high = price if state.high is None else max(state.high, price)
        state.low = price if state.low is None else min(state.low, price)
        state.price = price
        state.volume = volume if volume is not None and volume >= state.volume else state.volume + size
        state.turnover += price * size * self.size_multiplier
        state.event_time = t_us
        state.serial = serial
        if bid is not None and ask is not None and ask >= bid:
            state.bid, state.ask = bid, ask
            state.book_event_time = t_us

    def quote(self, state: SymState) -> Dict[str, Any]:
        return {
            "symbol": state.symbol, "market": state.market, "price": state.price, "last": state.price,
            "bid": state.bid, "ask": state.ask, "prev_close": state.info.prev_close,
            "timestamp": us_to_iso(state.event_time) if state.event_time else None,
            "event_time": state.event_time, "serial": state.serial,
        }

    def snapshot(self, state: SymState) -> Dict[str, Any]:
        """revalidate_buy_before_order 的 atomic MarketState。Book 時間 = 最近一筆帶 bid/ask 的成交時間。"""
        has_book = bool(state.book_event_time)
        return {
            "symbol": state.symbol, "market": state.market, "price": state.price, "last": state.price,
            "prev_close": state.info.prev_close, "event_time": state.event_time, "serial": state.serial,
            "volume": state.volume, "bid": state.bid, "ask": state.ask,
            "book_event_time": state.book_event_time or None, "book_active": True,
            "book_ready": has_book, "book_generation": 1,
            "quote_sequence": 0, "symbol_quote_sequence": 0,
        }

    def _tick(self, price: float, symbol: str) -> float:
        return float(self.trading._tick_size(price, symbol))

    # ------------------------------------------------------------------ 大盤 + Scanner
    def _scanner_rows(self, now_iso: str) -> List[Dict[str, Any]]:
        rows = []
        for state in self.states.values():
            if state.price is None:
                continue
            rows.append({
                "symbol": state.symbol, "market": state.market, "name": state.name,
                "price": state.price, "open": state.open, "high": state.high, "low": state.low,
                "prev_close": state.info.prev_close, "volume": state.volume,
                "volume_unit": "LOT" if self.size_multiplier == 1000 else "SHARE",
                "turnover": state.turnover, "bid": state.bid, "ask": state.ask,
                "event_time": state.event_time, "serial": state.serial,
                "timestamp": us_to_iso(state.event_time), "trade_date": self.day,
                "market_state_available": True, "market_state_source": "FUGLE_WEBSOCKET",
                "snapshot_acquired_at": now_iso,
                "day_trade_eligible": state.info.day_trade_eligible,
            })
        return rows

    def _sector_master(self) -> List[Dict[str, Any]]:
        return [{"symbol": s.symbol, "sector_name": self.sector_names.get(s.symbol, s.info.sector),
                 "sector_code": s.info.sector_code}
                for s in self.states.values() if s.info.sector or s.info.sector_code]

    def _finish_context(self, context: Dict[str, Any], rows: List[Dict[str, Any]], now: datetime,
                        now_iso: str) -> Dict[str, Any]:
        context = self.market_mod.attach_sector_contract(
            context, rows, self._sector_master(), parameters=self.mp, source=self.sp.sector_change_source,
            reference_at=now_iso)
        context["strategy_market_permissions"] = {"TREND": self.market_mod._trend_permission_policy(context)}
        return self.market_mod.resolve_strategy_market_context(context, now=now)

    def _context_from_indices(self, t_us: int, now: datetime, now_iso: str,
                              rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        feed = self.index_feed
        assert feed is not None
        market_data = {
            "taiex": feed.quote(self.day, "TAIEX", t_us, now_iso),
            "taiex_history": feed.history(self.day, "TAIEX"),
            "tpex": feed.quote(self.day, "TPEX", t_us, now_iso),
            "tpex_history": feed.history(self.day, "TPEX"),
            "twse_rows": [r for r in rows if r["market"] == "TWSE"],
            "tpex_rows": [r for r in rows if r["market"] != "TWSE"],
            "sector_map": dict(self.sector_names),
            "session_state": "REGULAR",
            "session_trade_date": self.trade_date_compact,
            "realtime_buy_data_ready": True,
            "breadth_snapshot_acquired_at": {"TWSE": now_iso, "TPEX": now_iso},
            "permission_evaluated_at": now_iso,
        }
        for name in ("taiex", "tpex"):
            if not market_data[f"{name}_history"]:
                self.warnings[f"INDEX_{name.upper()}_PREV_CLOSE_MISSING(缺前一日收盤)"] += 1
            elif len(market_data[f"{name}_history"]) < self.mp.ma_period:
                self.warnings[f"INDEX_{name.upper()}_HISTORY_SHORT(日收盤少於 MA 期數)"] += 1
        context = dict(self.market_mod.calculate_market_context(market_data, params=self.mp, now=now))
        context["market_snapshot"] = {"snapshot_id": context.get("data_fingerprint"), "stale": False,
                                      "publication_state": "PUBLISHED", "refreshed_at": now_iso,
                                      "session_trade_date": self.trade_date_compact}
        return self._finish_context(context, rows, now, now_iso)

    def _log_market(self, t_us: int) -> None:
        ctx = self.market_context
        self.market_log.append({
            "time": us_to_iso(t_us), "market_state": ctx.get("market_state"),
            "intraday_trend": ctx.get("intraday_trend"), "market_score": ctx.get("market_score"),
            "breadth_net_ratio": (ctx.get("breadth") or {}).get("net_ratio"),
            "taiex_change_pct": (ctx.get("taiex") or {}).get("change_pct"),
            "tpex_change_pct": (ctx.get("tpex") or {}).get("change_pct"),
            "momentum_allowed": ctx.get("momentum_long_allowed"),
            "trend_allowed": ctx.get("trend_long_allowed"), "trend_reason": ctx.get("trend_long_reason"),
            "candidates": len(self.candidates),
        })

    def refresh(self, t_us: int) -> None:
        self._refresh_market_and_scan(t_us)
        self._log_market(t_us)

    def _refresh_market_and_scan(self, t_us: int) -> None:
        now = us_to_dt(t_us)
        now_iso = now.isoformat(timespec="microseconds")
        if self.index_feed is not None:
            rows = self._scanner_rows(now_iso)
            self.market_context = self._context_from_indices(t_us, now, now_iso, rows)
            self._scan(rows, now_iso)
            return
        row, provided = self.timeline.at(self.day, t_us)
        self.market_row = row
        state = row["market_state"]
        if state not in {"BULL", "NORMAL", "WEAK", "PANIC"}:
            context = {"market_state": "UNKNOWN", "allow_long": False, "momentum_long_allowed": False,
                       "realtime_buy_data_ready": False, "parameters": self.mp.snapshot(),
                       "permission_evaluated_at": now_iso}
            self.market_context = self.market_mod.resolve_strategy_market_context(context, now=now)
            self.candidates = {}
            self.scan_stats["MARKET_DATA_MISSING"] += 1
            return
        mode = self.market_mod._default_trading_mode(state)
        allow_long, long_permission = self.market_mod._long_permission(state, mode)
        context: Dict[str, Any] = {
            "market_state": state, "auto_market_state": state,
            "panic_reason": "REPLAY_MARKET_CSV" if state == "PANIC" else "",
            "market_score": row["market_score"], "trading_mode": mode,
            "allow_long": allow_long, "long_permission": long_permission,
            "intraday_trend": row["intraday_trend"],
            "momentum_long_allowed": bool(allow_long and state != "PANIC"),
            "trend_long_allowed": False,
            "breadth": {"net_ratio": row["breadth_net_ratio"]},
            "taiex": {"available": True, "change_pct": row["taiex_change_pct"], "timestamp": now_iso},
            "tpex": {"available": True, "change_pct": row["tpex_change_pct"], "timestamp": now_iso},
            "parameters": self.mp.snapshot(),
            "asof": now_iso,
            "realtime_buy_data_ready": True,
            "session_state": "REGULAR",
            "session_trade_date": self.trade_date_compact,
            "breadth_snapshot_acquired_at": {"TWSE": now_iso, "TPEX": now_iso},
        }
        context["data_fingerprint"] = self.market_mod._stable_hash([self.day, t_us, row])
        context["market_snapshot"] = {"snapshot_id": context["data_fingerprint"], "stale": False,
                                      "publication_state": "PUBLISHED", "refreshed_at": now_iso,
                                      "session_trade_date": self.trade_date_compact}
        rows = self._scanner_rows(now_iso)
        self.market_context = self._finish_context(context, rows, now, now_iso)
        self._scan(rows, now_iso)

    def _scan(self, rows: List[Dict[str, Any]], now_iso: str) -> None:
        if self.skip_scanner:
            self.candidates = self._all_symbols_as_candidates(rows, now_iso)
            return
        day_trade_map = {s.symbol: s.info.day_trade_eligible for s in self.states.values()
                         if s.info.day_trade_eligible is not None}
        result = self.scanner.scan_candidates(
            rows, self.market_context, parameters=self.sp, day_trade_map=day_trade_map,
            session_phase="REGULAR", snapshot_reference_at=now_iso)
        picked = list(result.get("candidates") or [])[: self.watch_count]
        self.candidates = {(_normalize_market(c.get("market")), str(c.get("symbol"))): c for c in picked}
        self.scan_stats["ROUNDS"] += 1
        self.scan_stats["CANDIDATE_SLOTS"] += len(picked)

    def _all_symbols_as_candidates(self, rows: List[Dict[str, Any]], now_iso: str) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """--skip-scanner：跳過 Scanner 名次與分數門檻，但 RS／類股資格仍照原公式計算。"""
        day_trade_map = {s.symbol: s.info.day_trade_eligible for s in self.states.values()
                         if s.info.day_trade_eligible is not None}
        params = dataclasses.replace(self.sp, min_score=0.0, min_score_coverage=0.0, top_n=10_000,
                                     momentum_candidate_min_score=0.01, trend_candidate_min_score=0.01,
                                     min_volume=0, min_turnover=0.0, min_change=-100.0)
        result = self.scanner.scan_candidates(
            rows, self.market_context, parameters=params, day_trade_map=day_trade_map,
            session_phase="REGULAR", snapshot_reference_at=now_iso)
        return {(_normalize_market(c.get("market")), str(c.get("symbol"))): c
                for c in result.get("candidates") or []}

    # ------------------------------------------------------------------ 帳戶
    def _open_cost(self) -> float:
        return sum(float(p["entry_price"]) * int(p["managed_quantity"]) for p in self.positions.values())

    def account_state(self) -> Dict[str, Any]:
        pending_buys = [o for o in self.pending.values() if o.side == "BUY"]
        sector_counts: Dict[str, int] = defaultdict(int)
        for order in pending_buys:
            sector_counts[order.sector] += 1
        consecutive, cooldown = 0, None
        for stamp, net in sorted(self.closed):
            consecutive = consecutive + 1 if net < 0 else 0
            if consecutive >= self.tp.max_consecutive_losses:
                cooldown = (datetime.fromisoformat(stamp)
                            + timedelta(minutes=self.tp.loss_cooldown_minutes)).isoformat()
        equity = self.capital + self.realized_net
        return {
            "available_cash": equity - self._open_cost() - sum(o.est_cost for o in pending_buys),
            "equity": equity,
            "daily_trade_count": sum(self.symbol_entry_counts.values()),
            "daily_pnl": self.realized_net,
            "positions": [dict(p) for p in self.positions.values()],
            "open_orders": [{"symbol": o.key[1], "market": o.key[0], "side": "BUY", "status": "OPEN"}
                            for o in pending_buys],
            "pending_position_count": len(pending_buys),
            "pending_open_risk": sum(o.est_risk for o in pending_buys),
            "pending_sector_counts": dict(sector_counts),
            "symbol_entry_counts": dict(self.symbol_entry_counts),
            "symbol_last_exit": dict(self.symbol_last_exit),
            "consecutive_losses": consecutive,
            "loss_cooldown_until": cooldown,
            "strategy_daily_pnl": dict(self.strategy_pnl),
        }

    # ------------------------------------------------------------------ 訊號與決策
    def _log_signal(self, sig: Mapping[str, Any], state: SymState, t_us: int, stage: str,
                    codes: Sequence[str]) -> Dict[str, Any]:
        key = f"{self.day}|{state.symbol}|{sig.get('signal_fingerprint')}"
        entry = self.signal_log.get(key)
        if entry is None:
            entry = {
                "day": self.day, "symbol": state.symbol, "market": state.market,
                "strategy": sig.get("strategy_type"), "setup": sig.get("entry_setup_type"),
                "first_time": us_to_iso(t_us), "first_price": state.price,
                "attempts": 0, "accepted": False, "accepted_time": "",
                "codes": Counter(), "first_codes": "",
            }
            self.signal_log[key] = entry
        entry["attempts"] += 1
        for code in codes:
            entry["codes"][f"{stage}:{code}"] += 1
        if entry["attempts"] == 1:
            entry["first_codes"] = ";".join(f"{stage}:{c}" for c in codes) or "ACCEPTED"
        return entry

    def _evaluate(self, key: Tuple[str, str], state: SymState, t_us: int) -> None:
        position = self.positions.get(key)
        candidate = self.candidates.get(key)
        if position is None and candidate is None:
            return
        if key in self.pending:
            return
        if self.eval_interval_us and t_us - state.last_eval_us < self.eval_interval_us:
            return
        state.last_eval_us = t_us
        self.eval_count += 1
        signal = self.intraday.get_signal_context(
            symbol=state.symbol, market=state.market, candidate=candidate,
            market_context=self.market_context, quote_sequence=state.buffer,
            parameters=self.ip, position=position, include_aggregates=False)
        now = us_to_dt(t_us)
        if position is not None:
            if signal.get("exit_signal"):
                self._decide_sell(key, state, signal, t_us, now, str(signal.get("exit_stage") or ""))
            return
        if not signal.get("entry_signal"):
            return
        self._decide_buy(key, state, dict(signal), t_us, now)

    def _decide_buy(self, key, state: SymState, signal: Dict[str, Any], t_us: int, now: datetime) -> None:
        signal["execution_trigger"] = "AUTO"
        quote = self.quote(state)
        signal["candidate"] = self.market_mod.refresh_candidate_sector(
            signal.get("candidate"), signal["market_context"], now=now, quote=quote)
        account = self.account_state()
        decision = self.trading.evaluate_trading_decision(signal, quote, account, parameters=self.tp, now=now)
        if str(decision.get("decision")).upper() != "ALLOW":
            self._log_signal(signal, state, t_us, "DECISION", decision.get("rejection_codes") or ["REJECT"])
            return
        reval = self.trading.revalidate_buy_before_order(
            self.gateway, decision, signal, parameters=self.tp, now=now,
            market_state_snapshot=self.snapshot(state))
        if not reval.get("ok"):
            self._log_signal(signal, state, t_us, "REVALIDATION", reval.get("rejection_codes") or ["REJECT"])
            return
        sizing = reval.get("risk_guard") or {}
        quantity = int(sizing.get("position_size") or decision.get("position_size") or 0)
        if quantity <= 0:
            self._log_signal(signal, state, t_us, "REVALIDATION", ["POSITION_SIZE_ZERO"])
            return
        entry = self._log_signal(signal, state, t_us, "ACCEPTED", [])
        entry["accepted"] = True
        entry["accepted_time"] = entry["accepted_time"] or us_to_iso(t_us)
        order_price = float(reval.get("order_price") or decision.get("order_price") or state.price)
        stop = float(decision.get("effective_stop_price") or signal.get("strategy_stop_price") or 0)
        self.pending[key] = Order(
            side="BUY", key=key, quantity=quantity, decided_us=t_us,
            fill_after_us=t_us + self.latency_us, signal=signal, decision=decision,
            est_cost=order_price * quantity, est_risk=max(0.0, order_price - stop) * quantity,
            sector=str(decision.get("sector") or "UNKNOWN"))

    def _decide_sell(self, key, state: SymState, signal: Mapping[str, Any], t_us: int,
                     now: datetime, reason: str) -> None:
        account = self.account_state()
        decision = self.trading.evaluate_trading_decision(
            dict(signal), self.quote(state), account, parameters=self.tp, now=now)
        if str(decision.get("decision")).upper() != "ALLOW":
            for code in decision.get("rejection_codes") or ["REJECT"]:
                self.exit_rejections[f"{reason}:{code}"] += 1
            return
        quantity = int(decision.get("position_size") or 0)
        if quantity <= 0:
            self.exit_rejections[f"{reason}:SIZE_ZERO"] += 1
            return
        self.pending[key] = Order(side="SELL", key=key, quantity=quantity, decided_us=t_us,
                                  fill_after_us=t_us + self.latency_us, signal=dict(signal),
                                  decision=decision, reason=reason)

    def _clock_exits(self, t_us: int) -> None:
        now = us_to_dt(t_us)
        for key, position in list(self.positions.items()):
            if key in self.pending or key in self.clock_exit_done:
                continue
            self.clock_exit_done.add(key)
            state = self.states[key]
            signal = {"symbol": key[1], "market": key[0], "exit_signal": True, "exit_stage": "TIME_EXIT",
                      "exit_reason": "TIME_EXIT", "exit_fraction": 1.0,
                      "strategy_type": position.get("strategy_type"), "signal_price": state.price}
            self._decide_sell(key, state, signal, t_us, now, "CLOCK_TIME_EXIT")

    # ------------------------------------------------------------------ 成交
    def _fill_price(self, order: Order, tick: Optional[tuple], state: SymState) -> Tuple[float, str, Optional[float]]:
        if tick is not None:
            price, bid, ask = tick[T_PRICE], tick[T_BID], tick[T_ASK]
        else:
            price, bid, ask = state.price, state.bid, state.ask
        mid = (bid + ask) / 2 if bid is not None and ask is not None else None
        tick_size = self._tick(price, state.symbol)
        if order.side == "BUY":
            if ask is not None:
                fill, basis = ask, "ASK"
            else:
                fill, basis = price + tick_size, "LAST_PLUS_TICK"
            fill += self.extra_ticks * tick_size
        else:
            if bid is not None:
                fill, basis = bid, "BID"
            else:
                fill, basis = price - tick_size, "LAST_MINUS_TICK"
            fill -= self.extra_ticks * tick_size
        return round(fill, 4), basis if tick is not None else basis + "_EOD", mid

    def _fill(self, order: Order, state: SymState, t_us: int, tick: Optional[tuple]) -> None:
        del self.pending[order.key]
        price, basis, mid = self._fill_price(order, tick, state)
        if basis.startswith("LAST"):
            self.warnings["FILL_WITHOUT_BID_ASK"] += 1
        if order.side == "BUY":
            self._fill_buy(order, state, t_us, price, mid, basis)
        else:
            self._fill_sell(order, state, t_us, price, mid, basis)

    def _fill_buy(self, order: Order, state: SymState, t_us: int, price: float,
                  mid: Optional[float], basis: str) -> None:
        signal, decision = order.signal, order.decision
        ownership = {"owner": OWNER, "management_source": "REPLAY", "trade_date": self.trade_date_compact,
                     "trigger": "AUTO", "ownership_status": "SYSTEM"}
        for name in self.trading.STRATEGY_METADATA_FIELDS:
            value = decision.get(name)
            if value is None:
                value = signal.get(name)
            if value is not None:
                ownership[name] = value
        ownership["prev_close"] = state.info.prev_close
        position = self.trading.build_position_from_fill(
            {"average_fill_price": price, "filled_shares": order.quantity},
            symbol=state.symbol, market=state.market, parameters=self.tp,
            entry_time=us_to_iso(t_us), ownership=ownership)
        position["sector"] = order.sector
        position["prev_close"] = state.info.prev_close
        self.positions[order.key] = position
        self.symbol_entry_counts[state.symbol] += 1
        fee = self.cost.commission(price * order.quantity)
        self.realized_net -= fee
        strategy = str(position.get("strategy_type") or "UNKNOWN")
        self.strategy_pnl[strategy] -= fee
        stop = float(position.get("effective_stop_price") or 0)
        prev = state.info.prev_close
        self.open_trades[order.key] = TradeRecord(
            day=self.day, symbol=state.symbol, market=state.market, name=state.name,
            strategy=strategy, setup=str(signal.get("entry_setup_type") or ""),
            signal_time=us_to_iso(order.decided_us), signal_price=float(signal.get("signal_price") or 0),
            entry_time=us_to_iso(t_us), entry_price=price, entry_mid=mid, quantity=order.quantity,
            stop_price=stop, risk_per_share=max(0.0, price - stop),
            day_change_at_entry=(price / prev - 1) * 100 if prev else None,
            buy_fee=fee, max_price=price, min_price=price, remaining=order.quantity)
        self.open_trades[order.key].legs.append({"fill_basis": basis})

    def _fill_sell(self, order: Order, state: SymState, t_us: int, price: float,
                   mid: Optional[float], basis: str) -> None:
        position = self.positions.get(order.key)
        trade = self.open_trades.get(order.key)
        if position is None or trade is None:
            return
        quantity = min(order.quantity, int(position["managed_quantity"]))
        amount = price * quantity
        fee, tax = self.cost.commission(amount), self.cost.tax(amount)
        gross = (price - float(position["entry_price"])) * quantity
        self.realized_net += gross - fee - tax
        strategy = str(position.get("strategy_type") or "UNKNOWN")
        self.strategy_pnl[strategy] += gross - fee - tax
        trade.legs.append({"time": us_to_iso(t_us), "price": price, "mid": mid, "quantity": quantity,
                           "reason": order.reason, "fill_basis": basis, "fee": fee, "tax": tax,
                           "gross": gross})
        remaining = int(position["managed_quantity"]) - quantity
        if remaining > 0:
            for name in ("quantity", "managed_quantity", "managed_sellable_quantity", "remaining_quantity"):
                position[name] = remaining
            if order.reason == "MOMENTUM_DECAY_PARTIAL" or float(order.signal.get("exit_fraction") or 1) < 1:
                position["partial_exit_done"] = True
                position["partial_exit_stage"] = order.reason
            position["open_risk"] = remaining * max(0.0, float(position["entry_price"]) - float(position["effective_stop_price"]))
            trade.remaining = remaining
            return
        del self.positions[order.key]
        trade.remaining = 0
        self.symbol_last_exit[state.symbol] = us_to_iso(t_us)
        self._finalize(order.key, trade, t_us)

    def _finalize(self, key, trade: TradeRecord, t_us: int) -> None:
        del self.open_trades[key]
        sells = [leg for leg in trade.legs if "quantity" in leg]
        gross = sum(leg["gross"] for leg in sells)
        fees = trade.buy_fee + sum(leg["fee"] for leg in sells)
        tax = sum(leg["tax"] for leg in sells)
        net = gross - fees - tax
        self.closed.append((us_to_iso(t_us), net))
        cost_basis = trade.entry_price * trade.quantity
        exit_value = sum(leg["price"] * leg["quantity"] for leg in sells)
        mid_gross = None
        if trade.entry_mid is not None and all(leg.get("mid") is not None for leg in sells):
            mid_gross = sum((leg["mid"] - trade.entry_mid) * leg["quantity"] for leg in sells)
        risk = trade.risk_per_share * trade.quantity
        entry_dt = datetime.fromisoformat(trade.entry_time)
        exit_dt = datetime.fromisoformat(sells[-1]["time"])
        self.trades.append({
            "day": trade.day, "symbol": trade.symbol, "name": trade.name, "market": trade.market,
            "strategy": trade.strategy, "setup": trade.setup,
            "signal_time": trade.signal_time, "signal_price": trade.signal_price,
            "entry_time": trade.entry_time, "entry_price": trade.entry_price,
            "entry_fill_basis": trade.legs[0].get("fill_basis"),
            "quantity": trade.quantity, "stop_price": round(trade.stop_price, 4),
            "stop_distance_pct": round(trade.risk_per_share / trade.entry_price * 100, 4),
            "day_change_at_entry_pct": None if trade.day_change_at_entry is None else round(trade.day_change_at_entry, 3),
            "exit_time": sells[-1]["time"], "avg_exit_price": round(exit_value / trade.quantity, 4),
            "exit_reasons": "+".join(leg["reason"] for leg in sells),
            "final_exit_reason": sells[-1]["reason"],
            "partial_exit": len(sells) > 1,
            "hold_minutes": round((exit_dt - entry_dt).total_seconds() / 60, 2),
            "gross_pnl": round(gross, 2), "mid_gross_pnl": None if mid_gross is None else round(mid_gross, 2),
            "fees": round(fees, 2), "tax": round(tax, 2), "net_pnl": round(net, 2),
            "gross_ret_pct": round(gross / cost_basis * 100, 4),
            "net_ret_pct": round(net / cost_basis * 100, 4),
            "r_multiple": round(net / risk, 4) if risk > 0 else None,
            "mfe_pct": round((trade.max_price / trade.entry_price - 1) * 100, 4),
            "mae_pct": round((trade.min_price / trade.entry_price - 1) * 100, 4),
            "exit_legs": json.dumps([{k: leg[k] for k in ("time", "price", "quantity", "reason")}
                                     for leg in sells], ensure_ascii=False),
        })

    # ------------------------------------------------------------------ 主迴圈
    def run(self) -> Dict[str, Any]:
        keys = sorted(self.states)

        def stream(idx: int, ticks: List[tuple]) -> Iterable[Tuple[int, int, int, tuple]]:
            for tick in ticks:
                yield tick[T_TIME], tick[T_SERIAL], idx, tick

        streams = [stream(idx, self.ticks[key]) for idx, key in enumerate(keys)]
        next_refresh = self.session_start_us
        for t_us, _serial, idx, tick in heapq.merge(*streams, key=lambda x: (x[0], x[1], x[2])):
            while t_us >= next_refresh:
                self.refresh(next_refresh)
                if next_refresh >= self.exit_us:
                    self._clock_exits(next_refresh)
                next_refresh += self.refresh_us
            key = keys[idx]
            state = self.states[key]
            self._update_state(state, tick)
            trade = self.open_trades.get(key)
            if trade is not None:
                trade.max_price = max(trade.max_price, tick[T_PRICE])
                trade.min_price = min(trade.min_price, tick[T_PRICE])
            order = self.pending.get(key)
            if order is not None and t_us >= order.fill_after_us:
                self._fill(order, state, t_us, tick)
            if t_us >= self.exit_us:
                self._clock_exits(t_us)
            self._evaluate(key, state, t_us)

        # 收盤後還沒成交的委託與部位：用最後狀態強制了結（會在報告裡標記）。
        for key, order in list(self.pending.items()):
            self.warnings[f"EOD_UNFILLED_{order.side}"] += 1
            state = self.states[key]
            if order.side == "BUY":
                del self.pending[key]
            else:
                self._fill(order, state, state.event_time, None)
        for key in list(self.positions):
            state = self.states[key]
            self.warnings["EOD_FORCED_CLOSE"] += 1
            order = Order(side="SELL", key=key, quantity=int(self.positions[key]["managed_quantity"]),
                          decided_us=state.event_time, fill_after_us=state.event_time, signal={},
                          decision={}, reason="EOD_FORCED_CLOSE")
            self.pending[key] = order
            self._fill(order, state, state.event_time, None)

        signals = []
        for entry in self.signal_log.values():
            row = dict(entry)
            row["codes"] = ";".join(f"{k}x{v}" for k, v in entry["codes"].most_common())
            signals.append(row)
        return {
            "day": self.day, "trades": self.trades, "signals": signals,
            "exit_rejections": dict(self.exit_rejections), "scan_stats": dict(self.scan_stats),
            "eval_count": self.eval_count, "warnings": dict(self.warnings),
            "market_log": self.market_log, "market_source": "INDEX_FEED" if self.index_feed else (
                "MARKET_CSV" if self.timeline.provided else "ASSUMED_OPEN"),
            "realized_net": self.realized_net, "symbols": len(self.states),
            "ticks": sum(len(v) for v in self.ticks.values()),
        }


# =============================================================================
# 平行化（以交易日為單位）
# =============================================================================

_WORKER: Dict[str, Any] = {}


def _worker_init(repo: str, params_json: Optional[str], overrides: Sequence[str], fix: bool) -> None:
    mods = load_modules(repo)
    if fix:
        apply_momentum5m_fix(mods["intraday"])
    _WORKER["mods"] = mods
    _WORKER["params"] = load_parameters(mods, params_json, overrides)


def _worker_run(job: Tuple[str, Dict, Dict, Optional[str], Dict]) -> Dict[str, Any]:
    day, ticks, ref, market_csv, config = job
    started = time_module.perf_counter()
    sim = DaySimulator(_WORKER["mods"], _WORKER["params"], day, ticks, ref,
                       MarketTimeline(market_csv), config,
                       IndexFeed(config.get("index_csv"), config.get("index_daily_csv")))
    result = sim.run()
    result["seconds"] = round(time_module.perf_counter() - started, 2)
    return result


# =============================================================================
# 統計
# =============================================================================


def _mean(values: Sequence[float]) -> Optional[float]:
    return statistics.fmean(values) if values else None


MIN_TRADES_FOR_CI = 20


def bootstrap_ci(values: Sequence[float], *, iterations: int = 2000, seed: int = 7) -> Tuple[Optional[float], Optional[float]]:
    """每筆平均報酬的 bootstrap 95% 信賴區間；筆數太少時區間沒有意義，回傳 None。"""
    if len(values) < MIN_TRADES_FOR_CI:
        return None, None
    rng = random.Random(seed)
    n = len(values)
    means = sorted(statistics.fmean(rng.choices(values, k=n)) for _ in range(iterations))
    return means[int(0.025 * iterations)], means[int(0.975 * iterations) - 1]


def summarize(trades: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    if not trades:
        return {"trades": 0}
    net = [t["net_pnl"] for t in trades]
    ret = [t["net_ret_pct"] for t in trades]
    gross_ret = [t["gross_ret_pct"] for t in trades]
    wins = [r for r in ret if r > 0]
    losses = [r for r in ret if r <= 0]
    avg_win = _mean(wins)
    avg_loss = abs(_mean(losses)) if losses else None
    payoff = avg_win / avg_loss if avg_win is not None and avg_loss else None
    lo, hi = bootstrap_ci(ret)
    r_values = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
    gross_sum = sum(t["gross_pnl"] for t in trades)
    mid_trades = [t for t in trades if t.get("mid_gross_pnl") is not None]
    spread_cost = sum(t["mid_gross_pnl"] - t["gross_pnl"] for t in mid_trades)
    profits = sum(x for x in net if x > 0)
    losses_sum = -sum(x for x in net if x < 0)
    return {
        "trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2),
        "avg_net_ret_pct": round(statistics.fmean(ret), 4),
        "avg_net_ret_ci95": [None if lo is None else round(lo, 4), None if hi is None else round(hi, 4)],
        "median_net_ret_pct": round(statistics.median(ret), 4),
        "avg_gross_ret_pct": round(statistics.fmean(gross_ret), 4),
        "avg_win_pct": None if avg_win is None else round(avg_win, 4),
        "avg_loss_pct": None if avg_loss is None else round(-avg_loss, 4),
        "payoff_ratio": None if payoff is None else round(payoff, 3),
        "breakeven_win_rate_pct": None if payoff is None else round(100 / (1 + payoff), 2),
        "profit_factor": None if not losses_sum else round(profits / losses_sum, 3),
        "avg_r": None if not r_values else round(statistics.fmean(r_values), 4),
        "total_net_pnl": round(sum(net), 0),
        "total_gross_pnl": round(gross_sum, 0),
        "total_fees": round(sum(t["fees"] for t in trades), 0),
        "total_tax": round(sum(t["tax"] for t in trades), 0),
        "spread_cost_vs_mid": round(spread_cost, 0) if mid_trades else None,
        "avg_hold_minutes": round(statistics.fmean(t["hold_minutes"] for t in trades), 2),
        "avg_mfe_pct": round(statistics.fmean(t["mfe_pct"] for t in trades), 4),
        "avg_mae_pct": round(statistics.fmean(t["mae_pct"] for t in trades), 4),
        "avg_stop_distance_pct": round(statistics.fmean(t["stop_distance_pct"] for t in trades), 4),
        "exit_reasons": dict(Counter(t["final_exit_reason"] for t in trades).most_common()),
    }


def equity_stats(trades: Sequence[Mapping[str, Any]], days: Sequence[str]) -> Dict[str, Any]:
    ordered = sorted(trades, key=lambda t: t["exit_time"])
    peak = curve = drawdown = 0.0
    for t in ordered:
        curve += t["net_pnl"]
        peak = max(peak, curve)
        drawdown = min(drawdown, curve - peak)
    by_day: Dict[str, float] = {d: 0.0 for d in days}
    for t in trades:
        by_day[t["day"]] = by_day.get(t["day"], 0.0) + t["net_pnl"]
    daily = list(by_day.values())
    sd = statistics.stdev(daily) if len(daily) > 1 else None
    return {
        "days": len(daily),
        "total_net_pnl": round(sum(daily), 0),
        "max_drawdown": round(drawdown, 0),
        "avg_daily_pnl": round(statistics.fmean(daily), 0) if daily else None,
        "daily_pnl_sd": None if sd is None else round(sd, 0),
        "daily_sharpe_annualized": None if not sd else round(statistics.fmean(daily) / sd * math.sqrt(250), 2),
        "positive_days": sum(1 for x in daily if x > 0),
        "worst_day": round(min(daily), 0) if daily else None,
        "best_day": round(max(daily), 0) if daily else None,
    }


def grouped(trades: Sequence[Mapping[str, Any]], key: str) -> Dict[str, Any]:
    groups: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for t in trades:
        groups[str(t.get(key) or "")].append(t)
    return {name: summarize(rows) for name, rows in sorted(groups.items())}


def signal_funnel(signals: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    by_setup: Dict[str, Counter] = defaultdict(Counter)
    blockers: Counter = Counter()
    for s in signals:
        name = f"{s.get('strategy')}/{s.get('setup')}"
        by_setup[name]["signals"] += 1
        by_setup[name]["accepted"] += int(bool(s.get("accepted")))
        if not s.get("accepted"):
            for part in str(s.get("codes") or "").split(";"):
                if part:
                    blockers[part.rsplit("x", 1)[0]] += 1
    return {"by_setup": {k: dict(v) for k, v in sorted(by_setup.items())},
            "top_blockers_of_never_accepted_signals": dict(blockers.most_common(15))}


# =============================================================================
# 報告
# =============================================================================


def _fmt(value: Any, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.{digits}f}{suffix}"
    if isinstance(value, int):
        return f"{value:,}{suffix}"
    return f"{value}{suffix}"


def write_report(out: Path, summary: Mapping[str, Any]) -> None:
    lines: List[str] = []
    add = lines.append
    overall = summary["overall"]
    add("# Momentum_Scalping 逐筆重播評估")
    add("")
    add(f"- 期間：{summary['first_day']} ～ {summary['last_day']}（{summary['equity']['days']} 個交易日，"
        f"{summary['symbols']} 檔股票，{summary['ticks']:,} 筆成交）")
    add(f"- 程式版本：{', '.join(f'{k}={v}' for k, v in summary['modules'].items())}")
    add(f"- momentum_5m 修正：{'已套用（--fix-momentum5m）' if summary['config']['fix_momentum5m'] else '未套用（原始程式行為）'}")
    add(f"- 成本：手續費 0.1425% × {summary['config']['fee_discount']}（最低 {summary['config']['min_fee']:.0f} 元）"
        f"、當沖稅 {summary['config']['tax_rate'] * 100:.2f}%；延遲 {summary['config']['latency_ms']} ms；"
        f"額外滑價 {summary['config']['extra_slippage_ticks']} tick")
    add("")
    add("## 總結")
    add("")
    if not overall.get("trades"):
        add("沒有任何成交。請看下方「訊號漏斗」與「Scanner」統計，確認是哪一層擋掉。")
    else:
        lo, hi = overall["avg_net_ret_ci95"]
        add("| 指標 | 數值 |")
        add("|---|---|")
        add(f"| 交易筆數 | {overall['trades']} |")
        add(f"| 勝率（扣成本後） | {_fmt(overall['win_rate_pct'], suffix='%')} |")
        add(f"| 平均賺 / 平均賠 | {_fmt(overall['avg_win_pct'], 3, '%')} / {_fmt(overall['avg_loss_pct'], 3, '%')} |")
        add(f"| 盈虧比 → 不虧所需勝率 | {_fmt(overall['payoff_ratio'], 2)} → {_fmt(overall['breakeven_win_rate_pct'], suffix='%')} |")
        add(f"| 每筆平均報酬（扣成本前 → 後） | {_fmt(overall['avg_gross_ret_pct'], 3, '%')} → **{_fmt(overall['avg_net_ret_pct'], 3, '%')}** |")
        add(f"| 每筆平均報酬 95% 信賴區間 | {_fmt(lo, 3, '%')} ～ {_fmt(hi, 3, '%')} |")
        add(f"| 平均 R 倍數 | {_fmt(overall['avg_r'], 3)} |")
        add(f"| 獲利因子 | {_fmt(overall['profit_factor'], 2)} |")
        add(f"| 淨損益（元） | {_fmt(overall['total_net_pnl'], 0)} |")
        add(f"| 成本拆解（元）：價差 / 手續費 / 稅 | {_fmt(overall['spread_cost_vs_mid'], 0)} / {_fmt(overall['total_fees'], 0)} / {_fmt(overall['total_tax'], 0)} |")
        eq = summary["equity"]
        add(f"| 最大回撤（元） | {_fmt(eq['max_drawdown'], 0)} |")
        add(f"| 賺錢天數 | {eq['positive_days']} / {eq['days']} |")
        add(f"| 平均停損距離 / 平均持有 | {_fmt(overall['avg_stop_distance_pct'], 3, '%')} / {_fmt(overall['avg_hold_minutes'], 1)} 分 |")
        add(f"| 平均最大有利 / 不利波動 | {_fmt(overall['avg_mfe_pct'], 3, '%')} / {_fmt(overall['avg_mae_pct'], 3, '%')} |")
        add("")
        verdict = (f"交易少於 {MIN_TRADES_FOR_CI} 筆，不計算信賴區間，也不下結論。" if lo is None else
                   "信賴區間整段在 0 以上：在這段資料上有正期望值。" if lo > 0 else
                   "信賴區間整段在 0 以下：在這段資料上是負期望值。" if hi < 0 else
                   "信賴區間跨過 0：樣本不足以判斷有沒有優勢。")
        add(f"**判讀**：{verdict}（{overall['trades']} 筆；筆數少於約 200 時，結論都很不穩定。）")
    add("")
    add("## 依進場型態")
    add("")
    add("| 策略 / 型態 | 筆數 | 勝率 | 每筆淨報酬 | 95% CI | 盈虧比 | 不虧所需勝率 | 平均 R |")
    add("|---|---|---|---|---|---|---|---|")
    for name, s in summary["by_setup"].items():
        if not s.get("trades"):
            continue
        lo, hi = s["avg_net_ret_ci95"]
        add(f"| {name} | {s['trades']} | {_fmt(s['win_rate_pct'], suffix='%')} | {_fmt(s['avg_net_ret_pct'], 3, '%')} | "
            f"{_fmt(lo, 3)} ～ {_fmt(hi, 3)} | {_fmt(s['payoff_ratio'], 2)} | {_fmt(s['breakeven_win_rate_pct'], suffix='%')} | {_fmt(s['avg_r'], 3)} |")
    add("")
    add("## 出場原因")
    add("")
    add("| 原因 | 筆數 |")
    add("|---|---|")
    for name, count in (overall.get("exit_reasons") or {}).items():
        add(f"| {name} | {count} |")
    add("")
    add("## 訊號漏斗（策略產生的進場訊號，有多少真的送單）")
    add("")
    add("| 策略 / 型態 | 訊號數 | 通過全部閘門 |")
    add("|---|---|---|")
    for name, row in summary["funnel"]["by_setup"].items():
        add(f"| {name} | {row.get('signals', 0)} | {row.get('accepted', 0)} |")
    add("")
    add("從未通過的訊號，最常見的阻擋原因（階段:代碼）：")
    add("")
    for name, count in summary["funnel"]["top_blockers_of_never_accepted_signals"].items():
        add(f"- `{name}`：{count}")
    add("")
    add("## 假設與限制（解讀前務必看）")
    add("")
    for item in summary["assumptions"]:
        add(f"- {item}")
    if summary["warnings"]:
        add("")
        add("## 警告")
        add("")
        for item in summary["warnings"]:
            add(f"- {item}")
    add("")
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    names: List[str] = []
    for row in rows:
        for key in row:
            if key not in names:
                names.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=names)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in names})


# =============================================================================
# CLI
# =============================================================================


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Momentum_Scalping 逐筆重播評估：直接呼叫原系統的 Scanner / Intraday / Trading，量測扣成本後期望值。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--repo", required=True, help="Momentum_Scalping 程式資料夾（含 intraday_v*.py 等）")
    p.add_argument("--data", required=True, nargs="+",
                   help="逐筆資料：api_replay/fugle_trades.jsonl、CSV，或含這些檔案的資料夾")
    p.add_argument("--ref", help="參考資料 CSV：date,symbol,prev_close,day_trade_eligible,sector,sector_code,name")
    p.add_argument("--market-csv", help="大盤時間序列 CSV：date,time,taiex_change_pct,tpex_change_pct,"
                                        "breadth_net_ratio,market_score,market_state,intraday_trend")
    p.add_argument("--index-csv", help="指數分價 CSV：date,time,index(TAIEX/TPEX),price；"
                                       "與 --index-daily-csv 一起給時，大盤由原 market 模組計算")
    p.add_argument("--index-daily-csv", help="指數日收盤 CSV：date,index,close（昨收與日線 MA5 用）")
    p.add_argument("--params-json", help="GUI 參數檔 gui_parameters/latest.json；不給則用程式預設值")
    p.add_argument("--set", action="append", default=[], metavar="MODULE.NAME=VALUE",
                   help="覆寫單一參數，例如 --set trading.max_spread_pct=0.5（可重複）")
    p.add_argument("--out", default="replay_out", help="輸出資料夾")
    p.add_argument("--capital", type=float, default=1_000_000, help="每天起始權益（元）")
    p.add_argument("--latency-ms", type=float, default=500, help="決策到成交的延遲")
    p.add_argument("--extra-slippage-ticks", type=int, default=0, help="成交價再多吃幾個跳動點（壓力測試用）")
    p.add_argument("--fee-discount", type=float, default=0.28, help="手續費折數（0.28 = 2.8 折）")
    p.add_argument("--min-fee", type=float, default=20, help="每筆最低手續費")
    p.add_argument("--tax-rate", type=float, default=0.0015, help="當沖證交稅率")
    p.add_argument("--size-unit", choices=["LOT", "SHARE"], default="LOT",
                   help="資料 size 的單位（Fugle 一般盤為張 LOT）")
    p.add_argument("--refresh-seconds", type=float, default=30, help="大盤與 Scanner 重算週期（同 gui.market_refresh_seconds）")
    p.add_argument("--watch-count", type=int, default=30, help="同 gui.intraday_watch_count")
    p.add_argument("--eval-min-interval-ms", type=float, default=0,
                   help="同一檔股票兩次策略評估的最短間隔；0 = 每筆成交都評估（最貼近實盤，最慢）")
    p.add_argument("--skip-scanner", action="store_true",
                   help="跳過 Scanner 的名次與分數門檻，把所有股票都當候選（錄的股票很少時用）")
    p.add_argument("--assume-day-trade-eligible", action=argparse.BooleanOptionalAction, default=True,
                   help="參考檔沒寫當沖資格時，視為可當沖")
    p.add_argument("--fix-momentum5m", action="store_true", help="套用 momentum_5m 出場條件修正，與原始版比較用")
    p.add_argument("--dates", nargs="*", help="只跑這些日期（YYYY-MM-DD）")
    p.add_argument("--symbols", nargs="*", help="只載入這些股票代號")
    p.add_argument("--workers", type=int, default=1, help="平行處理的交易日數")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # Windows cp950 主控台遇到少見字元時不要中斷
        except AttributeError:
            pass
    started = time_module.perf_counter()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    mods = load_modules(args.repo)
    params = load_parameters(mods, args.params_json, args.set)
    print(f"[載入] 模組 {mods['_names']}", flush=True)

    dataset = load_dataset(args.data, dates=set(args.dates) if args.dates else None,
                           symbols=set(args.symbols) if args.symbols else None, size_unit=args.size_unit)
    if not dataset.days:
        raise SystemExit("沒有讀到任何 09:00–13:30 的成交資料。")
    ref = load_reference(args.ref)
    resolved, warnings = resolve_reference(dataset, ref, assume_day_trade=args.assume_day_trade_eligible)
    timeline = MarketTimeline(args.market_csv)
    print(f"[資料] {len(dataset.days)} 天，跳過 {dict(dataset.skipped)}", flush=True)

    config = {
        "capital": args.capital, "latency_ms": args.latency_ms, "extra_slippage_ticks": args.extra_slippage_ticks,
        "fee_discount": args.fee_discount, "min_fee": args.min_fee, "tax_rate": args.tax_rate,
        "size_unit": args.size_unit, "refresh_seconds": args.refresh_seconds, "watch_count": args.watch_count,
        "eval_min_interval_ms": args.eval_min_interval_ms, "skip_scanner": args.skip_scanner,
        "fix_momentum5m": args.fix_momentum5m,
        "index_csv": args.index_csv, "index_daily_csv": args.index_daily_csv,
    }
    if bool(args.index_csv) != bool(args.index_daily_csv):
        raise SystemExit("--index-csv 與 --index-daily-csv 必須一起提供")
    jobs = [(day, dataset.days[day], resolved[day], args.market_csv, config) for day in sorted(dataset.days)]
    results: List[Dict[str, Any]] = []
    if args.workers > 1 and len(jobs) > 1:
        with multiprocessing.Pool(args.workers, initializer=_worker_init,
                                  initargs=(args.repo, args.params_json, args.set, args.fix_momentum5m)) as pool:
            for result in pool.imap(_worker_run, jobs):
                results.append(result)
                _print_day(result)
    else:
        _worker_init(args.repo, args.params_json, args.set, args.fix_momentum5m)
        for job in jobs:
            result = _worker_run(job)
            results.append(result)
            _print_day(result)

    trades = [t for r in results for t in r["trades"]]
    signals = [s for r in results for s in r["signals"]]
    days = [r["day"] for r in results]
    run_warnings = Counter()
    for r in results:
        run_warnings.update(r["warnings"])
    exit_rejections = Counter()
    for r in results:
        exit_rejections.update(r["exit_rejections"])
    scan_stats = Counter()
    for r in results:
        scan_stats.update(r["scan_stats"])

    for t in trades:
        t["strategy_setup"] = f"{t['strategy']}/{t['setup']}"
    assumptions = [
        "成交：決策後經過延遲，用該股下一筆成交事件的 ask 買、bid 賣；不模擬五檔深度與部分成交，"
        "部位越大越樂觀。",
        "五檔來源：用 Fugle 成交事件附帶的 bid/ask 當作當下最佳一檔；送單前的 Book 新鮮度因此一定通過（實盤可能被擋）。",
        ("大盤：用指數分價與日收盤，交給原系統 market.calculate_market_context 計算"
         "（指數分數、相對日線 MA5 的短線方向、急跌保護）；市場廣度與類股強度只用資料內的股票計算。"
         if args.index_csv else
         "大盤：使用 --market-csv 提供的時間序列。" if timeline.provided else
         "大盤：**沒有提供指數資料，大盤濾網視為全開**（NORMAL、短線 UP、指數漲跌 0%）。"
         "實盤在弱勢／急跌日會少做很多單，這裡的結果偏樂觀。"),
        "類股強度：只用資料裡有錄到的股票計算（實盤用 500 檔 Universe），錄得越少越不準；"
        "沒有給 sector 的股票，Trend 一律不會進場（同實盤的 SECTOR_MAPPING_MISSING）。",
        ("Scanner：已跳過名次與分數門檻（--skip-scanner），所有股票都當候選，訊號數是上限。"
         if args.skip_scanner else
         "Scanner：只用資料裡有的股票排名；實盤是 500 檔裡挑前 30，若只錄了少數股票，候選名單會不同。"),
        "量比：原系統 Runtime 沒有提供 volume_ratio，這裡同樣不提供（與實盤一致）。",
        f"資金：每天從 {args.capital:,.0f} 元重新開始，不複利；部位大小完全由原 Trading 模組計算。",
        "時鐘清倉：與 Runtime 相同，在 exit_time 對所有持倉送出 TIME_EXIT。",
    ]
    all_warnings = list(warnings)
    if run_warnings:
        all_warnings.append("執行期警告：" + ", ".join(f"{k}={v}" for k, v in run_warnings.most_common()))
    if exit_rejections:
        all_warnings.append("出場訊號被 Trading 擋下（例如不足一張無法賣）：" +
                            ", ".join(f"{k}={v}" for k, v in exit_rejections.most_common(10)))

    summary = {
        "first_day": days[0], "last_day": days[-1],
        "symbols": len({(t[0], t[1]) for r in dataset.days.values() for t in r}),
        "ticks": sum(r["ticks"] for r in results),
        "modules": mods["_names"],
        "config": config,
        "overall": summarize(trades),
        "equity": equity_stats(trades, days),
        "by_strategy": grouped(trades, "strategy"),
        "by_setup": grouped(trades, "strategy_setup"),
        "funnel": signal_funnel(signals),
        "scan_stats": dict(scan_stats),
        "assumptions": assumptions,
        "warnings": all_warnings,
        "parameters": {k: dataclasses.asdict(v) for k, v in params.items() if dataclasses.is_dataclass(v)},
        "elapsed_seconds": round(time_module.perf_counter() - started, 1),
    }
    write_csv(out / "trades.csv", trades)
    write_csv(out / "signals.csv", signals)
    write_csv(out / "market_log.csv", [dict(day=r["day"], **row) for r in results for row in r["market_log"]])
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    write_report(out, summary)

    o = summary["overall"]
    print("", flush=True)
    if o.get("trades"):
        lo, hi = o["avg_net_ret_ci95"]
        print(f"[結果] {o['trades']} 筆｜勝率 {_fmt(o['win_rate_pct'], suffix='%')}｜盈虧比 {_fmt(o['payoff_ratio'])}"
              f"（不虧需勝率 {_fmt(o['breakeven_win_rate_pct'], suffix='%')}）｜每筆淨報酬 {_fmt(o['avg_net_ret_pct'], 3, '%')}"
              f"（95% CI {_fmt(lo, 3)} ~ {_fmt(hi, 3)}）｜淨損益 {_fmt(o['total_net_pnl'], 0)} 元")
    else:
        print("[結果] 沒有任何成交，請看 report.md 的訊號漏斗。")
    print(f"[輸出] {out.resolve()}（report.md / trades.csv / signals.csv / summary.json），"
          f"耗時 {summary['elapsed_seconds']} 秒", flush=True)
    return 0


def _print_day(result: Mapping[str, Any]) -> None:
    pnl = sum(t["net_pnl"] for t in result["trades"])
    print(f"[{result['day']}] {result['symbols']} 檔 {result['ticks']:,} 筆成交，評估 {result['eval_count']:,} 次，"
          f"成交 {len(result['trades'])} 筆，淨損益 {pnl:,.0f} 元（{result.get('seconds')} 秒）", flush=True)


if __name__ == "__main__":
    sys.exit(main())
