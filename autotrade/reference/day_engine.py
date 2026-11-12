"""對照用的原引擎：研究分支 research/ms-paper-trade（33f5b6e）
research/momentum_scalping/scripts/futures_replay.py 的 open_market_context、entry_reason_text、
Config、DayEngine、finish 原封不動抽出來，只把 `import ms_replay as M` 換成下面的 M。
現有模擬單（fubon_live.py 的動能順勢）跑的就是這份 DayEngine。
**不要修改**：它是 autotrade 動能順勢轉接的比對基準（A-004）。不屬於 autotrade 套件，只給測試與比對工具用。
"""
from __future__ import annotations

import dataclasses
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple


class M:
    """ms_replay 裡 DayEngine 用到的三樣東西。"""

    TPE = timezone(timedelta(hours=8))
    US = 1_000_000

    @staticmethod
    def us_to_dt(value: int) -> datetime:
        return datetime.fromtimestamp(value / M.US, tz=M.TPE)

    @staticmethod
    def us_to_iso(value: int) -> str:
        return M.us_to_dt(value).isoformat(timespec="microseconds")


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


def _f(v, nd=2):
    """個股價有小數、台指期是整數：預設最多兩位、去掉多餘的零（53.50 → 53.5、47242.00 → 47,242）。"""
    try:
        return f"{float(v):,.{nd}f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "—"


def entry_reason_text(signal: Mapping[str, Any]) -> str:
    """原版進場訊號 → 一句白話的「為什麼進場」（使用者 2026-10-30：「可以寫進場是為什麼嗎」）。
    只用訊號本身的欄位：均價線 vwap、EMA5／EMA10（欄位名叫 ema20，實際是 10）、結構前低、突破基準、量與振幅倍數。"""
    strategy = str(signal.get("strategy_type") or "")
    setup = str(signal.get("entry_setup_type") or "")
    price = signal.get("signal_price") or signal.get("price")
    vwap, e5, e10 = signal.get("vwap"), signal.get("ema5"), signal.get("ema20")
    low = signal.get("last_higher_low")
    parts = []
    if strategy == "TREND":
        g = signal.get("trend_initial_gates") or {}
        parts.append(f"價 {_f(price)} 在均價線 {_f(vwap)} 之上" if g.get("above_vwap", True) else f"價 {_f(price)}")
        if g.get("ema_fast_above_slow", True):
            parts.append(f"5 分均線 {_f(e5)} 在 10 分均線 {_f(e10)} 之上" + ("且向上" if g.get("ema_slope_up") else ""))
        if g.get("fractal_hl") or g.get("rolling_hl") or signal.get("higher_low"):
            parts.append(f"一底比一底高（前低 {_f(low)}）" if low is not None else "一底比一底高")
        head = {"TREND_INITIAL_ENTRY": "趨勢成形", "TREND_RESUME": "趨勢回踩後再轉強"}.get(setup, "趨勢")
        return f"{head}：" + "、".join(parts)
    g = signal.get("momentum_initial_gates") or {}
    b, v, r = g.get("breakout") or {}, g.get("volume") or {}, g.get("range") or {}
    if setup in ("INITIAL_BREAKOUT", "PERSISTENT_MOMENTUM_START") and b.get("threshold"):
        parts.append(f"突破 {_f(b.get('threshold'))}（基準 {_f(signal.get('entry_structure_reference'))}）")
    elif setup:  # 回檔再突破、延續：原版的基準是這一波的結構點，不是初次突破的門檻
        parts.append(f"這一波的起點 {_f(signal.get('entry_wave_anchor_price'))}")
    if v.get("value") is not None:
        parts.append(f"量是平常 {_f(v.get('value'), 1)} 倍")
    if r.get("value") is not None:
        parts.append(f"振幅 {_f(r.get('value'), 1)} 倍")
    if signal.get("price_above_vwap"):
        parts.append(f"在均價線 {_f(vwap)} 之上")
    head = {"INITIAL_BREAKOUT": "動能突破", "FIRST_PULLBACK_REBREAK": "回檔後再突破", "MOMENTUM_CONTINUATION": "動能延續",
            "PERSISTENT_MOMENTUM_START": "持續放量上攻"}.get(setup, "動能")
    return f"{head}：" + "、".join(parts) if parts else head


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
        self.on_event("signal", t_us, {"price": price, "stop": stop, "strategy": strategy, "setup": setup,
                                       "why": entry_reason_text(sig)})

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
