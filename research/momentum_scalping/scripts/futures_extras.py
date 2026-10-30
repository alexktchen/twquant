#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""futures_extras.py：台指期三條對照策略（只記帳不下單，都沒通過驗證；使用者 2026-10-30 要求接進系統）。

- 區間突圍（原噪音區，多空都做）：studies/fubon_9y，規則同 intraday_bidir_test.run_noise。
- 回測確認（箱型回測版）：studies/box_break 的 B，規則同 box_break_test.rule_b。
- 尾盤順勢空：studies/short_composite 的 B，13:15 低於昨收就空。沒有接三票閘門（要盤前抓籌碼），所以是單腿。
寫回 payload 的 extras，不併進主帳；改規則就是新登記。
"""
from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

import intraday_bidir_test as B
import ms_replay as M

SLEEVES = ("NOISE_LONG", "NOISE_SHORT", "BOX_LONG", "BOX_SHORT", "TAIL_SHORT")
NAME = {"NOISE_LONG": "區間突圍・多", "NOISE_SHORT": "區間突圍・空", "BOX_LONG": "回測確認・多", "BOX_SHORT": "回測確認・空", "TAIL_SHORT": "尾盤順勢空"}
BOX_BARS, STEP_MIN, RETEST_BARS, TAIL_AT = 12, 5, 6, "13:15"
REASON = {"EOD": "收盤前平倉", "EXIT": "訊號消失", "FLIP": "反手", "MID": "跌回箱子中線"}


class Extras:
    """掛在 LiveCombo 上：只讀它的 t／p／vwap，自己記訂單與帳，不影響主帳。"""

    def __init__(self, live, history: Sequence[Any], clock):
        self.live, self.clock = live, clock
        self.t0 = clock(live.day, B.SESSION_OPEN)
        self.eod = clock(live.day, B.EOD)
        self.tail_at = clock(live.day, TAIL_AT)
        self.pos: Dict[str, Optional[Dict[str, Any]]] = {s: None for s in SLEEVES}  # 持倉 {"side","entry":(t,p),"signal"}
        self.pending: List[Tuple[int, str, int, str, int]] = []                     # (due, sleeve, side, kind, signal_t)
        self.trades: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []
        self.sigma = None
        if len(history) >= B.NOISE_LOOKBACK:
            self.sigma = {m: statistics.fmean(abs(h.p[h.last_before(h.at(m))] / h.open - 1) for h in history) for m in B.NOISE_CHECKS}
        self.checks = [(self.t0 + m * 60 * M.US, m) for m in B.NOISE_CHECKS] if self.sigma else []
        self.next_check = 0
        self.noise = 0
        self.tail_done = self.eod_done = False
        self.bars: List[Tuple[int, float, float, float]] = []  # 收完的 5 分 K：(收完時刻, 高, 低, 收)
        self.cur: Optional[List[float]] = None                 # [第幾根, 高, 低, 收]
        self.box_pos, self.box_setup, self.box_mid = 0, None, None

    def note(self, t_us: int, kind: str, sleeve: str, text: str) -> None:
        self.events.append({"time": M.us_to_dt(t_us).strftime("%H:%M:%S"), "kind": kind, "sleeve": sleeve, "text": f"{NAME[sleeve]}：{text}"})

    def order(self, T: int, sleeve: str, side: int, kind: str, signal_t: int) -> None:
        self.pending.append((T + B.LATENCY_US, sleeve, side, kind, signal_t))

    # ── 逐筆：先結 K、再做到點的決策、最後成交 ──
    def on_tick(self, i: int) -> None:
        if self.live.disabled is not None:
            return
        t_us, price = self.live.t[i], self.live.p[i]
        self._bar(t_us, price)
        self._noise(t_us)
        self._tail(t_us)
        self._eod(t_us)
        self._fill(i)

    def _bar(self, t_us: int, price: float) -> None:
        k = (t_us - self.t0) // (STEP_MIN * 60 * M.US)
        if self.cur is not None and k != self.cur[0]:
            end = self.t0 + (int(self.cur[0]) + 1) * STEP_MIN * 60 * M.US
            self.bars.append((end, self.cur[1], self.cur[2], self.cur[3]))
            if end <= self.eod:
                self._box(len(self.bars) - 1)
            self.cur = None
        if self.cur is None:
            self.cur = [k, price, price, price]
        self.cur[1], self.cur[2], self.cur[3] = max(self.cur[1], price), min(self.cur[2], price), price

    def _box(self, i: int) -> None:
        """同 box_break_test.rule_b：突破不追，6 根內回測守住才進，跌回中線出，不反手。"""
        if i < BOX_BARS:
            return
        end, high, low, close = self.bars[i]
        if end >= self.eod:
            return
        if self.box_pos:
            if (self.box_pos > 0 and close < self.box_mid) or (self.box_pos < 0 and close > self.box_mid):
                sleeve = "BOX_LONG" if self.box_pos > 0 else "BOX_SHORT"
                self.order(end, sleeve, -self.box_pos, "MID", end)
                self.note(end, "signal", sleeve, f"收盤 {close:.0f} 穿過箱子中線 {self.box_mid:.0f} → 平倉")
                self.box_pos, self.box_setup = 0, None
            return
        if self.box_setup is not None:
            side, level, mid, born = self.box_setup
            if i - born > RETEST_BARS:
                self.box_setup = None
            elif (side > 0 and low <= level and close > level) or (side < 0 and high >= level and close < level):
                sleeve = "BOX_LONG" if side > 0 else "BOX_SHORT"
                self.order(end, sleeve, side, "entry", end)
                self.note(end, "signal", sleeve, f"回測 {level:.0f} 守住（收 {close:.0f}）→ 模擬{'買進' if side > 0 else '放空'} 1 口")
                self.box_pos, self.box_mid, self.box_setup = side, mid, None
                return
        if self.box_setup is None:
            past = self.bars[i - BOX_BARS : i]
            top, bottom = max(b[1] for b in past), min(b[2] for b in past)
            if close > top:
                self.box_setup = (1, top, (top + bottom) / 2, i)
            elif close < bottom:
                self.box_setup = (-1, bottom, (top + bottom) / 2, i)

    def _noise(self, now: int) -> None:
        live = self.live
        while self.next_check < len(self.checks) and now >= self.checks[self.next_check][0]:
            T, m = self.checks[self.next_check]
            self.next_check += 1
            if T >= self.eod or not live.t or live.t[0] >= T:
                continue
            i = live.last_before(T)
            up, down = max(live.p[0], live.prev_close), min(live.p[0], live.prev_close)
            ub, lb = up * (1 + self.sigma[m]), down * (1 - self.sigma[m])
            target = 1 if live.p[i] > max(ub, live.vwap[i]) else -1 if live.p[i] < min(lb, live.vwap[i]) else 0
            if target == self.noise:
                continue
            if self.noise:
                sleeve = "NOISE_LONG" if self.noise > 0 else "NOISE_SHORT"
                self.order(T, sleeve, -self.noise, "FLIP" if target else "EXIT", T)
            if target:
                sleeve = "NOISE_LONG" if target > 0 else "NOISE_SHORT"
                self.order(T, sleeve, target, "entry", T)
                self.note(T, "signal", sleeve, f"價 {live.p[i]:.0f} {'站上' if target > 0 else '跌破'}區間 {(ub if target > 0 else lb):.0f} 與均價線 → 模擬{'買進' if target > 0 else '放空'} 1 口")
            self.noise = target

    def _tail(self, now: int) -> None:
        live = self.live
        if self.tail_done or now < self.tail_at:
            return
        self.tail_done = True
        if self.tail_at >= self.eod or not live.t or live.t[0] >= self.tail_at:
            return
        price = live.p[live.last_before(self.tail_at)]
        if price < live.prev_close:
            self.order(self.tail_at, "TAIL_SHORT", -1, "entry", self.tail_at)
            self.note(self.tail_at, "signal", "TAIL_SHORT", f"13:15 價 {price:.0f} 低於昨收 {live.prev_close:.0f} → 模擬放空 1 口")

    def _eod(self, now: int) -> None:
        if self.eod_done or now < self.eod:
            return
        self.eod_done = True
        waiting = {p[1] for p in self.pending if p[3] == "entry"}
        for s in SLEEVES:
            o = self.pos[s]
            if o is not None or s in waiting:
                side = o["side"] if o is not None else (-1 if s.endswith("SHORT") else 1)
                self.order(self.eod, s, -side, "EOD", self.eod)

    def _fill(self, i: int) -> None:
        keep = []
        t_us, last = self.live.t[i], self.live.p[i]
        for due, sleeve, side, kind, signal_t in self.pending:
            if t_us < due:
                keep.append((due, sleeve, side, kind, signal_t))
                continue
            price = last + side * B.SLIP
            if kind == "entry":
                self.pos[sleeve] = {"side": side, "entry": (t_us, price), "signal": signal_t}
                self.note(t_us, "fill", sleeve, f"模擬{'買進' if side > 0 else '放空'} {price:.0f}")
            else:
                o = self.pos[sleeve]
                if o is not None:
                    self._book(sleeve, o, t_us, price, kind)
                    self.pos[sleeve] = None
        self.pending = keep

    def _book(self, sleeve: str, o: Dict[str, Any], t_us: int, xp: float, reason: str) -> None:
        live, side = self.live, o["side"]
        et, ep = o["entry"]
        points = side * (xp - ep)
        net = points * live.point_value - 2 * live.commission - B.TAX * (ep + xp) * live.point_value
        fmt = lambda x: M.us_to_dt(x).strftime("%H:%M:%S")  # noqa: E731
        self.trades.append({"sleeve": sleeve, "side": side, "entry_time": fmt(et), "entry_price": ep, "exit_time": fmt(t_us), "exit_price": xp,
                            "exit_reason": reason, "points": round(points, 2), "net_pnl": round(net, 1)})
        self.note(t_us, "exit", sleeve, f"模擬{'賣出' if side > 0 else '回補'} {xp:.0f}（{REASON.get(reason, reason)}），這筆淨 {net:+,.0f} 元")

    def snapshot(self, last: Optional[float]) -> Dict[str, Any]:
        live, out = self.live, {}
        for s in SLEEVES:
            realized = round(sum(t["net_pnl"] for t in self.trades if t["sleeve"] == s), 1)
            o, open_pnl = self.pos[s], None
            if o is not None and last is not None:
                side, ep = o["side"], o["entry"][1]
                xp = last - side * B.SLIP
                open_pnl = round(side * (xp - ep) * live.point_value - 2 * live.commission - B.TAX * (ep + xp) * live.point_value, 1)
            out[s] = {"name": NAME[s], "position": o["side"] if o else 0, "entry_price": o["entry"][1] if o else None, "realized": realized, "open_pnl": open_pnl,
                      "closed": sum(t["sleeve"] == s for t in self.trades)}
        return {"version": 1, "sleeves": out, "trades": self.trades, "events": self.events, "noise_ready": self.sigma is not None}
