#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_engine.py — 網友原版 Momentum_Scalping 個股當沖的「可逐筆餵」引擎（盤中錄製 stock_live.py 與 1 分 K 回測共用）。

沿用 ms_replay.DaySimulator（Scanner、Intraday、Trading 17 道閘門、送單前 Revalidation、13:00 清倉，原系統程式一行都不改），
只換兩件事：
  1. 選股母體：每 30 秒一份「全市場快照」（盤中＝富邦 snapshot；回測＝1 分 K 累計到那一刻），不是只有有逐筆的股票。
  2. 逐筆：只有進榜（前 watch_count 檔）與持倉中的股票才有逐筆；新進榜時用當天已收完的 1 分 K 補 K 棒（同原系統的 REST seed）。
大盤：加權、櫃買指數的盤中報價＋日 K，交給原系統 market.calculate_market_context() 算（含漲跌家數、類股）。

時間一律 Asia/Taipei；量的單位「張」（size_unit=LOT）；金額元。
"""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import ms_replay as M

Key = Tuple[str, str]  # (market, symbol)；market 是 TWSE / TPEX
COMMON = re.compile(r"^[1-9]\d{3}$")  # 四碼普通股

# 預設帳戶與成本（同 ms_replay 的 CLI 預設；使用者 2026-10-28：「先用他預設，但是可以調整」→ 由 stock_live.py 的 --account-json 覆蓋）
DEFAULT_CONFIG: Dict[str, Any] = {
    "capital": 1_000_000.0, "latency_ms": 500.0, "extra_slippage_ticks": 0, "fee_discount": 0.28, "min_fee": 20.0,
    "tax_rate": 0.0015, "size_unit": "LOT", "refresh_seconds": 30.0, "watch_count": 30, "eval_min_interval_ms": 0.0,
    "skip_scanner": False,
}
BOARD_FIELDS = ("rank", "symbol", "name", "market", "sector", "score", "momentum", "trend_score", "change_pct", "volume",
                "turnover", "volume_ratio", "market_relative_strength", "sector_relative_strength",
                "distance_to_intraday_high_pct", "near_intraday_high", "liquidity_score", "day_trade_eligible",
                "momentum_eligible", "trend_eligible", "trade_ready", "trade_block_reasons")
EXIT_TEXT = {"EMA_TREND_BREAK": "EMA 死叉", "HIGHER_LOW_BREAK": "跌破前低", "MOMENTUM_STRUCTURE_BREAK_FULL": "動能結構破壞",
             "MOMENTUM_DECAY_PARTIAL": "動能衰退減碼", "EMERGENCY_STOP": "緊急停損", "TIME_EXIT": "13:00 清倉",
             "CLOCK_TIME_EXIT": "13:00 清倉", "MARKET_TREND_INVALIDATED": "大盤轉弱", "RS_FAILURE": "強度轉弱",
             "UNKNOWN_STRATEGY_STOP": "停損", "EOD_FORCED_CLOSE": "收盤強制了結"}


def norm_market(value: Any) -> str:
    return M._normalize_market(value)


class LiveIndex:
    """給 DaySimulator._context_from_indices 用的指數來源：盤中報價由外部塞進來，日 K 給昨收與日線均線。"""

    NAMES = ("TAIEX", "TPEX")

    def __init__(self, daily: Mapping[str, Sequence[Tuple[str, float]]]):
        self.daily = {k: sorted(v) for k, v in daily.items()}
        self.live: Dict[str, Dict[str, float]] = {}

    def set(self, name: str, price: float, open_: float, high: float, low: float) -> None:
        self.live[name] = {"price": price, "open": open_, "high": high, "low": low}

    def has_day(self, day: str) -> bool:  # noqa: ARG002  # 介面同 IndexFeed
        return all(n in self.live for n in self.NAMES)

    def quote(self, day: str, name: str, t_us: int, now_iso: str) -> Optional[Dict[str, Any]]:  # noqa: ARG002
        q = self.live.get(name)
        previous = [c for d, c in self.daily.get(name, []) if d < day]
        if q is None or not previous:
            return None
        return {"symbol": name, **q, "prev_close": previous[-1], "timestamp": now_iso, "trade_date": day}

    def history(self, day: str, name: str) -> List[Dict[str, Any]]:
        return [{"price": c, "trade_date": d} for d, c in self.daily.get(name, []) if d < day][-20:]


class StockEngine(M.DaySimulator):
    """一天的個股當沖模擬；外部依時間順序呼叫 set_snapshot()（每 30 秒）、on_tick()（進榜股票的每一筆）、clock()、finish()。"""

    def __init__(self, mods: Mapping[str, Any], params: Mapping[str, Any], day: str, config: Mapping[str, Any],
                 universe: Mapping[Key, Mapping[str, Any]], index_daily: Mapping[str, Sequence[Tuple[str, float]]],
                 on_event: Optional[Callable[[str, int, Dict[str, Any]], None]] = None):
        cfg = {**DEFAULT_CONFIG, **config}
        super().__init__(mods, params, day, {}, {}, M.MarketTimeline(None), cfg, None)
        industry_names = getattr(mods.get("api_gateway"), "_INDUSTRY_NAMES", {}) or {}
        self.universe = {k: dict(v) for k, v in universe.items()}
        self.sector_names = {k[1]: industry_names.get(str(v.get("industry") or ""), str(v.get("industry") or ""))
                             for k, v in self.universe.items() if v.get("industry")}
        self.index_feed = LiveIndex(index_daily)
        self.rows: Dict[Key, Dict[str, Any]] = {}
        self.on_event = on_event or (lambda kind, t_us, info: None)
        self.board: List[Dict[str, Any]] = []
        self.board_time: Optional[int] = None
        self.watch_changes: Counter = Counter()
        self.day_trade: Dict[str, Optional[bool]] = {}
        self.now_us = 0

    # ------------------------------------------------------------------ 全市場快照
    def set_snapshot(self, t_us: int, rows: Sequence[Mapping[str, Any]], index: Mapping[str, Mapping[str, float]]) -> None:
        """rows：每檔 {market, symbol, name, price, open, high, low, prev_close, volume(張), turnover(元), event_time(μs)}。"""
        self.now_us = max(self.now_us, t_us)
        for r in rows:
            key = (norm_market(r["market"]), str(r["symbol"]))
            self.rows[key] = dict(r)
        for name, q in index.items():
            self.index_feed.set(name, q["price"], q["open"], q["high"], q["low"])
        if not self.index_feed.has_day(self.day):
            raise ValueError(f"{self.day} {M.us_to_iso(t_us)} 沒有加權／櫃買指數報價，無法算大盤狀態")
        self.refresh(t_us)
        if t_us >= self.exit_us:
            self._clock_exits(t_us)
        self._publish_board(t_us)

    def _scanner_rows(self, now_iso: str) -> List[Dict[str, Any]]:
        out = []
        for key, r in self.rows.items():
            if r.get("price") is None:
                continue
            row = {
                "symbol": key[1], "market": key[0], "name": r.get("name") or self.universe.get(key, {}).get("name", ""),
                "price": r["price"], "open": r.get("open"), "high": r.get("high"), "low": r.get("low"),
                "prev_close": r.get("prev_close"), "volume": r.get("volume") or 0, "volume_unit": "LOT",
                "turnover": r.get("turnover") or 0.0, "bid": None, "ask": None,
                "event_time": r.get("event_time"), "serial": -1,
                "timestamp": M.us_to_iso(r["event_time"]) if r.get("event_time") else now_iso,
                "trade_date": self.day, "market_state_available": True, "market_state_source": "SNAPSHOT",
                "snapshot_acquired_at": now_iso, "day_trade_eligible": self.day_trade.get(key[1]),
            }
            state = self.states.get(key)
            if state is not None and state.price is not None and state.event_time >= (r.get("event_time") or 0):
                # 進榜的股票有逐筆：用逐筆的最新價與買賣價（比快照新）
                row.update(price=state.price, high=max(state.high or state.price, r.get("high") or state.price),
                           low=min(state.low or state.price, r.get("low") or state.price), bid=state.bid, ask=state.ask,
                           event_time=state.event_time, serial=state.serial, timestamp=M.us_to_iso(state.event_time),
                           volume=max(state.volume, r.get("volume") or 0), market_state_source="FUGLE_WEBSOCKET")
            out.append(row)
        return out

    def _sector_master(self) -> List[Dict[str, Any]]:
        return [{"symbol": k[1], "sector_name": self.sector_names.get(k[1], ""), "sector_code": str(v.get("industry") or "")}
                for k, v in self.universe.items() if v.get("industry")]

    def _scan(self, rows: List[Dict[str, Any]], now_iso: str) -> None:
        day_trade_map = {s: v for s, v in self.day_trade.items() if v is not None}
        result = self.scanner.scan_candidates(rows, self.market_context, parameters=self.sp, day_trade_map=day_trade_map,
                                              session_phase="REGULAR", snapshot_reference_at=now_iso)
        picked = list(result.get("candidates") or [])[: self.watch_count]
        before = set(self.candidates)
        self.candidates = {(norm_market(c.get("market")), str(c.get("symbol"))): c for c in picked}
        self.watch_changes["in"] += len(set(self.candidates) - before)
        self.watch_changes["out"] += len(before - set(self.candidates))
        self.scan_stats["ROUNDS"] += 1
        self.scan_stats["CANDIDATE_SLOTS"] += len(picked)

    def watched(self) -> List[Key]:
        """要有逐筆的股票：進榜的＋持倉／委託中的。"""
        return sorted(set(self.candidates) | set(self.positions) | set(self.pending))

    def _publish_board(self, t_us: int) -> None:
        board = []
        for key, c in self.candidates.items():
            board.append({f: c.get(f) for f in BOARD_FIELDS if f in c} | {"market": key[0], "symbol": key[1]})
        board.sort(key=lambda r: (r.get("rank") or 999))
        self.board, self.board_time = board, t_us
        self.on_event("board", t_us, {"board": board})

    # ------------------------------------------------------------------ 逐筆
    def ensure_state(self, key: Key, prev_close: Optional[float], day_trade_eligible: Optional[bool]) -> M.SymState:
        state = self.states.get(key)
        if state is None:
            u = self.universe.get(key, {})
            info = M.RefInfo(prev_close=prev_close, day_trade_eligible=day_trade_eligible,
                             sector=self.sector_names.get(key[1], ""), sector_code=str(u.get("industry") or ""),
                             name=str(u.get("name") or ""))
            state = M.SymState(market=key[0], symbol=key[1], name=info.name, info=info,
                               buffer=self.intraday.IncrementalEventBuffer(self.ip))
            self.states[key] = state
        self.day_trade[key[1]] = day_trade_eligible
        return state

    def seed(self, key: Key, ticks: Sequence[tuple]) -> None:
        """新進榜：把它當天已收完的 K 棒（還原成逐筆）先餵進去，不做任何決策。"""
        state = self.states[key]
        for tick in ticks:
            if tick[M.T_TIME] > state.event_time or (tick[M.T_TIME] == state.event_time and tick[M.T_SERIAL] > state.serial):
                self._update_state(state, tick)

    def on_tick(self, key: Key, tick: tuple) -> None:
        """tick = (t_us, serial, price, size(張), cumulative_volume(張) 或 None, bid, ask)。"""
        state = self.states.get(key)
        if state is None:
            raise ValueError(f"{key} 還沒 ensure_state 就收到逐筆")
        t_us = tick[M.T_TIME]
        if t_us < state.event_time or (t_us == state.event_time and tick[M.T_SERIAL] <= state.serial):
            return  # 補抓過的成交
        self.now_us = max(self.now_us, t_us)
        self._update_state(state, tick)
        trade = self.open_trades.get(key)
        if trade is not None:
            trade.max_price = max(trade.max_price, tick[M.T_PRICE])
            trade.min_price = min(trade.min_price, tick[M.T_PRICE])
        order = self.pending.get(key)
        if order is not None and t_us >= order.fill_after_us:
            self._fill(order, state, t_us, tick)
        if t_us >= self.exit_us:
            self._clock_exits(t_us)
        self._evaluate(key, state, t_us)

    def clock(self, t_us: int) -> None:
        self.now_us = max(self.now_us, t_us)
        if t_us >= self.exit_us:
            self._clock_exits(t_us)

    def finish(self) -> None:
        """收盤：還沒成交的委託、還有的部位，用最後狀態了結（同 DaySimulator.run 的結尾）。"""
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
            order = M.Order(side="SELL", key=key, quantity=int(self.positions[key]["managed_quantity"]),
                            decided_us=state.event_time, fill_after_us=state.event_time, signal={}, decision={},
                            reason="EOD_FORCED_CLOSE")
            self.pending[key] = order
            self._fill(order, state, state.event_time, None)

    # ------------------------------------------------------------------ 事件（網站時間軸）
    def _decide_buy(self, key, state, signal, t_us, now) -> None:
        had = key in self.pending
        super()._decide_buy(key, state, signal, t_us, now)
        if not had and key in self.pending:
            o = self.pending[key]
            self.on_event("signal", t_us, {"symbol": key[1], "market": key[0], "name": state.name, "side": "BUY",
                                           "strategy": signal.get("strategy_type"), "setup": signal.get("entry_setup_type"),
                                           "price": state.price, "quantity": o.quantity,
                                           "stop": float(o.decision.get("effective_stop_price") or signal.get("strategy_stop_price") or 0)})

    def _decide_sell(self, key, state, signal, t_us, now, reason) -> None:
        had = key in self.pending
        super()._decide_sell(key, state, signal, t_us, now, reason)
        if not had and key in self.pending:
            self.on_event("exit_signal", t_us, {"symbol": key[1], "market": key[0], "name": state.name, "reason": reason,
                                                "price": state.price, "quantity": self.pending[key].quantity})

    def _fill_buy(self, order, state, t_us, price, mid, basis) -> None:
        super()._fill_buy(order, state, t_us, price, mid, basis)
        p = self.positions.get(order.key) or {}
        self.on_event("fill", t_us, {"symbol": state.symbol, "market": state.market, "name": state.name, "price": price,
                                     "quantity": order.quantity, "stop": float(p.get("effective_stop_price") or 0),
                                     "strategy": p.get("strategy_type")})

    def _fill_sell(self, order, state, t_us, price, mid, basis) -> None:
        before = len(self.trades)
        super()._fill_sell(order, state, t_us, price, mid, basis)
        if len(self.trades) > before:
            self.on_event("closed", t_us, dict(self.trades[-1]))
        else:
            self.on_event("partial", t_us, {"symbol": state.symbol, "name": state.name, "price": price,
                                            "quantity": order.quantity, "reason": order.reason})

    # ------------------------------------------------------------------ 狀態
    def positions_view(self) -> List[Dict[str, Any]]:
        out = []
        for key, p in self.positions.items():
            state = self.states[key]
            trade = self.open_trades.get(key)
            qty = int(p["managed_quantity"])
            entry = float(p["entry_price"])
            last = state.price
            open_pnl = None
            if last is not None:
                exit_ = last - self._tick(last, key[1])  # 現在賣的話：吃一檔
                amount = exit_ * qty * 1000
                open_pnl = (exit_ - entry) * qty * 1000 - self.cost.commission(amount) - self.cost.tax(amount) - (trade.buy_fee if trade else 0)
            out.append({"symbol": key[1], "market": key[0], "name": state.name, "strategy": p.get("strategy_type"),
                        "entry_time": trade.entry_time[11:19] if trade else None, "entry_price": entry, "quantity": qty,
                        "stop": float(p.get("effective_stop_price") or 0), "last": last,
                        "open_pnl": None if open_pnl is None else round(open_pnl, 0)})
        return out


def bar_ticks(day: str, minute_bars: Sequence[Tuple[int, float, float, float, float, int]], serial0: int = 0) -> List[tuple]:
    """1 分 K → 逐筆（同 studies/ms_tmf 的還原法：開 → 極值 → 極值 → 收，每 2 秒內插、價格取到跳動單位、量平均分）。
    minute_bars：[(那根 K 的開始時間 μs, 開, 高, 低, 收, 量張)]；回傳的 volume 是到那一筆為止的累計張數。"""
    out, serial, cum = [], serial0, 0
    path_s = (0, 20, 40, 59)
    secs = list(range(0, 59, 2)) + [59]
    for t0, o, h, low, c, v in minute_bars:
        x1, x2 = (low, h) if c >= o else (h, low)
        pts = list(zip(path_s, (o, x1, x2, c)))
        prices = []
        for s in secs:
            for (s0, p0), (s1, p1) in zip(pts, pts[1:]):
                if s0 <= s <= s1:
                    raw = p0 + (p1 - p0) * (s - s0) / (s1 - s0)
                    prices.append(round_tick(raw, lo=min(o, h, low, c), hi=max(o, h, low, c)))
                    break
        n = len(prices)
        sizes = [v // n + (1 if k < v % n else 0) for k in range(n)]
        for s, p, size in zip(secs, prices, sizes):
            cum += size
            out.append((t0 + s * M.US, serial, p, size, cum, None, None))
            serial += 1
    return out


def tick_size(price: float) -> float:
    for limit, step in ((10, 0.01), (50, 0.05), (100, 0.1), (500, 0.5), (1000, 1.0)):
        if price < limit:
            return step
    return 5.0


def round_tick(price: float, lo: float, hi: float) -> float:
    step = tick_size(price)
    p = round(round(price / step) * step, 2)
    return float(min(max(p, lo), hi))


def minute_of(t_us: int) -> int:
    dt = M.us_to_dt(t_us)
    return dt.hour * 60 + dt.minute


def day_start_us(day: str, hh: int, mm: int) -> int:
    d = date.fromisoformat(day)
    return int(datetime(d.year, d.month, d.day, hh, mm, tzinfo=M.TPE).timestamp() * M.US)


def finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)
