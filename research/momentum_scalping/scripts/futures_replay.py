#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
futures_replay.py — 把 Momentum_Scalping 的盤中策略套到台指期（期交所真實逐筆成交）

股票版（ms_replay.py）跑的是整套 Scanner → Intraday → Trading；指數期貨沒有選股、
沒有現股當沖、也沒有 150 元價格上限，所以這裡只沿用：

    進出場訊號     intraday.get_signal_context()（Momentum / Trend 狀態機、結構停損、
                   動能衰退、EMA 死叉、13:00 清倉、2.5% 緊急停損，全部原封不動）
    送單前驗證     trading._entry_wave_extension_revalidation()、_entry_momentum_revalidation()
                   ＋追價上限、結構停損距離上限（同 Trading 參數）
    帳戶規則       同股每日最多進場次數、平倉後冷卻、連虧冷卻（同 Trading 參數）
    大盤許可       選配：讀股票版輸出的 market_log.csv，Momentum／Trend 被大盤擋時不進場

刻意改掉的一條：個股「相對大盤強度 > 0」對指數本身沒有意義（指數相對自己永遠是 0），
這裡固定視為通過，報告會寫出來。

資料：期交所「期貨每筆成交資料」Daily_YYYY_MM_DD.zip（近 30 個交易日可下載），
欄位 成交日期, 商品代號, 到期月份, 成交時間(HHMMSS), 成交價格, 成交數量(B+S)。
只取一般交易時段 08:45–13:45、當天成交量最大的單一月份合約（不含價差單）。
檔案沒有委買委賣價，買進用「下一筆成交價 + N 跳動點」、賣出用「− N 跳動點」近似。

用法：
  python futures_replay.py --repo C:\\Momentum_Scalping --taifex taifex_raw --product MTX \\
      --market-log result/market_log.csv --out futures_out
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_replay as M  # noqa: E402

PRODUCTS = {"TX": 200, "MTX": 50, "TMF": 10}  # 每點價值（元）


# --------------------------------------------------------------------------- 資料
def read_taifex(folder: Path, product: str) -> Dict[str, Dict[str, Any]]:
    """回傳 {day: {"contract", "ticks": [(t_us, serial, price, size)], "close"}}。"""
    out: Dict[str, Dict[str, Any]] = {}
    files = sorted(folder.glob("Daily_*.csv"))
    if not files:
        raise SystemExit(f"{folder} 底下沒有 Daily_*.csv（請先解壓期交所的 zip）")
    for path in files:
        raw = path.read_bytes()
        text = None
        for encoding in ("utf-8-sig", "big5", "cp950"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise SystemExit(f"無法解碼 {path}")
        rows: Dict[str, Dict[str, List[Tuple[str, float, int]]]] = defaultdict(lambda: defaultdict(list))
        for row in csv.reader(text.splitlines()[1:]):
            if len(row) < 6 or row[1].strip() != product:
                continue
            day, contract, hhmmss = row[0].strip(), row[2].strip(), row[3].strip().zfill(6)
            if "/" in contract or not ("084500" <= hhmmss <= "134500"):
                continue
            rows[day][contract].append((hhmmss, float(row[4]), int(row[5])))
        for day_compact, by_contract in rows.items():
            contract = max(by_contract, key=lambda c: sum(x[2] for x in by_contract[c]))
            day = f"{day_compact[:4]}-{day_compact[4:6]}-{day_compact[6:]}"
            ticks = []
            per_second: Counter = Counter()
            for serial, (hhmmss, price, size) in enumerate(by_contract[contract]):
                base = datetime.fromisoformat(f"{day}T{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:]}").replace(tzinfo=M.TPE)
                # 檔案只到秒；同一秒內依檔案順序排開（每筆 1 微秒），維持原始先後。
                t_us = int(base.timestamp() * M.US) + per_second[hhmmss]
                per_second[hhmmss] += 1
                ticks.append((t_us, serial, price, size))
            closes = {c: v[-1][1] for c, v in by_contract.items() if v}
            out[day] = {"contract": contract, "ticks": ticks, "closes": closes}
    return out


def load_market_log(path: Optional[str]) -> Dict[str, List[Tuple[int, Dict[str, str]]]]:
    log: Dict[str, List[Tuple[int, Dict[str, str]]]] = defaultdict(list)
    if not path:
        return log
    with Path(path).open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            log[row["day"]].append((M.parse_time_us(row["time"]), row))
    for rows in log.values():
        rows.sort(key=lambda x: x[0])
    return log


def open_market_context(market_mod: Any, mp: Any, now: datetime, day: str) -> Dict[str, Any]:
    """大盤濾網全開的 context（同 ms_replay 的假設路徑）；實際的大盤許可改由 market_log 判斷。"""
    now_iso = now.isoformat(timespec="microseconds")
    mode = market_mod._default_trading_mode("NORMAL")
    allow, permission = market_mod._long_permission("NORMAL", mode)
    compact = day.replace("-", "")
    context = {
        "market_state": "NORMAL", "auto_market_state": "NORMAL", "panic_reason": "", "market_score": 40.0,
        "trading_mode": mode, "allow_long": allow, "long_permission": permission, "intraday_trend": "UP",
        "momentum_long_allowed": True, "trend_long_allowed": False, "breadth": {"net_ratio": 0.3},
        "taiex": {"available": True, "change_pct": 0.0, "timestamp": now_iso},
        "tpex": {"available": True, "change_pct": 0.0, "timestamp": now_iso},
        "parameters": mp.snapshot(), "asof": now_iso, "realtime_buy_data_ready": True,
        "session_state": "REGULAR", "session_trade_date": compact,
        "breadth_snapshot_acquired_at": {"TWSE": now_iso, "TPEX": now_iso},
        "data_fingerprint": f"FUTURES_REPLAY:{now_iso}",
    }
    context["market_snapshot"] = {"snapshot_id": context["data_fingerprint"], "stale": False,
                                  "publication_state": "PUBLISHED", "refreshed_at": now_iso,
                                  "session_trade_date": compact}
    context["strategy_market_permissions"] = {"TREND": market_mod._trend_permission_policy(context)}
    return market_mod.resolve_strategy_market_context(context, now=now)


# --------------------------------------------------------------------------- 模擬
@dataclasses.dataclass
class Config:
    product: str
    point_value: int
    contracts: int
    commission: float
    tax_rate: float
    slip_ticks: int
    tick: float
    latency_us: int
    eval_interval_us: int
    refresh_us: int


class DayEngine:
    """原版一天的模擬，一筆一筆餵（盤中模擬單 fubon_live.py 用）；simulate_day 也是用它跑，兩邊結果完全相同。

    on_event(kind, t_us, info)：signal（送單前驗證通過、決定買進）、fill（買進成交）、exit_signal（出場訊號）、
    exit_fill（賣出成交，一筆部位可能分兩次出）、closed（整筆平倉，info 是 finish() 的結果）。"""

    def __init__(self, mods, params, day: str, contract: str, log_rows, cfg: Config, on_event=None) -> None:
        self.intraday, self.trading, self.market_mod = mods["intraday"], mods["trading"], mods["market"]
        self.ip, self.tp, self.mp = params["intraday"], params["trading"], params["market"]
        self.intraday._STRATEGY_STATE_CACHE.clear()
        self.intraday.prune_calculation_cache()
        self.buffer = self.intraday.IncrementalEventBuffer(self.ip)
        self.day, self.contract, self.log_rows, self.cfg = day, contract, log_rows, cfg
        self.on_event = on_event or (lambda kind, t_us, info: None)
        self.symbol, self.market = cfg.product, "TAIFEX"
        self.candidate = {"symbol": self.symbol, "market": self.market, "trade_ready": True, "momentum_eligible": True,
                          "trend_eligible": True, "market_relative_strength": 1.0, "day_trade_eligible": True}
        self.context: Dict[str, Any] = {}
        self.next_refresh = 0
        self.last_eval = 0
        self.position: Optional[Dict[str, Any]] = None
        self.pending: Optional[Dict[str, Any]] = None
        self.trade: Optional[Dict[str, Any]] = None
        self.entries = 0
        self.last_exit_us: Optional[int] = None
        self.closed: List[Tuple[int, float]] = []
        self.trades: List[Dict[str, Any]] = []
        self.blocked: Counter = Counter()
        self.signals: Counter = Counter()
        self.n_ticks = 0
        self.last_tick: Optional[Tuple[int, float]] = None

    def market_allows(self, strategy: str, t_us: int) -> Tuple[bool, str]:
        rows = self.log_rows.get(self.day) or []
        if not rows:
            return True, ""
        chosen = None
        for stamp, row in rows:
            if stamp > t_us:
                break
            chosen = row
        if chosen is None:
            return False, "MARKET_LOG_NOT_READY"
        key = "momentum_allowed" if strategy == "MOMENTUM" else "trend_allowed"
        return (chosen.get(key) == "True"), f"MARKET_{strategy}_BLOCKED({chosen.get('market_state')})"

    def cooldown_active(self, t_us: int) -> bool:
        consecutive, until = 0, None
        for stamp, net in self.closed:
            consecutive = consecutive + 1 if net < 0 else 0
            if consecutive >= self.tp.max_consecutive_losses:
                until = stamp + int(self.tp.loss_cooldown_minutes * 60 * M.US)
        return until is not None and t_us < until

    def on_tick(self, t_us: int, serial: int, price: float, size: float) -> None:
        cfg, tp = self.cfg, self.tp
        self.n_ticks += 1
        self.last_tick = (t_us, price)
        now = M.us_to_dt(t_us)
        while t_us >= self.next_refresh:
            boundary = max(self.next_refresh, t_us - t_us % cfg.refresh_us)
            self.context = open_market_context(self.market_mod, self.mp, M.us_to_dt(boundary), self.day)
            self.next_refresh = boundary + cfg.refresh_us
        self.buffer.append_sequence([{"symbol": self.symbol, "market": self.market, "trade_date": self.day, "event_time": t_us,
                                      "serial": serial, "price": price, "size": size,
                                      "event_classification": "NEW"}], self.ip)
        trade, position, pending = self.trade, self.position, self.pending
        if trade is not None:
            trade["max"] = max(trade["max"], price)
            trade["min"] = min(trade["min"], price)

        # 成交：決策後延遲，用下一筆成交價 ± 滑價
        if pending is not None and t_us >= pending["fill_after"]:
            if pending["side"] == "BUY":
                fill = price + cfg.slip_ticks * cfg.tick
                position = {
                    "symbol": self.symbol, "market": self.market, "entry_price": fill, "quantity": pending["qty"],
                    "managed_quantity": pending["qty"], "strategy_type": pending["strategy"],
                    "strategy_stop_price": pending["stop"],
                    "emergency_stop_price": fill * (1 - tp.emergency_stop_loss_pct / 100),
                    "entry_time": M.us_to_iso(t_us), "partial_exit_done": False,
                }
                self.entries += 1
                fee = cfg.commission * pending["qty"] + round(fill * cfg.point_value * cfg.tax_rate) * pending["qty"]
                trade = {"day": self.day, "symbol": self.symbol, "contract": self.contract, "strategy": pending["strategy"],
                         "setup": pending["setup"], "signal_time": M.us_to_iso(pending["decided"]),
                         "signal_price": pending["signal_price"], "entry_time": M.us_to_iso(t_us),
                         "entry_price": fill, "entry_trade_price": price, "quantity": pending["qty"],
                         "stop_price": max(pending["stop"], position["emergency_stop_price"]),
                         "costs": fee, "legs": [], "max": price, "min": price}
                self.on_event("fill", t_us, {"price": fill, "qty": pending["qty"], "stop": trade["stop_price"],
                                             "strategy": pending["strategy"], "setup": pending["setup"]})
            else:
                fill = price - cfg.slip_ticks * cfg.tick
                qty = pending["qty"]
                fee = cfg.commission * qty + round(fill * cfg.point_value * cfg.tax_rate) * qty
                trade["costs"] += fee
                trade["legs"].append({"time": M.us_to_iso(t_us), "price": fill, "trade_price": price,
                                      "quantity": qty, "reason": pending["reason"]})
                position["quantity"] -= qty
                position["managed_quantity"] -= qty
                if pending["reason"] == "MOMENTUM_DECAY_PARTIAL":
                    position["partial_exit_done"] = True
                self.on_event("exit_fill", t_us, {"price": fill, "qty": qty, "reason": pending["reason"],
                                                  "left": position["quantity"]})
                if position["quantity"] <= 0:
                    self.trades.append(finish(trade, cfg))
                    self.closed.append((t_us, self.trades[-1]["net_pnl"]))
                    self.last_exit_us = t_us
                    self.on_event("closed", t_us, self.trades[-1])
                    position, trade = None, None
            pending = None
        self.trade, self.position, self.pending = trade, position, pending

        if pending is not None or t_us - self.last_eval < cfg.eval_interval_us:
            return
        self.last_eval = t_us
        signal = self.intraday.get_signal_context(symbol=self.symbol, market=self.market, candidate=self.candidate,
                                                  market_context=self.context, quote_sequence=self.buffer, parameters=self.ip,
                                                  position=position, include_aggregates=False)
        if position is not None:
            if signal.get("exit_signal"):
                fraction = float(signal.get("exit_fraction") or 1.0)
                qty = position["quantity"]
                if fraction < 1 and qty > 1:
                    qty = max(1, int(qty * fraction))  # 只有 1 口時減碼等於全出（同股票版只有 1 張）
                self.pending = {"side": "SELL", "qty": qty, "fill_after": t_us + cfg.latency_us,
                                "reason": str(signal.get("exit_stage") or "")}
                self.on_event("exit_signal", t_us, {"price": price, "qty": qty, "reason": self.pending["reason"]})
            return
        if not signal.get("entry_signal"):
            return
        strategy = str(signal.get("strategy_type"))
        setup = str(signal.get("entry_setup_type") or "")
        key = f"{strategy}/{setup}"
        self.signals[key] += 1
        reasons = []
        ok, reason = self.market_allows(strategy, t_us)
        if not ok:
            reasons.append(reason)
        if self.entries >= tp.max_entries_per_symbol_per_day:
            reasons.append("SYMBOL_ENTRY_LIMIT")
        if self.last_exit_us is not None and t_us - self.last_exit_us < tp.reentry_cooldown_minutes * 60 * M.US:
            reasons.append("REENTRY_COOLDOWN")
        if self.cooldown_active(t_us):
            reasons.append("CONSECUTIVE_LOSS_COOLDOWN")
        executable = price + cfg.slip_ticks * cfg.tick
        sig = dict(signal, execution_trigger="AUTO")
        stop = float(sig.get("strategy_stop_price") or 0)
        signal_price = float(sig.get("signal_price") or price)
        if stop <= 0 or stop >= executable:
            reasons.append("STRUCTURAL_STOP_INVALID")
        elif (executable - stop) / executable * 100 > tp.max_stop_distance_pct:
            reasons.append("STRUCTURAL_STOP_TOO_WIDE")
        if (price - signal_price) / signal_price * 100 > tp.max_chase_pct:
            reasons.append("CHASE_TOO_HIGH")
        wave = self.trading._entry_wave_extension_revalidation(sig, executable, tp)
        if not wave.get("ok"):
            reasons.append(wave.get("rejection_code") or "WAVE_EXTENSION")
        momentum = self.trading._entry_momentum_revalidation(sig, price, tp, now=now)
        if not momentum.get("ok"):
            reasons.extend(f"REVALIDATION:{r}" for r in momentum.get("reasons") or [])
        if reasons:
            for r in dict.fromkeys(reasons):
                self.blocked[f"{key}:{r}"] += 1
            return
        self.signals[f"{key}:ACCEPTED"] += 1
        self.pending = {"side": "BUY", "qty": cfg.contracts, "fill_after": t_us + cfg.latency_us, "decided": t_us,
                        "strategy": strategy, "setup": setup, "stop": stop, "signal_price": signal_price}
        self.on_event("signal", t_us, {"price": price, "stop": stop, "strategy": strategy, "setup": setup})

    def close(self) -> None:
        """收盤前仍持有：以最後成交價 − 滑價了結（只做一次）。"""
        cfg = self.cfg
        if self.position is None or self.trade is None or self.last_tick is None:
            return
        last_t, last_price = self.last_tick
        fill = last_price - cfg.slip_ticks * cfg.tick
        qty = self.position["quantity"]
        trade = self.trade
        trade["costs"] += cfg.commission * qty + round(fill * cfg.point_value * cfg.tax_rate) * qty
        trade["legs"].append({"time": M.us_to_iso(last_t), "price": fill, "trade_price": last_price,
                              "quantity": qty, "reason": "EOD_FORCED_CLOSE"})
        self.trades.append(finish(trade, cfg))
        self.on_event("exit_fill", last_t, {"price": fill, "qty": qty, "reason": "EOD_FORCED_CLOSE", "left": 0})
        self.on_event("closed", last_t, self.trades[-1])
        self.position, self.trade, self.pending = None, None, None


def simulate_day(mods, params, day: str, data: Dict[str, Any], log_rows, cfg: Config) -> Dict[str, Any]:
    engine = DayEngine(mods, params, day, data["contract"], log_rows, cfg)
    for t_us, serial, price, size in data["ticks"]:
        engine.on_tick(t_us, serial, price, size)
    engine.close()
    return {"day": day, "trades": engine.trades, "signals": dict(engine.signals), "blocked": dict(engine.blocked),
            "contract": data["contract"], "ticks": len(data["ticks"])}


def finish(trade: Dict[str, Any], cfg: Config) -> Dict[str, Any]:
    pv, qty = cfg.point_value, trade["quantity"]
    legs = trade["legs"]
    gross = sum((leg["price"] - trade["entry_price"]) * leg["quantity"] for leg in legs) * pv
    raw = sum((leg["trade_price"] - trade["entry_trade_price"]) * leg["quantity"] for leg in legs) * pv
    notional = trade["entry_price"] * pv * qty
    net = gross - trade["costs"]
    risk = max(0.0, trade["entry_price"] - trade["stop_price"]) * pv * qty
    points = sum((leg["price"] - trade["entry_price"]) * leg["quantity"] for leg in legs) / qty
    entry_dt, exit_dt = datetime.fromisoformat(trade["entry_time"]), datetime.fromisoformat(legs[-1]["time"])
    return {
        "day": trade["day"], "symbol": trade["symbol"], "contract": trade["contract"], "strategy": trade["strategy"],
        "setup": trade["setup"], "strategy_setup": f"{trade['strategy']}/{trade['setup']}",
        "signal_time": trade["signal_time"], "signal_price": trade["signal_price"],
        "entry_time": trade["entry_time"], "entry_price": trade["entry_price"], "quantity": qty,
        "stop_price": round(trade["stop_price"], 2),
        "stop_distance_pct": round((trade["entry_price"] - trade["stop_price"]) / trade["entry_price"] * 100, 4),
        "stop_distance_points": round(trade["entry_price"] - trade["stop_price"], 1),
        "exit_time": legs[-1]["time"], "final_exit_reason": legs[-1]["reason"],
        "exit_reasons": "+".join(leg["reason"] for leg in legs),
        "points": round(points, 2), "hold_minutes": round((exit_dt - entry_dt).total_seconds() / 60, 2),
        "gross_pnl": round(gross, 0), "mid_gross_pnl": round(raw, 0), "fees": round(trade["costs"], 0), "tax": 0.0,
        "net_pnl": round(net, 0), "gross_ret_pct": round(gross / notional * 100, 5),
        "net_ret_pct": round(net / notional * 100, 5), "r_multiple": round(net / risk, 4) if risk > 0 else None,
        "mfe_pct": round((trade["max"] / trade["entry_trade_price"] - 1) * 100, 4),
        "mae_pct": round((trade["min"] / trade["entry_trade_price"] - 1) * 100, 4),
        "exit_legs": json.dumps([{k: leg[k] for k in ("time", "price", "quantity", "reason")} for leg in legs],
                                ensure_ascii=False),
    }


def write_chart_inputs(out: Path, product: str, data: Dict[str, Dict[str, Any]]) -> None:
    """輸出 plot_jiangbo.py 可直接讀的 bars.csv / ref.csv（1 分 K 由逐筆聚合，附逐筆算的 VWAP）。"""
    days = sorted(data)
    with (out / "bars.csv").open("w", encoding="utf-8", newline="") as bar_handle, \
            (out / "ref.csv").open("w", encoding="utf-8", newline="") as ref_handle:
        bars = csv.writer(bar_handle)
        bars.writerow(["date", "symbol", "time", "open", "high", "low", "close", "volume_lots", "vwap"])
        ref = csv.writer(ref_handle)
        ref.writerow(["date", "symbol", "market", "prev_close", "day_trade_eligible", "sector_code", "name"])
        for i, day in enumerate(days):
            info = data[day]
            previous = data.get(days[i - 1]) if i > 0 else None
            prev_close = previous["closes"].get(info["contract"]) if previous else None
            ref.writerow([day, product, "TAIFEX", prev_close or "", "true", "", f"小台 {info['contract']}"])
            minutes: Dict[str, List[float]] = {}
            pv = vol = 0.0
            for t_us, _serial, price, size in info["ticks"]:
                dt = M.us_to_dt(t_us)
                if not (9 <= dt.hour < 13 or (dt.hour == 13 and dt.minute < 30)):
                    continue
                key = dt.strftime("%H:%M")
                pv += price * size
                vol += size
                bar = minutes.setdefault(key, [price, price, price, price, 0, 0.0])
                bar[1], bar[2], bar[3] = max(bar[1], price), min(bar[2], price), price
                bar[4] += size
                bar[5] = pv / vol
            for key, (o, h, low, c, v, vwap) in minutes.items():
                bars.writerow([day, product, key, o, h, low, c, v, round(vwap, 2)])


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", required=True)
    p.add_argument("--taifex", required=True, help="解壓後的期交所 Daily_*.csv 所在資料夾")
    p.add_argument("--product", default="MTX", choices=sorted(PRODUCTS))
    p.add_argument("--dates", nargs="*")
    p.add_argument("--market-log", help="股票版 ms_replay 輸出的 market_log.csv（大盤許可）")
    p.add_argument("--params-json")
    p.add_argument("--set", action="append", default=[])
    p.add_argument("--contracts", type=int, default=1)
    p.add_argument("--commission", type=float, default=30.0, help="每口單邊手續費（元）")
    p.add_argument("--tax-rate", type=float, default=0.00002, help="期交稅（單邊，契約價值的十萬分之二）")
    p.add_argument("--slip-ticks", type=int, default=1, help="每邊滑價幾個跳動點（沒有委買賣價時的近似）")
    p.add_argument("--latency-ms", type=float, default=500)
    p.add_argument("--eval-min-interval-ms", type=float, default=1000,
                   help="同一商品兩次策略評估的最短間隔；期貨每秒多筆成交，預設每秒評估一次")
    p.add_argument("--out", default="futures_out")
    args = p.parse_args()

    mods = M.load_modules(args.repo)
    params = M.load_parameters(mods, args.params_json, args.set)
    data = read_taifex(Path(args.taifex), args.product)
    if args.dates:
        data = {d: v for d, v in data.items() if d in set(args.dates)}
    log_rows = load_market_log(args.market_log)
    cfg = Config(product=args.product, point_value=PRODUCTS[args.product], contracts=args.contracts,
                 commission=args.commission, tax_rate=args.tax_rate, slip_ticks=args.slip_ticks, tick=1.0,
                 latency_us=int(args.latency_ms * 1000), eval_interval_us=int(args.eval_min_interval_ms * 1000),
                 refresh_us=30 * M.US)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    days = sorted(data)
    run_days = [d for d in days if not log_rows or d in log_rows] if args.market_log else days
    results = []
    for day in run_days:
        result = simulate_day(mods, params, day, data[day], log_rows, cfg)
        results.append(result)
        pnl = sum(t["net_pnl"] for t in result["trades"])
        print(f"[{day}] {args.product} {result['contract']} {result['ticks']:,} 筆成交，"
              f"成交 {len(result['trades'])} 筆，淨損益 {pnl:,.0f} 元", flush=True)
    trades = [t for r in results for t in r["trades"]]
    M.write_csv(out / "trades.csv", trades)
    write_chart_inputs(out, args.product, {d: data[d] for d in days})
    signals: Counter = Counter()
    blocked: Counter = Counter()
    for r in results:
        signals.update(r["signals"])
        blocked.update(r["blocked"])
    summary = {
        "product": args.product, "point_value": cfg.point_value, "contracts": cfg.contracts,
        "days": run_days, "overall": M.summarize(trades), "equity": M.equity_stats(trades, run_days),
        "by_setup": M.grouped(trades, "strategy_setup"),
        "avg_points": round(statistics.fmean(t["points"] for t in trades), 2) if trades else None,
        "avg_stop_points": round(statistics.fmean(t["stop_distance_points"] for t in trades), 1) if trades else None,
        "signals": dict(signals), "blocked": dict(blocked.most_common(30)),
        "assumptions": [
            "只套用 Intraday 進出場與 Trading 的送單前驗證；Scanner、現股當沖、價格上限、價差等個股規則不適用。",
            "相對大盤強度 > 0 的條件對指數本身無意義，固定視為通過。",
            "期交所檔案沒有委買委賣價：買進用下一筆成交價 + 滑價、賣出用 − 滑價。",
            "大盤許可來自 --market-log；沒給時大盤濾網全開。",
            f"成本：每口單邊手續費 {args.commission:.0f} 元＋期交稅十萬分之 {args.tax_rate * 1e5:g}＋每邊 {args.slip_ticks} 點滑價。",
        ],
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    o = summary["overall"]
    if o.get("trades"):
        print(f"[結果] {o['trades']} 筆｜勝率 {o['win_rate_pct']}%｜平均每筆 {summary['avg_points']} 點"
              f"（停損距離平均 {summary['avg_stop_points']} 點）｜淨損益 {o['total_net_pnl']:,.0f} 元")
    else:
        print("[結果] 沒有成交")
    print(f"[訊號] {dict(signals)}")
    print(f"[阻擋] {dict(blocked.most_common(8))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
