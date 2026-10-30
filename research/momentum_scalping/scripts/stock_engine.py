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

import futures_replay as FR
import weak_short as WS
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
        # 放空子帳戶（studies/short_paper）：漲停價、可否放空由呼叫端填（盤中：富邦代號表；重播：昨收 × 1.1、全當可以）
        self.limit_up: Dict[str, float] = {}
        self.short_ok: Dict[str, bool] = {}
        self.short: Optional["ShortBook"] = None
        self.short_lookup: Optional[Callable[[str], bool]] = None  # 盤中由 stock_live 接富邦代號表；重播沒有就當作可以

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
        if self.short is not None:
            self.short.weak_tick(t_us)

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

    def _refresh_market_and_scan(self, t_us: int) -> None:
        """同原系統 runtime 的分法：大盤（漲跌家數）用全市場快照；類股強弱與 Scanner 只用當下成交金額前
        scanner.active_universe_n（預設 500）檔＝原系統 gateway.get_active_universe()。
        冷門股的最後成交時間很舊，混進類股會讓整個類股被判「資料過時」、趨勢策略永遠不合格（2026-10-29 重播查到）。"""
        now = M.us_to_dt(t_us)
        now_iso = now.isoformat(timespec="microseconds")
        rows = self._scanner_rows(now_iso)
        active = sorted(rows, key=lambda r: (-float(r.get("turnover") or 0.0), -int(r.get("volume") or 0),
                                             r["market"], r["symbol"]))[: int(self.sp.active_universe_n)]
        feed = self.index_feed
        market_data = {
            "taiex": feed.quote(self.day, "TAIEX", t_us, now_iso), "taiex_history": feed.history(self.day, "TAIEX"),
            "tpex": feed.quote(self.day, "TPEX", t_us, now_iso), "tpex_history": feed.history(self.day, "TPEX"),
            "twse_rows": [r for r in rows if r["market"] == "TWSE"], "tpex_rows": [r for r in rows if r["market"] != "TWSE"],
            "sector_map": dict(self.sector_names), "session_state": "REGULAR", "session_trade_date": self.trade_date_compact,
            "realtime_buy_data_ready": True, "breadth_snapshot_acquired_at": {"TWSE": now_iso, "TPEX": now_iso},
            "permission_evaluated_at": now_iso,
        }
        context = dict(self.market_mod.calculate_market_context(market_data, params=self.mp, now=now))
        context["market_snapshot"] = {"snapshot_id": context.get("data_fingerprint"), "stale": False,
                                      "publication_state": "PUBLISHED", "refreshed_at": now_iso,
                                      "session_trade_date": self.trade_date_compact}
        self.market_context = self._finish_context(context, active, now, now_iso)
        self.active_count = len(active)
        self._scan(active, now_iso)

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
        """要有逐筆的股票：進榜的＋持倉／委託中的＋放空子帳戶持有／等成交的。"""
        keys = set(self.candidates) | set(self.positions) | set(self.pending)
        if self.short is not None:
            known = {k[1]: k for k in self.states}
            for sym in set(self.short.open) | set(self.short.pending):
                if sym in known:
                    keys.add(known[sym])
        return sorted(keys)

    def _publish_board(self, t_us: int) -> None:
        board = []
        for key, c in self.candidates.items():
            board.append({f: c.get(f) for f in BOARD_FIELDS if f in c} | {"market": key[0], "symbol": key[1]})
        board.sort(key=lambda r: (r.get("rank") or 999))
        self.board, self.board_time = board, t_us
        self.on_event("board", t_us, {"board": board})
        if self.short is not None:
            self.short.handle_event("board", t_us, {"board": board})

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
        if self.short is not None:
            self.short.on_tick(key, tick)

    def clock(self, t_us: int) -> None:
        self.now_us = max(self.now_us, t_us)
        if t_us >= self.exit_us:
            self._clock_exits(t_us)
        if self.short is not None:
            self.short.clock(t_us)

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
        if self.short is not None:
            self.short.finish()

    # ------------------------------------------------------------------ 事件（網站時間軸）
    def _decide_buy(self, key, state, signal, t_us, now) -> None:
        had = key in self.pending
        super()._decide_buy(key, state, signal, t_us, now)
        if not had and key in self.pending:
            o = self.pending[key]
            self.on_event("signal", t_us, {"symbol": key[1], "market": key[0], "name": state.name, "side": "BUY",
                                           "strategy": signal.get("strategy_type"), "setup": signal.get("entry_setup_type"),
                                           "why": FR.entry_reason_text(signal), "price": state.price, "quantity": o.quantity,
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
            if self.short is not None:
                self.short.handle_event("closed", t_us, dict(self.trades[-1]))
        else:
            self.on_event("partial", t_us, {"symbol": state.symbol, "name": state.name, "price": price,
                                            "quantity": order.quantity, "reason": order.reason})

    # ------------------------------------------------------------------ 狀態
    def positions_view(self) -> List[Dict[str, Any]]:
        out = []
        for key, p in self.positions.items():
            state = self.states[key]
            trade = self.open_trades.get(key)
            qty = int(p["managed_quantity"])  # 股（原版的數量單位）
            entry = float(p["entry_price"])
            last = state.price
            open_pnl = None
            if last is not None:
                exit_ = last - self._tick(last, key[1])  # 現在賣的話：吃一檔
                amount = exit_ * qty
                open_pnl = (exit_ - entry) * qty - self.cost.commission(amount) - self.cost.tax(amount) - (trade.buy_fee if trade else 0)
            out.append({"symbol": key[1], "market": key[0], "name": state.name, "strategy": p.get("strategy_type"),
                        "entry_time": trade.entry_time[11:19] if trade else None, "entry_price": entry,
                        "quantity": int(qty / 1000) if qty % 1000 == 0 else round(qty / 1000, 3),  # 張
                        "stop": float(p.get("effective_stop_price") or 0), "last": last,
                        "open_pnl": None if open_pnl is None else round(open_pnl, 0)})
        return out


def bar_ticks(day: str, minute_bars: Sequence[Tuple[int, float, float, float, float, int]], serial0: int = 0) -> List[tuple]:
    """1 分 K → 逐筆（同 studies/ms_tmf 的還原法：開 → 極值 → 極值 → 收，每 2 秒內插、價格取到跳動單位、量平均分）。
    minute_bars：[(那根 K 的開始時間 μs, 開, 高, 低, 收, 量張)]；回傳的 volume 是到那一筆為止的累計張數。
    買賣價：1 分 K 沒有，假設一檔價差——往上成交在賣價（買價低一檔）、往下成交在買價（賣價高一檔）、平盤沿用上一筆的那一邊。
    原版 Trading 沒有買賣價就拒絕買進（MISSING_BID_ASK），所以重播一定要有；盤中用富邦逐筆自帶的買賣價，不經過這裡。"""
    out, serial, cum = [], serial0, 0
    last_p, at_ask = None, False
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
            if last_p is not None and p != last_p:
                at_ask = p > last_p
            last_p = p
            step = tick_size(p)
            bid, ask = (round(p - step, 2), p) if at_ask else (p, round(p + step, 2))
            out.append((t0 + s * M.US, serial, p, size, cum, bid, ask))
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


# ============================================================================= 放空三條（studies/short_paper/PREREGISTRATION.md）
SHORT_REASON_TEXT = {"STOP": "停損（回到結構高點）", "LIMIT_UP": "逼近漲停強制回補", "TIME": "13:05 回補", "EOD": "收盤強制回補"}
SHORT_BOOK_NAME = {"A": "失速空", "B": "榜上過熱空（對沖）", "W": "弱勢順勢空"}


class ShortBook:
    """個股放空的記帳子帳戶（只記帳不下單）。掛在 StockEngine 上：
    A 失速空＝原版多單以結構破壞／跌破前低出場那一刻放空同一檔；B 榜上過熱空＝第一次進榜（10:30 前、漲 3～7%）放空 1 張＋小台對沖。
    規則與成本全部照事前登記，不從別處讀參數。"""

    MIN_ROOM, COVER_ROOM = 0.03, 0.015          # 離漲停 ≥3% 才進；≤1.5% 強制回補
    COVER_MIN = 13 * 60 + 5                     # 13:05 回補
    B_UNTIL, B_CHG = 10 * 60 + 30, (3.0, 7.0)   # B：10:30 前、進榜漲幅 3～7%
    B_MAX, B_STOP = 5, 1.02
    A_REASONS = ("MOMENTUM_STRUCTURE_BREAK_FULL", "HIGHER_LOW_BREAK")
    FEE_RATE, FEE_DISCOUNT, MIN_FEE, TAX, BORROW, HEDGE_COST = 0.001425, 0.28, 20.0, 0.0015, 0.0005, 0.0002

    def __init__(self, engine: "StockEngine", on_event: Callable[[str, int, Dict[str, Any]], None]):
        self.e = engine
        self.on_event = on_event
        self.pending: Dict[str, Dict[str, Any]] = {}   # symbol → 等成交的單（entry 或 cover）
        self.open: Dict[str, Dict[str, Any]] = {}      # symbol → 持有中的空單
        self.trades: List[Dict[str, Any]] = []
        self.done_today: set = set()                   # 同一檔一天最多一次（A、B 各算）
        self.seen_board: set = set()
        self.blocked: Counter = Counter()
        self.last_price: Dict[str, float] = {}
        self.weak = WS.WeakBook(on_event, self._shortable)  # 弱勢順勢空：不用網友的程式，只吃快照（PREREGISTRATION_WEAK.md）

    def _shortable(self, sym: str) -> bool:
        lookup = self.e.short_lookup
        return self.e.short_ok.get(sym, True) if lookup is None else lookup(sym)

    def weak_tick(self, t_us: int) -> None:
        """每輪快照：算兩個指數當日漲跌（昨收取日線），餵給弱勢順勢空。"""
        chg = {}
        for name, market in (("TAIEX", "TWSE"), ("TPEX", "TPEX")):
            q = self.e.index_feed.live.get(name)
            prev = [c for d, c in self.e.index_feed.daily.get(name, []) if d < self.e.day]
            if q is not None and prev:
                chg[market] = (float(q["price"]) / prev[-1] - 1) * 100
        rows = [{**r, "symbol": k[1], "market": k[0]} for k, r in self.e.rows.items()]
        self.weak.on_snapshot(t_us, rows, chg, self.market_ok())

    # ── 條件 ──
    def market_ok(self) -> bool:
        """濾網 C：加權與櫃買都低於當日開盤，且上漲家數淨比 < 0。"""
        live = self.e.index_feed.live
        if not all(n in live for n in ("TAIEX", "TPEX")):
            return False
        breadth = (self.e.market_context.get("breadth") or {}).get("net_ratio")
        return all(live[n]["price"] < live[n]["open"] for n in ("TAIEX", "TPEX")) and breadth is not None and float(breadth) < 0

    def room_ok(self, sym: str, price: float) -> bool:
        lu = self.e.limit_up.get(sym)
        return lu is not None and lu / price - 1 >= self.MIN_ROOM

    def _gate(self, book: str, sym: str, price: float) -> Optional[str]:
        if f"{book}:{sym}" in self.done_today or sym in self.open or sym in self.pending:
            return "ALREADY"
        if not self.e.short_ok.get(sym, True):
            return "NOT_SHORTABLE"
        if not self.room_ok(sym, price):
            return "NEAR_LIMIT_UP"
        if not self.market_ok():
            return "MARKET_FILTER"
        return None

    # ── 事件（原版多單平倉、榜單） ──
    def handle_event(self, kind: str, t_us: int, info: Dict[str, Any]) -> None:
        if kind == "closed":
            t = info
            if t.get("final_exit_reason") not in self.A_REASONS:
                return
            sym, price = t["symbol"], float(t["avg_exit_price"])
            why = self._gate("A", sym, price)
            if why:
                self.blocked[f"A:{why}"] += 1
                return
            high = float(t["entry_price"]) * (1 + float(t.get("mfe_pct") or 0) / 100)  # 多單持有期間最高價＝結構高點
            self._queue("A", sym, t.get("name") or "", int(t["quantity"]), t_us, stop=high,
                        text=f"原版多單{E_EXIT_TEXT.get(t['final_exit_reason'], t['final_exit_reason'])}出場 → 失速空 {lots_text(int(t['quantity']))}，停損 {high:g}")
        elif kind == "board":
            mnt = minute_of(t_us)
            for r in info["board"]:
                sym = r["symbol"]
                if sym in self.seen_board:
                    continue
                self.seen_board.add(sym)
                chg = r.get("change_pct")
                if mnt >= self.B_UNTIL or chg is None or not (self.B_CHG[0] <= float(chg) < self.B_CHG[1]):
                    continue
                if sum(1 for o in self.open.values() if o["book"] == "B") + sum(1 for p in self.pending.values() if p["book"] == "B") >= self.B_MAX:
                    self.blocked["B:MAX"] += 1
                    continue
                price = self.last_price.get(sym)
                if price is None:  # 剛進榜還沒收到逐筆：用這一輪快照的價
                    snap = self.e.rows.get((r.get("market"), sym)) or {}
                    price = snap.get("price")
                if price is None:
                    self.blocked["B:NO_PRICE"] += 1
                    continue
                why = self._gate("B", sym, price)
                if why:
                    self.blocked[f"B:{why}"] += 1
                    continue
                self._queue("B", sym, r.get("name") or "", 1000, t_us, stop=price * self.B_STOP,
                            text=f"進榜（漲 {float(chg):.1f}%）→ 過熱空 1 張，停損 {price * self.B_STOP:.2f}，同時記小台對沖")

    def _queue(self, book: str, sym: str, name: str, shares: int, t_us: int, stop: float, text: str) -> None:
        self.pending[sym] = {"book": book, "sym": sym, "name": name, "shares": shares, "stop": stop, "kind": "entry",
                             "after": t_us + 500_000, "signal_time": t_us}
        self.done_today.add(f"{book}:{sym}")
        self.on_event("short_signal", t_us, {"book": book, "symbol": sym, "name": name, "text": text})

    # ── 逐筆 ──
    def on_tick(self, key: Key, tick: tuple) -> None:
        sym = key[1]
        t_us, price, bid, ask = tick[M.T_TIME], tick[M.T_PRICE], tick[M.T_BID], tick[M.T_ASK]
        self.last_price[sym] = price
        p = self.pending.get(sym)
        if p is not None and t_us >= p["after"]:
            step = tick_size(price)
            if p["kind"] == "entry":
                fill = float(bid) if bid is not None else price - step
                self._open(p, t_us, fill)
            else:
                fill = float(ask) if ask is not None else price + step
                self._close(p, t_us, fill)
            self.pending.pop(sym, None)
            return
        o = self.open.get(sym)
        if o is None or sym in self.pending:
            return
        lu = self.e.limit_up.get(sym)
        if price >= o["stop"]:
            self._queue_cover(sym, t_us, "STOP", price)
        elif lu is not None and price >= lu * (1 - self.COVER_ROOM):
            self._queue_cover(sym, t_us, "LIMIT_UP", price)
        elif minute_of(t_us) >= self.COVER_MIN:
            self._queue_cover(sym, t_us, "TIME", price)

    def clock(self, t_us: int) -> None:
        """沒有成交也要到點回補：13:05 以後對每個持倉掛回補單（下一筆成交才成交）。"""
        if minute_of(t_us) < self.COVER_MIN:
            return
        for sym in list(self.open):
            if sym not in self.pending:
                self._queue_cover(sym, t_us, "TIME", self.last_price.get(sym))

    def _queue_cover(self, sym: str, t_us: int, reason: str, price) -> None:
        o = self.open[sym]
        self.pending[sym] = {**o, "kind": "cover", "reason": reason, "after": t_us + 500_000}
        self.on_event("short_signal", t_us, {"book": o["book"], "symbol": sym, "name": o["name"],
                                             "text": f"{SHORT_REASON_TEXT[reason]}（價 {price:g}）→ 回補"})

    def _idx(self) -> Optional[float]:
        q = self.e.index_feed.live.get("TAIEX")
        return None if q is None else float(q["price"])

    def _open(self, p: Dict[str, Any], t_us: int, fill: float) -> None:
        amount = fill * p["shares"]
        o = {**p, "entry_time": t_us, "entry_price": fill, "fee_in": max(self.MIN_FEE, math.floor(amount * self.FEE_RATE * self.FEE_DISCOUNT)),
             "tax": math.floor(amount * self.TAX), "borrow": amount * self.BORROW, "idx0": self._idx() if p["book"] == "B" else None}
        self.open[p["sym"]] = o
        self.on_event("short_fill", t_us, {"book": o["book"], "symbol": o["sym"], "name": o["name"], "price": fill, "shares": o["shares"], "stop": o["stop"],
                                           "text": f"模擬放空 {lots_text(o['shares'])} @ {fill:g}，停損 {o['stop']:.2f}"})

    def _close(self, p: Dict[str, Any], t_us: int, fill: float) -> None:
        o = self.open.pop(p["sym"])
        amount = fill * o["shares"]
        fee_out = max(self.MIN_FEE, math.floor(amount * self.FEE_RATE * self.FEE_DISCOUNT))
        gross = (o["entry_price"] - fill) * o["shares"]
        net = gross - o["fee_in"] - fee_out - o["tax"] - o["borrow"]
        hedge = None
        if o["book"] == "B" and o["idx0"]:
            idx1 = self._idx()
            if idx1:
                notional = o["entry_price"] * o["shares"]
                hedge = notional * (idx1 / o["idx0"] - 1) - notional * self.HEDGE_COST
        row = {"book": o["book"], "symbol": o["sym"], "name": o["name"], "shares": o["shares"], "quantity": o["shares"] / 1000,
               "signal_time": M.us_to_dt(o["signal_time"]).strftime("%H:%M:%S"), "entry_time": M.us_to_dt(o["entry_time"]).strftime("%H:%M:%S"),
               "entry_price": o["entry_price"], "exit_time": M.us_to_dt(t_us).strftime("%H:%M:%S"), "exit_price": fill,
               "exit_reason": p["reason"], "stop": round(o["stop"], 2), "gross_pnl": round(gross, 0), "net_pnl": round(net, 0),
               "net_ret_pct": round(net / (o["entry_price"] * o["shares"]) * 100, 4),
               "hedge_pnl": None if hedge is None else round(hedge, 0), "net_hedged": None if hedge is None else round(net + hedge, 0),
               "limit_up_locked": p["reason"] == "EOD" and self.e.limit_up.get(o["sym"]) is not None and fill >= self.e.limit_up[o["sym"]] * 0.999}
        self.trades.append(row)
        self.on_event("short_exit", t_us, {"book": o["book"], "symbol": o["sym"], "name": o["name"], "price": fill,
                                           "text": f"模擬回補 @ {fill:g}（{SHORT_REASON_TEXT[p['reason']]}），這筆淨 {net:+,.0f} 元"
                                                   + (f"，含對沖 {net + hedge:+,.0f} 元" if hedge is not None else "")})

    def finish(self) -> None:
        """收盤：還沒成交的回補用最後一筆成交價＋一檔；還沒進場的取消。"""
        self.weak.finish()
        for sym, p in list(self.pending.items()):
            if p["kind"] == "cover":
                last = self.last_price.get(sym, p["entry_price"])
                self._close({**p, "reason": "EOD"}, self.e.now_us, last + tick_size(last))
            self.pending.pop(sym, None)
        for sym in list(self.open):
            last = self.last_price.get(sym, self.open[sym]["entry_price"])
            self._close({**self.open[sym], "reason": "EOD"}, self.e.now_us, last + tick_size(last))

    # ── 狀態 ──
    def snapshot(self) -> Dict[str, Any]:
        books = {}
        for b in ("A", "B"):
            T = [t for t in self.trades if t["book"] == b]
            opens = []
            for o in self.open.values():
                if o["book"] != b:
                    continue
                last = self.last_price.get(o["sym"])
                opn = None if last is None else round((o["entry_price"] - (last + tick_size(last))) * o["shares"] - o["fee_in"] - o["tax"] - o["borrow"], 0)
                opens.append({"symbol": o["sym"], "name": o["name"], "quantity": o["shares"] / 1000, "entry_time": M.us_to_dt(o["entry_time"]).strftime("%H:%M:%S"),
                              "entry_price": o["entry_price"], "stop": round(o["stop"], 2), "last": last, "open_pnl": opn})
            realized = round(sum(t["net_pnl"] for t in T), 0)
            hedged = round(sum((t["net_hedged"] if t["net_hedged"] is not None else t["net_pnl"]) for t in T), 0) if b == "B" else None
            books[b] = {"name": SHORT_BOOK_NAME[b], "trades": T, "positions": opens, "realized": realized,
                        "open_pnl": round(sum(p["open_pnl"] or 0 for p in opens), 0), "closed": len(T),
                        "wins": sum(t["net_pnl"] > 0 for t in T), "realized_hedged": hedged}
        books["W"] = self.weak.snapshot()
        return {"version": 1, "registration": "studies/short_paper/PREREGISTRATION.md", "market_ok": self.market_ok(),
                "blocked": {**self.blocked, **self.weak.blocked_counts()}, "books": books}


def lots_text(shares: int) -> str:
    v = shares / 1000
    return f"{int(v) if float(v).is_integer() else round(v, 3)} 張"


E_EXIT_TEXT = EXIT_TEXT
