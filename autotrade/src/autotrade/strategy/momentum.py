"""動能順勢：網友原版 Momentum_Scalping（seromitama，只做多）的轉接。

原版程式不放進這個 repo，執行時從設定的資料夾載入（load_original）。
這裡只做「原版訊號 → 委託」：進出場判斷、送單前驗證、帳戶規則（每日進場次數、平倉後冷卻、
連虧冷卻）全部照研究分支 futures_replay.DayEngine 的寫法，一條不改；
差別只有成交不再自己假設，而是送出委託、等引擎回報實際成交價。
個股的「相對大盤強度」對指數沒有意義，固定視為通過；大盤濾網全開（同現有模擬單）。
"""

from __future__ import annotations

import importlib
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from autotrade.config import ContractConfig, MomentumConfig
from autotrade.model import Fill, OrderRequest, Side, Tick

US = 1_000_000
TPE8 = timezone(timedelta(hours=8))  # 原版引擎用固定 +08:00，照用以求逐筆相同
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MARKET = "TAIFEX"
MODULE_KEYS = ("intraday", "market", "scanner", "trading", "api_gateway")
PARAMETER_CLASSES = {
    "intraday": "IntradayParameters",
    "trading": "TradingParameters",
    "market": "MarketParameters",
}


class StrategyStateError(RuntimeError):
    """策略自己記的部位跟實際部位或回報對不上。"""


def to_us(ts: datetime) -> int:
    """時間轉成 epoch 微秒（整數運算，不經過浮點數）。"""
    return (ts - EPOCH) // timedelta(microseconds=1)


def us_to_dt(value: int) -> datetime:
    """epoch 微秒轉回時間（寫法同原引擎）。"""
    return datetime.fromtimestamp(value / US, tz=TPE8)


def _latest_module(repo: Path, prefix: str) -> str:
    best: tuple[tuple[int, ...], str] | None = None
    for path in repo.glob(f"{prefix}_v*.py"):
        match = re.fullmatch(rf"{prefix}_v(\d+(?:_\d+)*)", path.stem)
        if match is None:
            continue
        version = tuple(int(x) for x in match.group(1).split("_"))
        if best is None or version > best[0]:
            best = (version, path.stem)
    if best is None:
        raise FileNotFoundError(
            f"在 {repo} 找不到 {prefix}_v*.py（原版 Momentum_Scalping 的資料夾？）"
        )
    return best[1]


def load_original(repo: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """載入原版模組與預設參數。會改 sys.path 與原版用來互相找模組的環境變數。"""
    root = repo.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"原版 Momentum_Scalping 的資料夾不存在：{root}")
    names = {key: _latest_module(root, key) for key in MODULE_KEYS}
    os.environ["INTRADAY_MARKET_MODULE"] = names["market"]
    os.environ["INTRADAY_API_GATEWAY_MODULE"] = names["api_gateway"]
    os.environ["TRADING_MARKET_MODULE"] = names["market"]
    os.environ["TRADING_SCANNER_MODULE"] = names["scanner"]
    os.environ["TRADING_INTRADAY_MODULE"] = names["intraday"]
    os.environ["TRADING_API_GATEWAY_MODULE"] = names["api_gateway"]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    mods: dict[str, Any] = {key: importlib.import_module(name) for key, name in names.items()}
    params: dict[str, Any] = {}
    for key, class_name in PARAMETER_CLASSES.items():
        params[key] = getattr(mods[key], class_name)()
        params[key].validate()
    return mods, params


def open_market_context(market_mod: Any, mp: Any, now: datetime, day: str) -> dict[str, Any]:
    """大盤濾網全開的 context（照抄原引擎）。"""
    now_iso = now.isoformat(timespec="microseconds")
    mode = market_mod._default_trading_mode("NORMAL")
    allow, permission = market_mod._long_permission("NORMAL", mode)
    compact = day.replace("-", "")
    context: dict[str, Any] = {
        "market_state": "NORMAL",
        "auto_market_state": "NORMAL",
        "panic_reason": "",
        "market_score": 40.0,
        "trading_mode": mode,
        "allow_long": allow,
        "long_permission": permission,
        "intraday_trend": "UP",
        "momentum_long_allowed": True,
        "trend_long_allowed": False,
        "breadth": {"net_ratio": 0.3},
        "taiex": {"available": True, "change_pct": 0.0, "timestamp": now_iso},
        "tpex": {"available": True, "change_pct": 0.0, "timestamp": now_iso},
        "parameters": mp.snapshot(),
        "asof": now_iso,
        "realtime_buy_data_ready": True,
        "session_state": "REGULAR",
        "session_trade_date": compact,
        "breadth_snapshot_acquired_at": {"TWSE": now_iso, "TPEX": now_iso},
        "data_fingerprint": f"FUTURES_REPLAY:{now_iso}",
    }
    context["market_snapshot"] = {
        "snapshot_id": context["data_fingerprint"],
        "stale": False,
        "publication_state": "PUBLISHED",
        "refreshed_at": now_iso,
        "session_trade_date": compact,
    }
    context["strategy_market_permissions"] = {"TREND": market_mod._trend_permission_policy(context)}
    resolved: dict[str, Any] = market_mod.resolve_strategy_market_context(context, now=now)
    return resolved


@dataclass
class _Trade:
    entry_price: float
    qty: int
    costs: float
    gross_points: float = 0.0


@dataclass
class MomentumStrategy:
    """動能順勢。mods／params 是 load_original 的回傳值。"""

    mods: dict[str, Any]
    params: dict[str, Any]
    cfg: MomentumConfig
    contract: ContractConfig
    trades: list[dict[str, Any]] = field(default_factory=list)
    signals: Counter[str] = field(default_factory=Counter)
    blocked: Counter[str] = field(default_factory=Counter)
    _day: str = ""
    _serial: int = 0
    _buffer: Any = None
    _context: dict[str, Any] = field(default_factory=dict)
    _next_refresh: int = 0
    _last_eval: int = 0
    _position: dict[str, Any] | None = None
    _pending: dict[str, Any] | None = None
    _trade: _Trade | None = None
    _entries: int = 0
    _last_exit_us: int | None = None
    _closed: list[tuple[int, float]] = field(default_factory=list)

    def _start_day(self, day: str) -> None:
        if self._position is not None or self._pending is not None:
            raise StrategyStateError(f"換日到 {day} 時還有部位或在途委託，前一天沒有清乾淨")
        intraday = self.mods["intraday"]
        intraday._STRATEGY_STATE_CACHE.clear()
        intraday.prune_calculation_cache()
        self._buffer = intraday.IncrementalEventBuffer(self.params["intraday"])
        self._day, self._serial = day, 0
        self._context, self._next_refresh, self._last_eval = {}, 0, 0
        self._entries, self._last_exit_us, self._closed = 0, None, []

    def _cooldown_active(self, t_us: int) -> bool:
        tp = self.params["trading"]
        consecutive, until = 0, None
        for stamp, net in self._closed:
            consecutive = consecutive + 1 if net < 0 else 0
            if consecutive >= tp.max_consecutive_losses:
                until = stamp + int(tp.loss_cooldown_minutes * 60 * US)
        return until is not None and t_us < until

    def _cost(self, price: float, qty: int) -> float:
        tax = round(price * self.contract.point_value * self.contract.tax_rate) * qty
        return self.contract.commission_twd * qty + tax

    def on_tick(self, tick: Tick, position: int) -> list[OrderRequest]:
        """餵一筆行情；需要進場或出場時回傳委託。"""
        day = tick.ts.date().isoformat()
        if day != self._day:
            self._start_day(day)
        held = 0 if self._position is None else int(self._position["quantity"])
        if self._pending is None and held != position:
            raise StrategyStateError(
                f"策略記的部位 {held} 口跟實際部位 {position} 口不同（{tick.ts}）"
            )
        symbol, tp, ip = self.contract.symbol, self.params["trading"], self.params["intraday"]
        t_us, price = to_us(tick.ts), tick.price
        refresh_us = int(self.cfg.market_refresh_s * US)
        while t_us >= self._next_refresh:
            boundary = max(self._next_refresh, t_us - t_us % refresh_us)
            self._context = open_market_context(
                self.mods["market"], self.params["market"], us_to_dt(boundary), day
            )
            self._next_refresh = boundary + refresh_us
        self._buffer.append_sequence(
            [
                {
                    "symbol": symbol,
                    "market": MARKET,
                    "trade_date": day,
                    "event_time": t_us,
                    "serial": self._serial,
                    "price": price,
                    "size": tick.qty,
                    "event_classification": "NEW",
                }
            ],
            ip,
        )
        self._serial += 1
        if self._pending is not None or t_us - self._last_eval < int(self.cfg.eval_interval_s * US):
            return []
        self._last_eval = t_us
        candidate = {
            "symbol": symbol,
            "market": MARKET,
            "trade_ready": True,
            "momentum_eligible": True,
            "trend_eligible": True,
            "market_relative_strength": 1.0,
            "day_trade_eligible": True,
        }
        signal = self.mods["intraday"].get_signal_context(
            symbol=symbol,
            market=MARKET,
            candidate=candidate,
            market_context=self._context,
            quote_sequence=self._buffer,
            parameters=ip,
            position=self._position,
            include_aggregates=False,
        )
        if self._position is not None:
            if not signal.get("exit_signal"):
                return []
            fraction = float(signal.get("exit_fraction") or 1.0)
            qty = int(self._position["quantity"])
            if fraction < 1 and qty > 1:
                qty = max(1, int(qty * fraction))
            reason = str(signal.get("exit_stage") or "")
            self._pending = {"side": Side.SELL, "left": qty, "reason": reason}
            return [OrderRequest(ts=tick.ts, symbol=symbol, side=Side.SELL, qty=qty, reason=reason)]
        if not signal.get("entry_signal"):
            return []
        strategy = str(signal.get("strategy_type"))
        setup = str(signal.get("entry_setup_type") or "")
        key = f"{strategy}/{setup}"
        self.signals[key] += 1
        reasons: list[str] = []
        if self._entries >= tp.max_entries_per_symbol_per_day:
            reasons.append("SYMBOL_ENTRY_LIMIT")
        cooldown_us = tp.reentry_cooldown_minutes * 60 * US
        if self._last_exit_us is not None and t_us - self._last_exit_us < cooldown_us:
            reasons.append("REENTRY_COOLDOWN")
        if self._cooldown_active(t_us):
            reasons.append("CONSECUTIVE_LOSS_COOLDOWN")
        executable = price + self.cfg.expected_slippage_points
        sig = dict(signal, execution_trigger="AUTO")
        stop = float(sig.get("strategy_stop_price") or 0)
        signal_price = float(sig.get("signal_price") or price)
        if stop <= 0 or stop >= executable:
            reasons.append("STRUCTURAL_STOP_INVALID")
        elif (executable - stop) / executable * 100 > tp.max_stop_distance_pct:
            reasons.append("STRUCTURAL_STOP_TOO_WIDE")
        if (price - signal_price) / signal_price * 100 > tp.max_chase_pct:
            reasons.append("CHASE_TOO_HIGH")
        trading = self.mods["trading"]
        wave = trading._entry_wave_extension_revalidation(sig, executable, tp)
        if not wave.get("ok"):
            reasons.append(wave.get("rejection_code") or "WAVE_EXTENSION")
        momentum = trading._entry_momentum_revalidation(sig, price, tp, now=us_to_dt(t_us))
        if not momentum.get("ok"):
            reasons.extend(f"REVALIDATION:{r}" for r in momentum.get("reasons") or [])
        if reasons:
            for r in dict.fromkeys(reasons):
                self.blocked[f"{key}:{r}"] += 1
            return []
        self.signals[f"{key}:ACCEPTED"] += 1
        qty = self.cfg.contracts
        self._pending = {
            "side": Side.BUY,
            "left": qty,
            "strategy": strategy,
            "setup": setup,
            "stop": stop,
            "signal_time": tick.ts,
        }
        why = f"{key} 結構停損 {stop:g}"
        return [OrderRequest(ts=tick.ts, symbol=symbol, side=Side.BUY, qty=qty, reason=why)]

    def on_fill(self, fill: Fill) -> None:
        """成交回報：買進建立部位，賣出減少部位（含系統強制平倉的賣單）。"""
        pending, tp = self._pending, self.params["trading"]
        t_us = to_us(fill.ts)
        if fill.side == Side.BUY:
            if pending is None or pending["side"] != Side.BUY or self._position is not None:
                raise StrategyStateError(f"收到沒有對應進場委託的買進成交：{fill!r}")
            self._position = {
                "symbol": fill.symbol,
                "market": MARKET,
                "entry_price": fill.price,
                "quantity": fill.qty,
                "managed_quantity": fill.qty,
                "strategy_type": pending["strategy"],
                "strategy_stop_price": pending["stop"],
                "emergency_stop_price": fill.price * (1 - tp.emergency_stop_loss_pct / 100),
                "entry_time": us_to_dt(t_us).isoformat(timespec="microseconds"),
                "partial_exit_done": False,
            }
            self._entries += 1
            self._trade = _Trade(fill.price, fill.qty, self._cost(fill.price, fill.qty))
            self.trades.append(
                {
                    "signal_time": pending["signal_time"],
                    "setup": f"{pending['strategy']}/{pending['setup']}",
                    "entry_time": fill.ts,
                    "entry_price": fill.price,
                    "stop_price": max(pending["stop"], self._position["emergency_stop_price"]),
                }
            )
        else:
            if self._position is None or self._trade is None:
                raise StrategyStateError(f"沒有部位卻收到賣出成交：{fill!r}")
            mine = pending is not None and pending["side"] == Side.SELL
            reason = pending["reason"] if mine and pending is not None else "SYSTEM_FLATTEN"
            trade = self._trade
            trade.costs += self._cost(fill.price, fill.qty)
            trade.gross_points += (fill.price - trade.entry_price) * fill.qty
            self._position["quantity"] -= fill.qty
            self._position["managed_quantity"] -= fill.qty
            if reason == "MOMENTUM_DECAY_PARTIAL":
                self._position["partial_exit_done"] = True
            if self._position["quantity"] < 0:
                raise StrategyStateError(f"賣出成交超過持有口數：{fill!r}")
            if self._position["quantity"] == 0:
                net = round(trade.gross_points * self.contract.point_value - trade.costs, 0)
                self.trades[-1].update(
                    exit_time=fill.ts, exit_price=fill.price, exit_reason=reason, net_pnl=net
                )
                self._closed.append((t_us, net))
                self._last_exit_us = t_us
                self._position, self._trade = None, None
            if not mine:
                return
        if pending is not None:
            pending["left"] -= fill.qty
            if pending["left"] <= 0:
                self._pending = None

    def on_reject(self, req: OrderRequest, reason: str) -> None:
        """委託沒送出去或被刪掉：放掉在途狀態，下一次評估再決定。"""
        self._pending = None
