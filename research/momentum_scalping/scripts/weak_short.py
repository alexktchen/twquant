#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""weak_short.py：弱勢順勢空（只記帳不下單）。規則與門檻照 studies/short_paper/PREREGISTRATION_WEAK.md，改規則就是新登記。

完全不用網友的程式：只吃每 30 秒的全市場快照，自己組 1 分 K、算均價、EMA 與擺動高低點。
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

TPE = timezone(timedelta(hours=8))
US = 1_000_000

ENTRY_FROM, ENTRY_UNTIL, COVER_MIN = 9 * 60 + 15, 12 * 60, 13 * 60 + 5
TOP_N, MAX_AGE_US = 500, 180 * US
CHG_LOW, CHG_HIGH, WEAKER_BY = -7.0, -3.0, 2.0   # −7% < 跌幅 ≤ −3%，比指數弱 2 個百分點
MIN_BARS, EMA_FAST, EMA_SLOW, SWING = 10, 5, 10, 2
MAX_CHASE, MAX_RISK = 0.01, 0.03                 # 跌破 L 不追超過 1%；停損距離不超過 3%
SHARES, MAX_OPEN = 1000, 5
FEE_RATE, FEE_DISCOUNT, MIN_FEE, TAX, BORROW = 0.001425, 0.28, 20.0, 0.0015, 0.0005
EXIT_TEXT = {"STOP": "停損（回到前高）", "VWAP": "站回均價線", "TIME": "13:05 回補", "EOD": "收盤回補"}


def tick_size(price: float) -> float:
    for limit, step in ((10, 0.01), (50, 0.05), (100, 0.1), (500, 0.5), (1000, 1.0)):
        if price < limit:
            return step
    return 5.0


def hms(t_us: int) -> str:
    return datetime.fromtimestamp(t_us / US, TPE).strftime("%H:%M:%S")


def minute_of(t_us: int) -> int:
    t = datetime.fromtimestamp(t_us / US, TPE)
    return t.hour * 60 + t.minute


class Series:
    """一檔的 1 分 K（用快照價組）、EMA 與擺動點。K 棒完成才進指標，不看未完成的那一根。"""

    def __init__(self) -> None:
        self.minute: Optional[int] = None
        self.h = self.l = self.c = 0.0
        self.highs: List[float] = []
        self.lows: List[float] = []
        self.fast: List[float] = []
        self.slow: List[float] = []
        self.swing_high: List[tuple] = []  # (第幾根, 價)
        self.swing_low: List[tuple] = []

    def add(self, minute: int, price: float) -> None:
        if self.minute is not None and minute != self.minute:
            self._close_bar()
        if minute != self.minute:
            self.minute, self.h, self.l = minute, price, price
        self.h, self.l, self.c = max(self.h, price), min(self.l, price), price

    def _close_bar(self) -> None:
        self.highs.append(self.h)
        self.lows.append(self.l)
        for out, n in ((self.fast, EMA_FAST), (self.slow, EMA_SLOW)):
            a = 2 / (n + 1)
            out.append(self.c if not out else a * self.c + (1 - a) * out[-1])
        i = len(self.highs) - 1 - SWING  # 中間那根要有前後各 2 根
        if i - SWING < 0:
            return
        side = [*range(i - SWING, i), *range(i + 1, i + SWING + 1)]
        if all(self.highs[i] > self.highs[j] for j in side):
            self.swing_high.append((i, self.highs[i]))
        if all(self.lows[i] < self.lows[j] for j in side):
            self.swing_low.append((i, self.lows[i]))

    def setup(self) -> Optional[Dict[str, float]]:
        """條件 6、7：回傳 {H2, L}；不成立回 None。"""
        if len(self.highs) < MIN_BARS or len(self.swing_high) < 2:
            return None
        if not (self.fast[-1] < self.slow[-1] and self.fast[-1] < self.fast[-2]):
            return None
        (_, h1), (i2, h2) = self.swing_high[-2:]
        if h2 >= h1:
            return None
        lows = [p for i, p in self.swing_low if i < i2]
        return {"H2": h2, "L": lows[-1]} if lows else None


class WeakBook:
    """on_snapshot 吃一輪快照；book 代號 W。shortable(sym) 只在要進場時才問，省富邦的查詢額度。"""

    def __init__(self, on_event: Callable[[str, int, Dict[str, Any]], None], shortable: Callable[[str], bool] = lambda s: True):
        self.on_event, self.shortable = on_event, shortable
        self.series: Dict[str, Series] = {}
        self.open: Dict[str, Dict[str, Any]] = {}
        self.trades: List[Dict[str, Any]] = []
        self.done: set = set()
        self.blocked: Dict[str, set] = {}
        self.last_price: Dict[str, float] = {}
        self.now_us = 0

    def _block(self, why: str, sym: str) -> None:
        self.blocked.setdefault(f"W:{why}", set()).add(sym)

    def on_snapshot(self, t_us: int, rows: Sequence[Mapping[str, Any]], index_chg: Mapping[str, float], market_ok: bool) -> None:
        self.now_us = max(self.now_us, t_us)
        mnt = minute_of(t_us)
        fresh = [r for r in rows if r.get("price") and r.get("event_time") and t_us - r["event_time"] <= MAX_AGE_US]
        for r in fresh:
            self.series.setdefault(r["symbol"], Series()).add(mnt, float(r["price"]))
            self.last_price[r["symbol"]] = float(r["price"])
        by = {r["symbol"]: r for r in fresh}
        for sym in list(self.open):
            if sym in by:
                self._check_exit(sym, by[sym], t_us, mnt)
        if not (ENTRY_FROM <= mnt < ENTRY_UNTIL):
            return
        top = sorted(fresh, key=lambda r: (-float(r.get("turnover") or 0), r["symbol"]))[:TOP_N]
        for r in top:
            self._check_entry(r, t_us, index_chg, market_ok)

    @staticmethod
    def vwap(r: Mapping[str, Any]) -> Optional[float]:
        vol = r.get("volume") or 0  # 快照的 volume 單位是張（2026-10-29 實際快照驗過）
        return float(r["turnover"]) / (vol * 1000) if vol and r.get("turnover") else None

    def _check_entry(self, r: Mapping[str, Any], t_us: int, index_chg: Mapping[str, float], market_ok: bool) -> None:
        sym, price, prev = r["symbol"], float(r["price"]), r.get("prev_close")
        idx = index_chg.get(r.get("market"))
        vw = self.vwap(r)
        if not prev or idx is None or vw is None:
            return
        chg = (price / float(prev) - 1) * 100
        if not (CHG_LOW < chg <= CHG_HIGH) or chg > idx - WEAKER_BY or price >= vw:
            return
        s = self.series.get(sym)
        setup = s.setup() if s else None
        if setup is None or not (setup["L"] * (1 - MAX_CHASE) <= price < setup["L"]):
            return
        if sym in self.done or sym in self.open:
            return self._block("ALREADY", sym)
        if setup["H2"] / price - 1 > MAX_RISK:
            return self._block("RISK", sym)
        if len(self.open) >= MAX_OPEN:
            return self._block("MAX", sym)
        if not self.shortable(sym):
            return self._block("NOT_SHORTABLE", sym)
        fill = round(price - tick_size(price), 2)
        amount = fill * SHARES
        self.done.add(sym)
        name = r.get("name") or ""
        self.open[sym] = {"sym": sym, "name": name, "stop": setup["H2"], "market_ok": market_ok, "entry_time": t_us, "entry_price": fill,
                          "fee_in": max(MIN_FEE, math.floor(amount * FEE_RATE * FEE_DISCOUNT)), "tax": math.floor(amount * TAX), "borrow": amount * BORROW}
        text = (f"跌 {-chg:.1f}%、比大盤弱 {idx - chg:.1f} 個百分點，在均價線 {vw:.2f} 之下、5 分均線在 10 分均線之下，"
                f"反彈 {setup['H2']:g} 不過前高後跌破 {setup['L']:g} → 弱勢空 1 張")
        self.on_event("short_signal", t_us, {"book": "W", "symbol": sym, "name": name, "text": text})
        self.on_event("short_fill", t_us, {"book": "W", "symbol": sym, "name": name, "price": fill, "shares": SHARES, "stop": setup["H2"],
                                           "text": f"模擬放空 1 張 @ {fill:g}，停損 {setup['H2']:g}"})

    def _check_exit(self, sym: str, r: Mapping[str, Any], t_us: int, mnt: int) -> None:
        o, price = self.open[sym], float(r["price"])
        vw = self.vwap(r)
        why = "STOP" if price >= o["stop"] else "VWAP" if vw is not None and price > vw else "TIME" if mnt >= COVER_MIN else None
        if why:
            self._close(sym, t_us, price, why)

    def _close(self, sym: str, t_us: int, price: float, why: str) -> None:
        o = self.open.pop(sym)
        fill = round(price + tick_size(price), 2)
        fee_out = max(MIN_FEE, math.floor(fill * SHARES * FEE_RATE * FEE_DISCOUNT))
        gross = (o["entry_price"] - fill) * SHARES
        net = gross - o["fee_in"] - fee_out - o["tax"] - o["borrow"]
        self.trades.append({"book": "W", "symbol": sym, "name": o["name"], "shares": SHARES, "quantity": 1, "signal_time": hms(o["entry_time"]),
                            "entry_time": hms(o["entry_time"]), "entry_price": o["entry_price"], "exit_time": hms(t_us), "exit_price": fill,
                            "exit_reason": why, "stop": round(o["stop"], 2), "gross_pnl": round(gross, 0), "net_pnl": round(net, 0),
                            "net_ret_pct": round(net / (o["entry_price"] * SHARES) * 100, 4), "hedge_pnl": None, "net_hedged": None,
                            "limit_up_locked": False, "market_ok": o["market_ok"]})
        self.on_event("short_exit", t_us, {"book": "W", "symbol": sym, "name": o["name"], "price": fill,
                                           "text": f"模擬回補 @ {fill:g}（{EXIT_TEXT[why]}），這筆淨 {net:+,.0f} 元"})

    def finish(self) -> None:
        for sym in list(self.open):
            self._close(sym, self.now_us, self.last_price.get(sym, self.open[sym]["entry_price"]), "EOD")

    def snapshot(self) -> Dict[str, Any]:
        T = self.trades
        positions = []
        for o in self.open.values():
            last = self.last_price.get(o["sym"])
            opn = None if last is None else round((o["entry_price"] - (last + tick_size(last))) * SHARES - o["fee_in"] - o["tax"] - o["borrow"], 0)
            positions.append({"symbol": o["sym"], "name": o["name"], "quantity": 1, "entry_time": hms(o["entry_time"]), "entry_price": o["entry_price"],
                              "stop": round(o["stop"], 2), "last": last, "open_pnl": opn})
        return {"name": "弱勢順勢空", "trades": T, "positions": positions, "realized": round(sum(t["net_pnl"] for t in T), 0),
                "open_pnl": round(sum(p["open_pnl"] or 0 for p in positions), 0), "closed": len(T), "wins": sum(t["net_pnl"] > 0 for t in T),
                "realized_hedged": None}

    def blocked_counts(self) -> Dict[str, int]:
        return {k: len(v) for k, v in self.blocked.items()}
