#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fubon_live.py — 富邦即時行情：錄小台／台指逐筆與小台五檔（只存本機），同時即時跑固定組合
「ORB 只做多 + 噪音區只放空」的模擬單（只記帳，不送任何委託）。

規則、參數、成交方式全部來自 intraday_bidir_test.py（與 paper/、forward_paper.py 同一套，2026-10-26 凍結）：
訊號在 T 發出 → 以 T + 0.5 秒後的第一筆成交價成交，買 +1 點、賣 −1 點。
收盤後用同一個逐筆引擎（B.run_orb／B.run_noise）把錄到的逐筆重算一次，當作正式結果，並和盤中即時紀錄對照。
開高走低（GapFail，studies/gap_fail）2026-10-29 拿掉（使用者：「開高走低策略直接拿掉」；T-461／D-220 修訂八）。
網友原版 Momentum_Scalping（Netizen；studies/ms_tmf，只做多 1 口、futures_replay.DayEngine 逐筆跑）放在 payload 的 ms。
盤口與大單（Flow，T-455）：每筆成交照當下買賣價分主動買／主動賣、5 口以上記成大單、五檔留最新與每分鐘最後一份，放在 payload 的 flow。

一定要在 repo 以外的資料夾執行：富邦 SDK 會在「執行目錄」寫含個資的 log/。程式一開始就 chdir 到 --out。

用法：
  python fubon_live.py --env D:/project/twquant/.env --out D:/project/twquant/data/research_ms/fubon_live \
      --history D:/project/twquant/data/research_ms/taifex_30d/csv
  檢查用（不連富邦）：加 --replay-day 2026-10-22，用 --history 裡那天的期交所逐筆重播
  --upload：把狀態寫進雲端 serving.futures_paper（網站「小台模擬」分頁讀它；DSN 取 .env 的 TWQUANT_SUPABASE_DSN）
  收盤後重建（含盤口與大單）：--replay-raw 2026-10-28 [--env ... --upload]，讀 <out>/<日>/raw_*.jsonl(.gz)

網站狀態（payload）的欄位是網站 frontend/src/cloud/futures.ts 的 FuturesDay；改這裡要一起改那裡。
"""
from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import json
import os
import queue
import shutil
import statistics
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import futures_replay as F  # noqa: E402
import intraday_bidir_test as B  # noqa: E402
import ms_replay as M  # noqa: E402

MONTH_CODE = "ABCDEFGHIJKL"
PRODUCT_TAIFEX = "MTX"
# 富邦代號 → 期交所商品代號：小台（模擬單用）、大台、微台（2024-07-29 上市）
FUBON_PRODUCTS = ("MXF", "TXF", "TMF")
TAIFEX_CODE = {"MXF": "MTX", "TXF": "TX", "TMF": "TMF"}
LOGIN_UNTIL, START_AT, STOP_AT = "09:30", "08:25", "13:47"
REASON_TEXT = {"STOP": "停損", "TARGET": "停利", "EOD": "收盤前", "EXIT": "回到下緣之上", "FLIP": "反手"}
OPEN_SLACK_S = 60              # 第一筆成交晚於 08:45 這麼多秒＝開盤沒錄到，當天不做模擬單
BACKFILL_LIMIT = 500           # 補抓當天逐筆：每頁幾筆（富邦日內行情每分鐘最多 300 次）
DECISION_GRACE_S = 3.0          # 到了決策時點、還沒收到之後的成交時，最多再等幾秒才用手上的逐筆決策
RECONNECT_WAIT_S = 5.0
UPLOAD_EVERY_S = 15.0         # 盤中多久寫一次雲端（有新事件時立刻寫）
DSN_ENV = "TWQUANT_SUPABASE_DSN"
# 與 twquant serving schema 的 FUTURES_PAPER_SPEC 產生的建表語句相同（tests/test_futures_paper_store.py 守著那邊）
TABLE_DDL = ('CREATE TABLE IF NOT EXISTS "serving"."futures_paper" '
             "(day date, source text, status text, payload jsonb, updated_at timestamptz, PRIMARY KEY (day))")
SECONDS_DDL = ('CREATE TABLE IF NOT EXISTS "serving"."futures_paper_seconds" '
               "(day date, payload jsonb, updated_at timestamptz, PRIMARY KEY (day))")
QUOTES_DDL = ('CREATE TABLE IF NOT EXISTS "serving"."futures_quotes" '
              "(day date, product text, source text, status text, payload jsonb, updated_at timestamptz, "
              "PRIMARY KEY (day, product))")
QUOTES_SECONDS_DDL = ('CREATE TABLE IF NOT EXISTS "serving"."futures_quotes_seconds" '
                      "(day date, product text, payload jsonb, updated_at timestamptz, PRIMARY KEY (day, product))")
QUOTE_PRODUCTS = {"TXF": "TX", "TMF": "TMF"}  # 網站行情：富邦代號 → 期交所代號（大台、微台）
# 模擬單成本（每點幾元、每邊手續費）。小台是原研究的數字；大台、微台照合約規格，手續費是假設（使用者 2026-10-27「也要有模擬單」）
PAPER_COST = {"MTX": (B.POINT_VALUE, B.COMMISSION), "TX": (200, 60.0), "TMF": (10, 15.0)}
PRODUCT_NAME = {"MTX": "小台", "TX": "大台", "TMF": "微台"}
FIELDS = ["day", "sleeve", "side", "signal_time", "entry_time", "entry_price", "exit_time", "exit_price",
          "exit_reason", "points", "net_pnl", "source"]

# 盤口與大單（T-455，網站顯示用、不影響模擬單；使用者 2026-10-28「做成可切換的」：網站可切 5／10／20／30 口）
BIG_MIN_LOTS = 5       # 一筆幾口以上記成大單；網站再照選的門檻篩
BOOK_LEVELS = 5


def third_wednesday(y: int, m: int) -> date:
    first = date(y, m, 1)
    return first + timedelta(days=(2 - first.weekday()) % 7 + 14)


def add_month(y: int, m: int, k: int):
    n = y * 12 + (m - 1) + k
    return n // 12, n % 12 + 1


def contracts_for(day: date):
    """(近月, 次月) 的 yyyymm；結算日當天近月 13:30 就收，改用次月當主力。"""
    y, m = day.year, day.month
    if day >= third_wednesday(y, m):
        y, m = add_month(y, m, 1)
    ny, nm = add_month(y, m, 1)
    return f"{y}{m:02d}", f"{ny}{nm:02d}"


def symbol(product: str, yyyymm: str) -> str:
    return f"{product}{MONTH_CODE[int(yyyymm[4:]) - 1]}{yyyymm[3]}"


def clock(day: date, hhmm: str) -> int:
    return int(datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=M.TPE).timestamp() * M.US)


def hms(t_us: int) -> str:
    return M.us_to_dt(t_us).strftime("%H:%M:%S.%f")[:-3]


def encode_seconds(day: date, secs: dict, what: str) -> dict:
    """秒級復盤：每一秒（有成交的那幾秒）最後成交價與 VWAP；三個陣列都是差分整數（VWAP ×10），省空間。"""
    keys = sorted(secs)
    price = [secs[k][0] for k in keys]
    if any(x != int(x) for x in price):
        raise ValueError(f"{day} {what}成交價有小數，秒級資料的整數編碼不適用")
    vw = [round(secs[k][1] * 10) for k in keys]

    def delta(xs):
        return [b - a for a, b in zip([0, *xs[:-1]], xs)]

    return {"version": 1, "day": str(day), "t0": B.SESSION_OPEN + ":00", "s": delta(keys),
            "p": delta([int(x) for x in price]), "w": delta(vw)}


def net_pnl(side: int, ep: float, xp: float, point_value: float, commission: float) -> float:
    return round(side * (xp - ep) * point_value - 2 * commission - B.TAX * (ep + xp) * point_value, 1)


MS_SLEEVE = "MS_LONG"
MS_COMMIT = "1dea51b"  # seromitama/Momentum_Scalping 用的版本（studies/ms_tmf 同一版）
MS_SETUP_TEXT = {"MOMENTUM/INITIAL_BREAKOUT": "動能・初次突破", "MOMENTUM/FIRST_PULLBACK_REBREAK": "動能・回檔再突破",
                 "TREND/TREND_INITIAL_ENTRY": "趨勢・初次進場", "TREND/TREND_RESUME": "趨勢・恢復",
                 "MOMENTUM/MOMENTUM_CONTINUATION": "動能・延續", "MOMENTUM/PERSISTENT_MOMENTUM_START": "動能・持續啟動"}
MS_EXIT_TEXT = {"EMA_TREND_BREAK": "EMA 死叉", "HIGHER_LOW_BREAK": "跌破前低", "MOMENTUM_STRUCTURE_BREAK_FULL": "動能結構破壞",
                "MOMENTUM_DECAY_PARTIAL": "動能衰退減碼", "EMERGENCY_STOP": "2.5% 緊急停損", "TIME_EXIT": "13:00 清倉",
                "MARKET_TREND_INVALIDATED": "大盤轉弱", "RS_FAILURE": "強度轉弱", "UNKNOWN_STRATEGY_STOP": "停損",
                "EOD_FORCED_CLOSE": "收盤前"}
_MS = {}  # 原版模組只載入一次：{"repo", "mods", "params"}


def ms_engine_parts(repo: Path):
    """載入原版（預設參數）；三個商品共用（原版的快取以商品代號分開，而且每次都核對資料）。"""
    if _MS.get("repo") != str(repo):
        if not (repo / "intraday_v2_1.py").exists():
            raise SystemExit(f"--ms-repo {repo} 底下沒有原版程式（git clone https://github.com/seromitama/Momentum_Scalping）")
        mods = M.load_modules(str(repo))
        _MS.update(repo=str(repo), mods=mods, params=M.load_parameters(mods, None, []))
    return _MS["mods"], _MS["params"]


def ms_config(product: str):
    pv, fee = PAPER_COST[product]
    return F.Config(product=product, point_value=pv, contracts=1, commission=fee, tax_rate=B.TAX, slip_ticks=1, tick=1.0,
                    latency_us=B.LATENCY_US, eval_interval_us=1_000_000, refresh_us=30 * M.US)


def iso_hms(iso: str) -> str:
    return M.us_to_dt(M.parse_time_us(iso)).strftime("%H:%M:%S.%f")[:-3]


class Netizen:
    """網友原版 Momentum_Scalping（studies/ms_tmf）只做多 1 口的模擬單：futures_replay.DayEngine 一筆一筆跑，
    和研究用的逐筆重播是同一份程式（2026-10-28 用 59 天逐筆比對過，一筆不差）。大盤濾網全開、「相對大盤強度」視為通過。"""

    def __init__(self, live: "LiveCombo", repo: Path):
        self.live = live
        mods, params = ms_engine_parts(repo)
        self.cfg = ms_config(live.product)
        self.engine = F.DayEngine(mods, params, str(live.day), live.contract, {}, self.cfg, self.on_event)
        self.serial = 0
        self.events, self.trades = [], []
        self.open = None         # 持倉中：{"entry_time", "entry_price", "stop", "signal_time", "setup"}
        self.signal = None       # 最近一次送單的訊號（等成交）
        self.closed = False

    def note(self, t_us: int, kind: str, text: str) -> None:
        self.events.append({"time": hms(t_us)[:8], "kind": kind, "sleeve": MS_SLEEVE, "text": text})
        self.live.log(f"[{hms(t_us)[:8]}] 網友原版：{text}")

    def on_tick(self, t_us: int, price: float, size: float) -> None:
        if self.closed:
            return
        self.engine.on_tick(t_us, self.serial, price, size)
        self.serial += 1

    def close(self) -> None:
        """收盤：還持有就用最後一筆了結（同 simulate_day；原版 13:00 就清倉，通常用不到）。"""
        if not self.closed:
            self.engine.close()
            self.closed = True

    def on_event(self, kind: str, t_us: int, info: dict) -> None:
        if kind == "signal":
            setup = MS_SETUP_TEXT.get(f"{info['strategy']}/{info['setup']}", f"{info['strategy']}/{info['setup']}")
            self.signal = {"time": t_us, "setup": setup}
            self.note(t_us, "signal", f"{setup}訊號，價 {info['price']:.0f}、結構停損 {info['stop']:.0f} → 模擬買進 1 口")
        elif kind == "fill":
            sig = self.signal or {"time": t_us, "setup": ""}
            self.open = {"entry_time": t_us, "entry_price": info["price"], "stop": info["stop"],
                         "signal_time": sig["time"], "setup": sig["setup"]}
            self.note(t_us, "fill", f"模擬買進 {info['price']:.0f}，停損 {info['stop']:.0f}")
        elif kind == "exit_signal":
            self.note(t_us, "signal", f"{MS_EXIT_TEXT.get(info['reason'], info['reason'])}（價 {info['price']:.0f}）→ 賣出")
        elif kind == "exit_fill" and info["left"] > 0:
            self.note(t_us, "info", f"減碼 {info['qty']} 口 {info['price']:.0f}，還有 {info['left']} 口")
        elif kind == "closed":
            t = info
            legs = json.loads(t["exit_legs"])
            reason = t["final_exit_reason"]
            row = {"day": str(self.live.day), "sleeve": MS_SLEEVE, "side": 1,
                   "signal_time": hms(self.open["signal_time"]) if self.open else iso_hms(t["signal_time"]),
                   "entry_time": iso_hms(t["entry_time"]), "entry_price": t["entry_price"],
                   "exit_time": iso_hms(t["exit_time"]), "exit_price": legs[-1]["price"], "exit_reason": reason,
                   "points": t["points"], "net_pnl": t["net_pnl"], "source": "live", "stop_price": t["stop_price"],
                   "setup": self.open["setup"] if self.open else ""}
            self.trades.append(row)
            self.open = None
            self.note(t_us, "exit", f"模擬賣出 {row['exit_price']:.0f}（{MS_EXIT_TEXT.get(reason, reason)}），這筆淨 {t['net_pnl']:+,.0f} 元")

    def mark(self, last):
        realized = round(sum(t["net_pnl"] for t in self.trades), 1)
        if self.open is None:
            return {"position": 0, "entry_price": None, "realized": realized, "open_pnl": None}
        entry = self.open["entry_price"]
        open_pnl = None if last is None else net_pnl(1, entry, last - B.SLIP, self.cfg.point_value, self.cfg.commission)
        return {"position": 1, "entry_price": entry, "realized": realized, "open_pnl": open_pnl}

    def snapshot(self, last) -> dict:
        st = self.mark(last)
        return {"version": 1, "name": "網友原版", "commit": MS_COMMIT, "sleeves": {MS_SLEEVE: st},
                "open": None if self.open is None else {"entry_time": hms(self.open["entry_time"])[:8],
                                                         "entry_price": self.open["entry_price"], "stop": self.open["stop"],
                                                         "setup": self.open["setup"]},
                "trades": self.trades, "events": self.events, "signals": sum(v for k, v in self.engine.signals.items() if k.endswith(":ACCEPTED")),
                "total": round(st["realized"] + (st["open_pnl"] or 0), 1), "official": None, "official_match": None}


def ms_official(day: date, today: dict, product: str) -> list:
    """收盤重算：同一個 DayEngine 吃期交所格式的逐筆（只到秒）跑一遍；和盤中比對用。"""
    mods, params = ms_engine_parts(Path(_MS["repo"]))
    res = F.simulate_day(mods, params, str(day), today, {}, ms_config(product))
    return [{"day": str(day), "sleeve": MS_SLEEVE, "side": 1, "signal_time": iso_hms(t["signal_time"]),
             "entry_time": iso_hms(t["entry_time"]), "entry_price": t["entry_price"], "exit_time": iso_hms(t["exit_time"]),
             "exit_price": json.loads(t["exit_legs"])[-1]["price"], "exit_reason": t["final_exit_reason"], "points": t["points"],
             "net_pnl": t["net_pnl"], "source": "official", "stop_price": t["stop_price"]} for t in res["trades"]]


def ms_attach_official(snap: dict, rows: list) -> None:
    ms = snap.get("ms")
    if ms is None:
        return
    key = lambda r: (float(r["entry_price"]), float(r["exit_price"]), r["exit_reason"])  # noqa: E731
    ms["official"] = rows
    ms["official_match"] = sorted(map(key, ms["trades"])) == sorted(map(key, rows))


def ms_summary(snap: dict) -> list:
    ms = snap.get("ms")
    if ms is None:
        return []
    lines = ["## 網友原版（只做多 1 口）", ""]
    rows = [("盤中即時", r) for r in ms["trades"]] + [("收盤重算", r) for r in ms.get("official") or []]
    if rows:
        lines += ["| 來源 | 進場 | 出場 | 原因 | 淨損益 |", "|---|---|---|---|---|"]
        lines += [f"| {src} | {r['entry_time']} {r['entry_price']:.0f} | {r['exit_time']} {r['exit_price']:.0f} | "
                  f"{MS_EXIT_TEXT.get(r['exit_reason'], r['exit_reason'])} | {r['net_pnl']:+,.0f} |" for src, r in rows]
        lines.append("")
    lines.append(f"合計：盤中即時 {sum(r['net_pnl'] for r in ms['trades']):+,.0f} 元；"
                 f"收盤重算 {sum(r['net_pnl'] for r in ms.get('official') or []):+,.0f} 元"
                 + ("" if ms.get("official_match") is None else ("（一致）" if ms["official_match"] else "（不一致，以收盤重算為準）")))
    return lines + [""]


def _px(v):
    v = float(v)
    return int(v) if v.is_integer() else v


class Flow:
    """盤口與大單（T-455；網站「盤口與大單」，只顯示、不影響模擬單）。

    主動買賣：成交價 ≥ 成交當下的賣價算主動買、≤ 買價算主動賣；中間價或沒有買賣價的不算方向。
    開盤第一筆（08:45:00 集合競價）和 13:45 那一筆（收盤）沒有主動方向，也不算大單。
    期交所逐筆沒有買賣價也沒有五檔，重算的日子 snapshot 回 None。"""

    def __init__(self, day: date):
        self.lo, self.close = clock(day, "08:45"), clock(day, "13:45")
        self.minutes = {}        # "HH:MM" -> [主動買口數, 主動賣口數, 其他口數]
        self.big = []            # [HH:MM:SS, 價, 口, +1 主動買／−1 主動賣／0 不明]
        self.book = None         # 最新五檔 {"time", "bids": [[價, 口]], "asks": [[價, 口]]}
        self.books = {}          # "HH:MM" -> [bids, asks]：那一分鐘最後一份五檔（復盤用）
        self.first = True
        self.quoted = False

    def on_trade(self, t_us: int, price: float, size: float, bid=None, ask=None) -> None:
        if not self.lo <= t_us <= self.close + 999_999:
            return
        auction = (self.first and t_us < self.lo + M.US) or t_us >= self.close
        self.first = False
        if bid is not None or ask is not None:
            self.quoted = True
        side = 0
        if not auction and ask is not None and price >= float(ask):
            side = 1
        elif not auction and bid is not None and price <= float(bid):
            side = -1
        row = self.minutes.setdefault(M.us_to_dt(t_us).strftime("%H:%M"), [0, 0, 0])
        row[0 if side > 0 else 1 if side < 0 else 2] += int(size)
        if not auction and size >= BIG_MIN_LOTS:
            self.big.append([hms(t_us)[:8], _px(price), int(size), side])

    def on_book(self, t_us: int, bids, asks) -> None:
        if not self.lo <= t_us <= self.close + 999_999:
            return
        levels = lambda xs: [[_px(x["price"]), int(x["size"])] for x in xs if x.get("price") is not None][:BOOK_LEVELS]  # noqa: E731
        b, a = levels(bids), levels(asks)
        self.book = {"time": hms(t_us)[:8], "bids": b, "asks": a}
        self.books[M.us_to_dt(t_us).strftime("%H:%M")] = [b, a]

    def snapshot(self):
        if not self.quoted and self.book is None:
            return None
        mins = sorted(self.minutes.items())
        return {"version": 1, "big_min": BIG_MIN_LOTS, "minutes": [[k, *v] for k, v in mins],
                "buy": sum(v[0] for _, v in mins), "sell": sum(v[1] for _, v in mins), "big": self.big,
                "book": self.book, "books": [[k, *v] for k, v in sorted(self.books.items())]}


class LiveCombo:
    """單執行緒、只看已到的逐筆：ORB 只做多 + NOISE 只放空，兩個子帳戶各 1 口；另外跑網友原版（Netizen）。"""

    def __init__(self, day: date, contract: str, prev_close: float, history, log, product: str = PRODUCT_TAIFEX):
        self.day, self.contract, self.prev_close, self.log = day, contract, prev_close, log
        self.product = product
        self.point_value, self.commission = PAPER_COST[product]
        self.t, self.p, self.vwap = [], [], []
        self.pv = self.vol = 0.0
        self.sigma = {m: statistics.fmean(abs(h.p[h.last_before(h.at(m))] / h.open - 1) for h in history)
                      for m in B.NOISE_CHECKS}
        self.orb_at = clock(day, B.SESSION_OPEN) + B.ORB_MINUTES * 60 * M.US
        self.eod = clock(day, B.EOD)
        self.checks = [clock(day, B.SESSION_OPEN) + m * 60 * M.US for m in B.NOISE_CHECKS]
        self.pending = []        # [(due_us, sleeve, side, kind, signal_t)]
        self.orb = None          # {"side", "stop", "target", "entry": (t, p), "entry_i", "signal_t"}
        self.orb_done = False
        self.noise_pos, self.noise_entry, self.noise_signal = 0, None, None
        self.next_check = 0
        self.eod_done = False
        self.trades = []
        self.events = []         # 網站時間軸：[{"time", "kind", "sleeve", "text"}]，time 是盤中時間
        self.bars = {}           # "HH:MM" -> [開, 高, 低, 收, 那分鐘結束時的 VWAP]
        self.secs = {}           # 08:45:00 起第幾秒 -> [那一秒最後成交價, 那一秒結束時的 VWAP]（秒級復盤）
        self.t0 = clock(day, B.SESSION_OPEN)
        self.noise_seen = []     # 已經檢查過的噪音區時點
        self.orb_box = None      # 開盤 5 分鐘：{"open", "close", "high", "low", "side"}
        self.orb_state = "waiting"
        self.disabled = None     # 開盤沒錄到 → 原因；之後只錄資料、不做任何決策
        self.flow = Flow(day)
        self.ms = Netizen(self, Path(_MS["repo"])) if _MS.get("repo") else None  # 網友原版；main() 有載入原版才跑

    def disable(self, t_us: int, reason: str) -> None:
        if self.disabled is None:
            self.disabled = reason
            self.note(t_us, "info", None, f"{reason}：今天只錄資料、不做模擬單")

    def note(self, t_us: int, kind: str, sleeve, text: str) -> None:
        self.events.append({"time": hms(t_us)[:8], "kind": kind, "sleeve": sleeve, "text": text})
        self.log(f"[{hms(t_us)[:8]}] {text}")

    # ── 逐筆 ──
    def on_tick(self, t_us: int, price: float, size: float, bid=None, ask=None) -> None:
        if not clock(self.day, "08:45") <= t_us <= clock(self.day, "13:45") + 999_999:
            return
        self.flow.on_trade(t_us, price, size, bid, ask)
        self.t.append(t_us)
        self.p.append(price)
        self.pv += price * size
        self.vol += size
        self.vwap.append(self.pv / self.vol)
        i = len(self.t) - 1
        self.secs[(t_us - self.t0) // M.US] = [price, self.vwap[-1]]
        key = M.us_to_dt(t_us).strftime("%H:%M")
        bar = self.bars.get(key)
        if bar is None:
            self.bars[key] = [price, price, price, price, self.vwap[-1]]
        else:
            bar[1], bar[2], bar[3], bar[4] = max(bar[1], price), min(bar[2], price), price, self.vwap[-1]
        self.decide(t_us, wall=False)
        self.fill_pending(i)
        self.watch_orb(i)
        if self.ms is not None and self.disabled is None:
            self.ms.on_tick(t_us, price, size)

    def last_before(self, t_us: int) -> int:
        i = bisect.bisect_left(self.t, t_us) - 1
        if i < 0:
            raise ValueError(f"{self.day} {hms(t_us)} 之前沒有成交")
        return i

    # ── 決策：時點 T 只看 T 之前的成交；收到 T 之後的第一筆（或等過寬限）才決定 ──
    def decide(self, now_us: int, wall: bool) -> None:
        if self.disabled is not None:
            return
        ready = (lambda T: now_us >= T + DECISION_GRACE_S * M.US) if wall else (lambda T: now_us >= T)
        if not self.orb_done and self.t and ready(self.orb_at):
            self.orb_done = True
            seg = self.p[:self.last_before(self.orb_at) + 1]
            side = 1 if seg[-1] > seg[0] else -1 if seg[-1] < seg[0] else 0
            self.orb_box = {"open": seg[0], "close": seg[-1], "high": max(seg), "low": min(seg), "side": side}
            if seg[-1] > seg[0]:
                self.pending.append((self.orb_at + B.LATENCY_US, "ORB_LONG", 1, "entry", self.orb_at))
                self.orb = {"side": 1, "stop": min(seg), "signal_t": self.orb_at}
                self.orb_state = "pending"
                self.note(self.orb_at, "signal", "ORB_LONG",
                          f"開盤 5 分鐘收紅（{seg[0]:.0f} → {seg[-1]:.0f}）→ 模擬買進 1 口，停損 {min(seg):.0f}")
            else:
                self.orb_state = "skipped"
                self.note(self.orb_at, "info", "ORB_LONG",
                          f"開盤 5 分鐘{'收黑' if seg[-1] < seg[0] else '平盤'}（{seg[0]:.0f} → {seg[-1]:.0f}）→ 只做多，今天不做")
        while self.next_check < len(self.checks) and ready(self.checks[self.next_check]):
            T = self.checks[self.next_check]
            self.next_check += 1
            if T >= self.eod or not self.t:
                continue
            i = self.last_before(T)
            m = B.NOISE_CHECKS[self.next_check - 1]
            lb = min(self.p[0], self.prev_close) * (1 - self.sigma[m])
            target = -1 if self.p[i] < min(lb, self.vwap[i]) else 0
            self.noise_seen.append({"time": hms(T)[:5], "lb": round(lb, 1), "price": self.p[i],
                                    "vwap": round(self.vwap[i], 1), "target": target})
            self.note(T, "check", "NOISE_SHORT", f"{hms(T)[:5]} 檢查：價 {self.p[i]:.0f}、下緣 {lb:.0f}、VWAP {self.vwap[i]:.1f} → "
                      f"{'持空' if target else '空手'}")
            if target != self.noise_pos:
                kind = "entry" if target else "exit"
                self.pending.append((T + B.LATENCY_US, "NOISE_SHORT", -1 if target else 1, kind, T))
                self.noise_pos = target
        if not self.eod_done and ready(self.eod):
            self.eod_done = True
            if self.orb is not None and "entry" in self.orb and "exit" not in self.orb and "trigger" not in self.orb:
                self.pending.append((self.eod + B.LATENCY_US, "ORB_LONG", -1, "EOD", self.eod))
                self.note(self.eod, "signal", "ORB_LONG", "收盤前平倉（13:44）")
            if self.noise_pos:
                self.pending.append((self.eod + B.LATENCY_US, "NOISE_SHORT", 1, "EOD", self.eod))
                self.noise_pos = 0
                self.note(self.eod, "signal", "NOISE_SHORT", "收盤前回補（13:44）")
        if self.ms is not None and ready(clock(self.day, "13:45") + M.US):
            self.ms.close()

    def fill_pending(self, i: int) -> None:
        keep = []
        for due, sleeve, side, kind, signal_t in self.pending:
            if self.t[i] < due:
                keep.append((due, sleeve, side, kind, signal_t))
                continue
            price = self.p[i] + side * B.SLIP
            if sleeve == "ORB_LONG" and kind == "entry":
                o = self.orb
                o["entry"], o["entry_i"] = (self.t[i], price), i
                risk = price - o["stop"]
                if risk <= 0:
                    self.note(self.t[i], "info", "ORB_LONG", f"進場價 {price:.0f} 已在停損 {o['stop']:.0f} 之下 → 取消（不算交易）")
                    self.orb = None
                    self.orb_state = "cancelled"
                    continue
                o["target"] = price + B.ORB_TARGET_R * risk
                self.orb_state = "open"
                self.note(self.t[i], "fill", "ORB_LONG", f"模擬買進 {price:.0f}，停損 {o['stop']:.0f}、停利 {o['target']:.0f}")
            elif sleeve == "ORB_LONG":
                self.book("ORB_LONG", 1, self.orb["signal_t"], self.orb["entry"], (self.t[i], price), kind)
                self.orb["exit"] = True
                self.orb_state = "closed"
            elif kind == "entry":
                self.noise_entry, self.noise_signal = (self.t[i], price), signal_t
                self.note(self.t[i], "fill", "NOISE_SHORT", f"模擬放空 {price:.0f}")
            else:
                self.book("NOISE_SHORT", -1, self.noise_signal, self.noise_entry, (self.t[i], price),
                          "EXIT" if kind == "exit" else kind)
                self.noise_entry = None
        self.pending = keep

    def watch_orb(self, i: int) -> None:
        o = self.orb
        if not o or "target" not in o or "exit" in o or "trigger" in o or i <= o["entry_i"] or self.t[i] >= self.eod:
            return
        reason = "STOP" if self.p[i] <= o["stop"] else "TARGET" if self.p[i] >= o["target"] else None
        if reason:
            o["trigger"] = True
            self.pending.append((self.t[i] + B.LATENCY_US, "ORB_LONG", -1, reason, self.t[i]))
            self.note(self.t[i], "signal", "ORB_LONG", f"{'碰到停損' if reason == 'STOP' else '碰到停利'} {self.p[i]:.0f}")

    def book(self, sleeve, side, signal_t, entry, exit_, reason) -> None:
        (et, ep), (xt, xp) = entry, exit_
        points = side * (xp - ep)
        net = points * self.point_value - 2 * self.commission - B.TAX * (ep + xp) * self.point_value
        row = {"day": str(self.day), "sleeve": sleeve, "side": side, "signal_time": hms(signal_t), "entry_time": hms(et),
               "entry_price": ep, "exit_time": hms(xt), "exit_price": xp, "exit_reason": reason,
               "points": round(points, 2), "net_pnl": round(net, 1), "source": "live"}
        self.trades.append(row)
        word = "賣出" if side > 0 else "回補"
        self.note(xt, "exit", sleeve, f"模擬{word} {xp:.0f}（{REASON_TEXT.get(reason, reason)}），這筆淨 {net:+,.0f} 元")

    def seconds_payload(self) -> dict:
        out = encode_seconds(self.day, self.secs, PRODUCT_NAME[self.product])
        out["product"] = self.product
        return out

    # ── 網站狀態 ──
    def mark(self, sleeve: str, last):
        """子帳戶現況：部位、已實現、現在平倉的話（含滑價與成本）。"""
        realized = round(sum(r["net_pnl"] for r in self.trades if r["sleeve"] == sleeve), 1)
        side, entry = 0, None
        if sleeve == "ORB_LONG" and self.orb and "target" in self.orb and "exit" not in self.orb:
            side, entry = 1, self.orb["entry"][1]
        if sleeve == "NOISE_SHORT" and self.noise_entry is not None:
            side, entry = -1, self.noise_entry[1]
        open_pnl = None
        if side and last is not None:
            xp = last - side * B.SLIP
            open_pnl = round(side * (xp - entry) * self.point_value - 2 * self.commission - B.TAX * (entry + xp) * self.point_value, 1)
        return {"position": side, "entry_price": entry, "realized": realized, "open_pnl": open_pnl}

    def snapshot(self, status: str, source: str, symbol_: str, updated_at: datetime):
        last = self.p[-1] if self.p else None
        sleeves = {s: self.mark(s, last) for s in ("ORB_LONG", "NOISE_SHORT")}
        lb_plan = []
        if self.p:
            base = min(self.p[0], self.prev_close)
            lb_plan = [[hms(T)[:5], round(base * (1 - self.sigma[m]), 1)]
                       for T, m in zip(self.checks, B.NOISE_CHECKS) if T < self.eod]
        orb = {"state": self.orb_state, "box": self.orb_box, "stop": None, "target": None, "entry_price": None,
               "entry_time": None}
        if self.orb:
            orb.update(stop=self.orb.get("stop"), target=self.orb.get("target"))
            if "entry" in self.orb:
                orb.update(entry_price=self.orb["entry"][1], entry_time=hms(self.orb["entry"][0])[:8])
        total = sum(v["realized"] + (v["open_pnl"] or 0) for v in sleeves.values())
        return {
            "version": 1, "day": str(self.day), "source": source, "status": status, "contract": self.contract,
            "symbol": symbol_, "updated_at": updated_at.isoformat(timespec="seconds"),
            "last_tick": hms(self.t[-1])[:8] if self.t else None, "last_price": last,
            "prev_close": self.prev_close, "open": self.p[0] if self.p else None,
            "bars": [[k, *(round(x, 1) for x in v)] for k, v in sorted(self.bars.items())],
            "orb": orb, "noise_lb": lb_plan, "noise": self.noise_seen, "sleeves": sleeves,
            "events": self.events, "trades": self.trades, "total": round(total, 1),
            "official": None, "official_match": None, "note": self.disabled,
            "product": self.product, "name": PRODUCT_NAME[self.product], "volume": int(self.vol),
            "point_value": self.point_value, "commission": self.commission,
            "flow": self.flow.snapshot(),
            "ms": None if self.ms is None else self.ms.snapshot(last),
        }

    def on_book(self, t_us: int, bids, asks) -> None:
        self.flow.on_book(t_us, bids, asks)

    def n_events(self) -> int:
        return len(self.events) + (len(self.ms.events) if self.ms is not None else 0)


class QuoteTracker:
    """大台、微台的網站行情：每分鐘 K（含 VWAP）、每秒最後成交價；不做模擬單。"""

    NAME = {"TX": "大台", "TMF": "微台"}

    def __init__(self, day: date, product: str, contract: str, prev_close):
        self.day, self.product, self.contract, self.prev_close = day, product, contract, prev_close
        self.t0 = clock(day, B.SESSION_OPEN)
        self.lo, self.hi = clock(day, "08:45"), clock(day, "13:45") + 999_999
        self.bars, self.secs = {}, {}
        self.pv = self.vol = 0.0
        self.first = self.last = self.last_t = None
        self.flow = Flow(day)

    def on_book(self, t_us: int, bids, asks) -> None:
        self.flow.on_book(t_us, bids, asks)

    def on_tick(self, t_us: int, price: float, size: float, bid=None, ask=None) -> None:
        if not self.lo <= t_us <= self.hi:
            return
        self.flow.on_trade(t_us, price, size, bid, ask)
        self.pv += price * size
        self.vol += size
        vwap = self.pv / self.vol
        if self.first is None:
            self.first = price
        self.last, self.last_t = price, t_us
        self.secs[(t_us - self.t0) // M.US] = [price, vwap]
        key = M.us_to_dt(t_us).strftime("%H:%M")
        bar = self.bars.get(key)
        if bar is None:
            self.bars[key] = [price, price, price, price, vwap]
        else:
            bar[1], bar[2], bar[3], bar[4] = max(bar[1], price), min(bar[2], price), price, vwap

    def snapshot(self, status: str, source: str, symbol_: str, updated_at: datetime) -> dict:
        return {"version": 1, "day": str(self.day), "product": self.product, "name": self.NAME.get(self.product, self.product),
                "source": source, "status": status, "contract": self.contract, "symbol": symbol_,
                "updated_at": updated_at.isoformat(timespec="seconds"),
                "last_tick": hms(self.last_t)[:8] if self.last_t else None, "last_price": self.last,
                "prev_close": self.prev_close, "open": self.first, "volume": int(self.vol),
                "bars": [[k, *(round(x, 1) for x in v)] for k, v in sorted(self.bars.items())], "flow": self.flow.snapshot()}

    def seconds_payload(self) -> dict:
        out = encode_seconds(self.day, self.secs, self.NAME.get(self.product, self.product))
        out["product"] = self.product
        return out


def quote_prev_close(ticks_root: Path, code: str, day: date, contract: str):
    """期交所逐筆另存（taifex_ticks/<代號>）裡，今天之前最後一個交易日、同一個合約的一般盤收盤；沒有回 None。"""
    folder = ticks_root / code
    if not folder.exists():
        return None, None
    raw = F.read_taifex(folder, code)
    names = [d for d in sorted(raw) if d < str(day)]
    if not names:
        return None, None
    return raw[names[-1]]["closes"].get(contract), names[-1]


class Uploader:
    """把最新狀態寫進雲端 serving.futures_paper（背景執行緒、只留最新一份；失敗記錄後下次再試，不擋錄製）。"""

    def __init__(self, dsn: str, log):
        import threading

        import psycopg  # 只有上傳才需要

        self.psycopg, self.dsn, self.log = psycopg, dsn, log
        self.pending: dict = {}  # 鍵（"paper" 或 商品代號）→ (寫入函式, 最新一份)
        self.cv = threading.Condition()
        self.conn = None
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def push(self, payload: dict, key: str = "paper") -> None:
        writer = self.write if key == "paper" else self.write_quote
        with self.cv:
            self.pending[key] = (writer, payload)
            self.cv.notify()

    def connect(self) -> None:
        if self.conn is None or self.conn.closed:
            self.conn = self.psycopg.connect(self.dsn, prepare_threshold=None, connect_timeout=10)
            with self.conn.cursor() as cur:
                cur.execute('CREATE SCHEMA IF NOT EXISTS "serving"')
                cur.execute(TABLE_DDL)
                cur.execute(QUOTES_DDL)
            self.conn.commit()

    def write_quote(self, payload: dict) -> None:
        self.connect()
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        with self.conn.cursor() as cur:
            cur.execute(
                'INSERT INTO "serving"."futures_quotes" (day, product, source, status, payload, updated_at) '
                "VALUES (%s, %s, %s, %s, %s::jsonb, %s) ON CONFLICT (day, product) DO UPDATE SET source = EXCLUDED.source, "
                "status = EXCLUDED.status, payload = EXCLUDED.payload, updated_at = EXCLUDED.updated_at "
                "WHERE NOT (futures_quotes.source = 'fubon_live' AND EXCLUDED.source = 'taifex_replay')",
                (payload["day"], payload["product"], payload["source"], payload["status"], body, payload["updated_at"]))
        self.conn.commit()

    def flush_quote(self, payload: dict, seconds: dict) -> None:
        """大台／微台收盤定稿：同步寫一次（含秒級），失敗要讓呼叫端知道。"""
        self.write_quote(payload)
        body = json.dumps(seconds, ensure_ascii=False, allow_nan=False)
        with self.conn.cursor() as cur:
            cur.execute(QUOTES_SECONDS_DDL)
            cur.execute(
                'INSERT INTO "serving"."futures_quotes_seconds" (day, product, payload, updated_at) VALUES (%s, %s, %s::jsonb, %s) '
                "ON CONFLICT (day, product) DO UPDATE SET payload = EXCLUDED.payload, updated_at = EXCLUDED.updated_at",
                (seconds["day"], seconds["product"], body, payload["updated_at"]))
        self.conn.commit()

    def write(self, payload: dict) -> None:
        self.connect()
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        with self.conn.cursor() as cur:
            # 期交所重算的復盤不覆蓋富邦即時錄到的那一天
            cur.execute(
                'INSERT INTO "serving"."futures_paper" (day, source, status, payload, updated_at) '
                "VALUES (%s, %s, %s, %s::jsonb, %s) ON CONFLICT (day) DO UPDATE SET source = EXCLUDED.source, "
                "status = EXCLUDED.status, payload = EXCLUDED.payload, updated_at = EXCLUDED.updated_at "
                "WHERE NOT (futures_paper.source = 'fubon_live' AND EXCLUDED.source = 'taifex_replay')",
                (payload["day"], payload["source"], payload["status"], body, payload["updated_at"]))
        self.conn.commit()

    def run(self) -> None:
        while True:
            with self.cv:
                while not self.pending:
                    self.cv.wait()
                jobs, self.pending = list(self.pending.values()), {}
            for writer, payload in jobs:
                try:
                    writer(payload)
                except Exception as exc:  # noqa: BLE001  # 記錄後丟掉這份、等下一份；不讓上傳問題停掉盤中錄製
                    self.log(f"⚠ 寫雲端失敗：{exc}")
                    if self.conn is not None:
                        self.conn.close()
                        self.conn = None

    def flush(self, payload: dict, seconds: dict | None = None) -> None:
        """收盤定稿：同步寫一次（含秒級復盤資料），失敗要讓呼叫端知道。"""
        self.write(payload)
        if seconds is None:
            return
        body = json.dumps(seconds, ensure_ascii=False, allow_nan=False)
        with self.conn.cursor() as cur:
            cur.execute(SECONDS_DDL)
            cur.execute(
                'INSERT INTO "serving"."futures_paper_seconds" (day, payload, updated_at) VALUES (%s, %s::jsonb, %s) '
                "ON CONFLICT (day) DO UPDATE SET payload = EXCLUDED.payload, updated_at = EXCLUDED.updated_at",
                (seconds["day"], body, payload["updated_at"]))
        self.conn.commit()


def env_value(path: Path, key: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(f"{path} 沒有 {key}")


def backfill(futopt, symbol_: str, log):
    """富邦當天逐筆查詢（一般盤、不含試撮）：回傳 [(t_us, price, size, serial, bid, ask)]，依時間排序。失敗回 None。"""
    rows, offset = [], 0
    try:
        while True:
            page = futopt.intraday.trades(symbol=symbol_, offset=offset, limit=BACKFILL_LIMIT)
            data = (page or {}).get("data") or []
            for tr in data:
                if tr.get("isTrial"):
                    continue
                rows.append((int(tr["time"]), float(tr["price"]), float(tr["size"]), int(tr.get("serial") or 0),
                             tr.get("bid"), tr.get("ask")))
            if len(data) < BACKFILL_LIMIT:
                break
            offset += len(data)
            time.sleep(0.25)
    except Exception as exc:  # noqa: BLE001  # 補抓失敗 → 呼叫端照「開盤沒錄到」處理，不會用不完整的資料做決策
        log(f"⚠ 補抓當天逐筆失敗：{exc}")
        return None
    rows.sort(key=lambda r: (r[0], r[3]))
    log(f"補抓當天逐筆：{symbol_} {len(rows):,} 筆" + (f"（{hms(rows[0][0])}～{hms(rows[-1][0])}）" if rows else ""))
    return rows


def now_us_start() -> int:
    return int(time.time() * M.US)


def open_us_of(day: date) -> int:
    return clock(day, B.SESSION_OPEN)


def history_days(folder: Path, day: date, product: str = PRODUCT_TAIFEX, raw=None):
    """期交所逐筆：今天之前最後 14 個交易日（B.Day）與今天主力合約的昨收來源。raw 有給就不重讀。"""
    raw = raw if raw is not None else F.read_taifex(folder, product)
    names = [d for d in sorted(raw) if d < str(day)]
    if len(names) < B.NOISE_LOOKBACK:
        raise SystemExit(f"{folder} 在 {day} 之前只有 {len(names)} 天逐筆，噪音區需要 {B.NOISE_LOOKBACK} 天")
    days = [B.Day(n, raw[n], None) for n in names[-B.NOISE_LOOKBACK:]]
    return days, raw[names[-1]], names[-1]


def official(day: date, tick_csv, folder: Path, history_raw_last, contract: str, product: str = PRODUCT_TAIFEX,
             today=None, hist=None):
    """收盤後：用逐筆引擎重算（與 forward_paper 同一套函數）。大台、微台的損益照各自的合約成本重算（價格與出場原因不變）。"""
    if today is None:
        tmp = tick_csv.parent / "official_input"
        tmp.mkdir(exist_ok=True)
        shutil.copy(tick_csv, tmp / f"Daily_{day:%Y_%m_%d}.csv")
        today = F.read_taifex(tmp, product)[str(day)]
    prev = history_raw_last["closes"].get(today["contract"])
    d = B.Day(str(day), today, prev)
    if hist is None:
        hist, _, _ = history_days(folder, day, product)
    pv, fee = PAPER_COST[product]

    def net_of(t):
        if product == PRODUCT_TAIFEX:
            return t["net_pnl"]
        return round(t["points"] * pv - 2 * fee - B.TAX * (t["entry_price"] + t["exit_price"]) * pv, 1)

    rows = []
    for t in B.run_orb(d)[0]:
        if t["side"] > 0:
            rows.append(("ORB_LONG", t))
    for t in B.run_noise(d, hist)[0]:
        if t["side"] < 0:
            rows.append(("NOISE_SHORT", t))
    return [{"day": str(day), "sleeve": s, "side": t["side"], "signal_time": t["signal_time"][11:19],
             "entry_time": t["entry_time"][11:19], "entry_price": t["entry_price"], "exit_time": t["exit_time"][11:19],
             "exit_price": t["exit_price"], "exit_reason": t["exit_reason"], "points": t["points"],
             "net_pnl": net_of(t), "source": "official"} for s, t in rows], today["contract"]


def write_rows(path: Path, rows) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def finish(day: date, out: Path, live: LiveCombo, tick_csv: Path, folder: Path, last_raw, log, final=None) -> None:
    """final＝(uploader, source, symbol)：把定稿狀態（含收盤重算）寫進雲端。"""
    write_rows(out / "paper_live.csv", live.trades)
    rows, contract = official(day, tick_csv, folder, last_raw, live.contract)
    write_rows(out / "paper_official.csv", rows)
    lines = [f"# 富邦即時模擬單 {day}（{live.contract} 小台，只記帳、沒有送委託）", "",
             "| 來源 | 子帳戶 | 進場 | 出場 | 原因 | 淨損益 |", "|---|---|---|---|---|---|"]
    for r in live.trades + rows:
        lines.append(f"| {'盤中即時' if r['source'] == 'live' else '收盤重算'} | {r['sleeve']} | {r['entry_time']} {r['entry_price']:.0f} | "
                     f"{r['exit_time']} {r['exit_price']:.0f} | {r['exit_reason']} | {r['net_pnl']:+,.0f} |")
    total_live = sum(r["net_pnl"] for r in live.trades)
    total_off = sum(r["net_pnl"] for r in rows)
    lines += ["", f"合計：盤中即時 {total_live:+,.0f} 元；收盤重算 {total_off:+,.0f} 元（收盤重算的主力合約 {contract}）。",
              "兩者應該一致；不一致時以收盤重算為準，並查原因。", ""]
    ms_rows = None
    if live.ms is not None:
        live.ms.close()
        ms_rows = ms_official(day, F.read_taifex(tick_csv.parent / "official_input", PRODUCT_TAIFEX)[str(day)], PRODUCT_TAIFEX)
        M.write_csv(out / "ms_live.csv", live.ms.trades)
        M.write_csv(out / "ms_official.csv", ms_rows)
        probe = {"ms": live.ms.snapshot(None)}
        ms_attach_official(probe, ms_rows)
        lines += ms_summary(probe)
    (out / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")
    log("\n".join(lines))
    if final is not None:
        uploader, source, symbol_ = final
        snap = live.snapshot("closed", source, symbol_, datetime.now(M.TPE))
        key = lambda r: (r["sleeve"], float(r["entry_price"]), float(r["exit_price"]), r["exit_reason"])  # noqa: E731
        snap["official"] = rows
        snap["official_match"] = sorted(map(key, live.trades)) == sorted(map(key, rows))
        if ms_rows is not None:
            ms_attach_official(snap, ms_rows)
        seconds = live.seconds_payload()
        uploader.flush(snap, seconds)
        (out / "payload.json").write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        (out / "seconds.json").write_text(json.dumps(seconds), encoding="utf-8")
        log(f"定稿已寫進雲端（{'與收盤重算一致' if snap['official_match'] else '與收盤重算不一致，請查'}）")


def quote_day(day: date, fubon: str, code: str, raw, log=lambda m: None):
    """期交所逐筆重算大台／微台的一天：前面有 14 天就跑模擬單（含收盤重算），不夠就只有行情。回傳 (payload, 秒級)。"""
    info = raw[str(day)]
    names = [d for d in sorted(raw) if d < str(day)]
    prev = raw[names[-1]]["closes"].get(info["contract"]) if names else None
    when = datetime.fromisoformat(f"{day}T13:50:00").replace(tzinfo=M.TPE)
    sym = symbol(fubon, info["contract"])
    if len(names) < B.NOISE_LOOKBACK or prev is None:
        tracker = QuoteTracker(day, code, info["contract"], prev)
        for t_us, _, price, size in info["ticks"]:
            tracker.on_tick(t_us, price, size)
        return tracker.snapshot("closed", "taifex_replay", sym, when), tracker.seconds_payload()
    hist = [B.Day(n, raw[n], None) for n in names[-B.NOISE_LOOKBACK:]]
    live = LiveCombo(day, info["contract"], prev, hist, log, product=code)
    for t_us, _, price, size in info["ticks"]:
        live.on_tick(t_us, price, size)
    live.decide(clock(day, "13:50"), wall=True)
    snap = live.snapshot("closed", "taifex_replay", sym, when)
    rows, _ = official(day, None, None, raw[names[-1]], info["contract"], code, today=info, hist=hist)
    key = lambda r: (r["sleeve"], float(r["entry_price"]), float(r["exit_price"]), r["exit_reason"])  # noqa: E731
    snap["official"] = rows
    snap["official_match"] = sorted(map(key, live.trades)) == sorted(map(key, rows))
    if live.ms is not None:
        live.ms.close()
        snap["ms"] = live.ms.snapshot(None)
        ms_attach_official(snap, ms_official(day, info, code))
    return snap, live.seconds_payload()


def replay_quotes(args) -> int:
    """不連富邦：用期交所逐筆另存（taifex_ticks）重算大台、微台（行情＋模擬單）；--upload 寫進網站。
    --replay-quotes 可以給一天、逗號分隔的幾天，或 all（另存裡的每一天；每個商品只讀一次檔）。"""
    root = Path(args.history).parent / "taifex_ticks"
    uploader = Uploader(env_value(Path(args.env), DSN_ENV), print) if args.upload else None
    for fubon, code in QUOTE_PRODUCTS.items():
        raw = F.read_taifex(root / code, code)
        wanted = sorted(raw) if args.replay_quotes == "all" else args.replay_quotes.split(",")
        for name in wanted:
            if name not in raw:
                raise SystemExit(f"{root / code} 沒有 {name} 的一般盤逐筆")
            snap, secs = quote_day(date.fromisoformat(name), fubon, code, raw)
            if uploader is not None:
                uploader.flush_quote(snap, secs)
            paper = "trades" in snap
            tail = (f"模擬單 {len(snap['trades'])} 筆、合計 {snap['total']:+,.0f} 元、"
                    f"{'與收盤重算一致' if snap['official_match'] else '與收盤重算不一致'}；"
                    f"網友原版 {len((snap.get('ms') or {}).get('trades') or [])} 筆、合計 {(snap.get('ms') or {}).get('total', 0):+,.0f} 元") if paper else "只有行情（前面不足 14 天）"
            print(f"{name} {PRODUCT_NAME[code]} {snap['contract']}：{tail}" + ("，已寫進雲端" if uploader is not None else ""))
    return 0


def replay(args) -> int:
    """不連富邦：用期交所逐筆重播一天，檢查即時引擎和逐筆引擎是否一致。"""
    day = date.fromisoformat(args.replay_day)
    folder = Path(args.history) / "csv"
    raw = F.read_taifex(folder, PRODUCT_TAIFEX)
    info = raw[str(day)]
    hist, last_raw, _ = history_days(folder, day)
    out = Path(args.out) / f"replay_{day}"
    out.mkdir(parents=True, exist_ok=True)
    logs = []
    live = LiveCombo(day, info["contract"], last_raw["closes"][info["contract"]], hist, logs.append)
    for t_us, _, price, size in info["ticks"]:
        live.on_tick(t_us, price, size)
    live.decide(clock(day, "13:50"), wall=True)
    tick_csv = out / f"ticks_{day}.csv"
    src = next(folder.glob(f"Daily_{day:%Y_%m_%d}.csv"))
    shutil.copy(src, tick_csv)
    final = None
    if args.upload:
        final = (Uploader(env_value(Path(args.env), DSN_ENV), print), "taifex_replay", symbol("MXF", info["contract"]))
    finish(day, out, live, tick_csv, folder, last_raw, print, final)
    same = [(r["sleeve"], r["entry_price"], r["exit_price"], r["exit_reason"]) for r in live.trades]
    off = [(r["sleeve"], r["entry_price"], r["exit_price"], r["exit_reason"])
           for r in csv.DictReader((out / "paper_official.csv").open(encoding="utf-8"))]
    off = [(s, float(a), float(b), c) for s, a, b, c in off]
    print("一致" if sorted(same) == sorted(off) else f"不一致：即時 {same} vs 重算 {off}")
    return 0 if sorted(same) == sorted(off) else 1


def setup_quotes(day: date, near: str, ticks_root: Path, log):
    """大台、微台：期交所逐筆另存裡前面有 14 天就跑模擬單（LiveCombo），不夠就只有行情（QuoteTracker）。"""
    quotes, quote_raw = {}, {}
    for fubon, code in QUOTE_PRODUCTS.items():
        qs = symbol(fubon, near)
        raw_q = F.read_taifex(ticks_root / code, code) if (ticks_root / code).exists() else {}
        quote_raw[qs] = raw_q
        names_q = [d for d in sorted(raw_q) if d < str(day)]
        prev = raw_q[names_q[-1]]["closes"].get(near) if names_q else None
        if len(names_q) >= B.NOISE_LOOKBACK and prev is not None:
            hist_q = [B.Day(n, raw_q[n], None) for n in names_q[-B.NOISE_LOOKBACK:]]
            quotes[qs] = LiveCombo(day, near, prev, hist_q, log, product=code)
            kind = "行情＋模擬單"
        else:
            quotes[qs] = QuoteTracker(day, code, near, prev)
            kind = "只有行情（期交所逐筆不足 14 天）"
        log(f"{PRODUCT_NAME[code]} {qs}：{kind}，昨收 {prev}（{names_q[-1] if names_q else '—'}）")
    return quotes, quote_raw


def finish_quotes(day: date, out: Path, quotes, quote_raw, tick_csv: Path, near: str, uploader, log) -> None:
    """大台、微台收盤定稿：有模擬單的用逐筆引擎重算對照；有上傳就同步寫一次（含秒級）。"""
    for qs, tracker in quotes.items():
        snap = tracker.snapshot("closed", "fubon_live", qs, datetime.now(M.TPE))
        has_ticks = bool(tracker.t) if isinstance(tracker, LiveCombo) else tracker.last is not None
        if isinstance(tracker, LiveCombo) and has_ticks:
            names_q = [d for d in sorted(quote_raw[qs]) if d < str(day)]
            rows, _ = official(day, tick_csv, None, quote_raw[qs][names_q[-1]], near, tracker.product,
                               hist=[B.Day(n, quote_raw[qs][n], None) for n in names_q[-B.NOISE_LOOKBACK:]])
            key = lambda r: (r["sleeve"], float(r["entry_price"]), float(r["exit_price"]), r["exit_reason"])  # noqa: E731
            snap["official"] = rows
            snap["official_match"] = sorted(map(key, tracker.trades)) == sorted(map(key, rows))
            if tracker.ms is not None:
                tracker.ms.close()
                snap["ms"] = tracker.ms.snapshot(None)
                today_q = F.read_taifex(tick_csv.parent / "official_input", tracker.product)[str(day)]
                ms_attach_official(snap, ms_official(day, today_q, tracker.product))
                log(chr(10).join(ms_summary(snap)))
        (out / f"quotes_{tracker.product}.json").write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        if uploader is not None and has_ticks:
            uploader.flush_quote(snap, tracker.seconds_payload())
            log(f"{PRODUCT_NAME[tracker.product]}定稿已寫進雲端")


def flow_summary(name: str, flow) -> str:
    if flow is None:
        return f"{name}：沒有盤口資料"
    big = flow["big"]
    counts = "、".join(f"≥{k} 口 {sum(b[2] >= k for b in big)} 筆" for k in (5, 10, 20, 30))
    return (f"{name}：主動買 {flow['buy']:,} 口、主動賣 {flow['sell']:,} 口（差 {flow['buy'] - flow['sell']:+,}）；"
            f"大單 {counts}；五檔 {len(flow['books'])} 分鐘、最新 {flow['book']['time'] if flow['book'] else '—'}")


def replay_raw(args) -> int:
    """收盤後用富邦錄到的原始訊息把一天重建一次（含盤口與大單，T-455）；--upload 覆蓋網站那天的小台、大台、微台。
    原始訊息要從開盤前就開始錄（盤中才開錄、靠補抓的日子，原始訊息沒有開盤那段，不能重建）。
    結果寫在 <out>/<日>/rebuild/，並和盤中即時錄到的模擬單（<out>/<日>/paper_live.csv）對照。"""
    day = date.fromisoformat(args.replay_raw)
    src = Path(args.out) / str(day)
    raw_path = src / f"raw_{day:%Y%m%d}.jsonl"
    gz = Path(str(raw_path) + ".gz")
    if not raw_path.exists() and not gz.exists():
        raise SystemExit(f"{src} 沒有原始訊息（raw_{day:%Y%m%d}.jsonl 或 .gz）")
    out = src / "rebuild"
    out.mkdir(exist_ok=True)
    logs = []
    history = Path(args.history) / "csv"
    hist, last_raw, last_day = history_days(history, day)
    near, _ = contracts_for(day)
    prev_close = last_raw["closes"].get(near)
    if prev_close is None:
        raise SystemExit(f"{last_day} 的逐筆裡沒有 {near} 的收盤價")
    main_sym = symbol("MXF", near)
    live = LiveCombo(day, near, prev_close, hist, logs.append)
    quotes, quote_raw = setup_quotes(day, near, Path(args.history).parent / "taifex_ticks", logs.append)
    last, bad = 0, None
    opener = (lambda: gzip.open(gz, "rt", encoding="utf-8")) if gz.exists() else (lambda: raw_path.open(encoding="utf-8"))
    with opener() as f:
        for line in f:
            if bad is not None:
                raise SystemExit(f"原始訊息中間有壞掉的一行：{bad[:80]}")
            try:
                body = json.loads(line)
            except json.JSONDecodeError:
                bad = line  # 只容許最後一行還沒寫完（錄製還在寫的檔案）
                continue
            data = body.get("data") or {}
            if body.get("event") != "data" or data.get("isTrial"):
                continue
            sym = data.get("symbol")
            target = live if sym == main_sym else quotes.get(sym)
            if target is None:
                continue
            t_us = int(data["time"])
            if body.get("channel") == "trades":
                for tr in data.get("trades") or []:
                    target.on_tick(t_us, float(tr["price"]), float(tr["size"]), tr.get("bid"), tr.get("ask"))
                last = max(last, t_us)
            elif body.get("channel") == "books":
                target.on_book(t_us, data.get("bids") or [], data.get("asks") or [])
    if bad is not None and gz.exists():
        raise SystemExit(f"壓縮好的原始訊息最後一行壞了：{bad[:80]}")
    open_us = clock(day, B.SESSION_OPEN)
    if not live.t or live.t[0] > open_us + OPEN_SLACK_S * M.US:
        raise SystemExit(f"{day} 原始訊息沒有錄到開盤（第一筆 {hms(live.t[0]) if live.t else '—'}），不能重建")
    complete = last >= clock(day, B.EOD)
    end = clock(day, "13:50") if complete else last
    for tracker in [live, *quotes.values()]:
        if isinstance(tracker, LiveCombo):
            tracker.decide(end, wall=complete)
    print(f"{day} 原始訊息到 {hms(last)}（{'已收盤' if complete else '還沒收盤，只檢查'}）")
    print(flow_summary("小台", live.flow.snapshot()))
    for tracker in quotes.values():
        print(flow_summary(PRODUCT_NAME[tracker.product], tracker.flow.snapshot()))
    if not complete:
        if args.upload:
            raise SystemExit("還沒收盤，不能上傳")
        return 0
    uploader = Uploader(env_value(Path(args.env), DSN_ENV), print) if args.upload else None
    finish(day, out, live, src / f"Daily_{day:%Y_%m_%d}_fubon.csv", history, last_raw, print,
           (uploader, "fubon_live", main_sym) if uploader is not None else None)
    finish_quotes(day, out, quotes, quote_raw, src / f"Daily_{day:%Y_%m_%d}_fubon.csv", near, uploader, print)
    recorded = src / "paper_live.csv"
    if recorded.exists():
        key = lambda r: (r["sleeve"], float(r["entry_price"]), float(r["exit_price"]), r["exit_reason"])  # noqa: E731
        same = sorted(map(key, csv.DictReader(recorded.open(encoding="utf-8")))) == sorted(map(key, live.trades))
        print("和盤中即時錄到的模擬單" + ("一致" if same else "不一致，請查（網站以這次重建為準）"))
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--env", help=".env 絕對路徑（即時模式必填）")
    p.add_argument("--out", required=True, help="資料根目錄（repo 外）；每天一個子資料夾")
    p.add_argument("--history", required=True, help="期交所逐筆工作資料夾（底下 csv/ 是 Daily_*.csv；前 14 天與昨收）")
    p.add_argument("--replay-day", help="檢查用：重播這天的期交所逐筆，不連富邦")
    p.add_argument("--replay-quotes", help="重算大台、微台（行情＋模擬單）：一天、逗號分隔的幾天或 all；配 --upload 放上網站")
    p.add_argument("--replay-raw", help="收盤後用富邦錄到的原始訊息重建這一天（含盤口與大單）；配 --upload 覆蓋網站那天")
    p.add_argument("--upload", action="store_true", help="狀態寫進雲端 serving.futures_paper（網站「小台模擬」）")
    p.add_argument("--ms-repo", default=str(HERE.parents[3] / "Momentum_Scalping"),
                   help="網友原版 repo（seromitama/Momentum_Scalping，commit 1dea51b）；三個商品都跑它的模擬單")
    p.add_argument("--no-ms", action="store_true", help="不跑網友原版")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 排程視窗是 cp950，印「⚠」會當掉
    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=True)
    os.chdir(root)  # 富邦 SDK 的 log/ 寫在這裡，不會進 repo
    if not args.no_ms:
        ms_engine_parts(Path(args.ms_repo))  # 載不到就在開盤前停下來，不要盤中才發現
    if args.replay_day:
        return replay(args)
    if args.replay_quotes:
        return replay_quotes(args)
    if args.replay_raw:
        return replay_raw(args)

    day = datetime.now(M.TPE).date()
    out = root / str(day)
    out.mkdir(exist_ok=True)
    logf = (out / "live.log").open("a", encoding="utf-8")

    def log(msg: str) -> None:
        line = f"{datetime.now(M.TPE):%H:%M:%S} {msg}"
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    near, nxt = contracts_for(day)
    import forward_paper as P  # 只在即時模式更新期交所近 30 日逐筆

    try:
        P.sync_ticks(Path(args.history))
    except SystemExit as exc:
        log(f"⚠ 期交所逐筆更新失敗（{exc}），用現有檔案；請確認下面的「昨收日期」是前一個交易日")
    import taifex_archive as A  # 大台、微台的期交所逐筆另存一份（期交所只留 30 個交易日）

    try:
        A.archive_all(Path(args.history), Path(args.history).parent / "taifex_ticks", log)
    except SystemExit as exc:
        log(f"⚠ 大台／微台期交所逐筆另存失敗：{exc}")
    history = Path(args.history) / "csv"
    hist, last_raw, last_day = history_days(history, day)
    prev_close = last_raw["closes"].get(near)
    if prev_close is None:
        raise SystemExit(f"{last_day} 的逐筆裡沒有 {near} 的收盤價，算不出噪音區")
    # 小台（模擬單用）、大台、微台：近月與次月逐筆；近月五檔
    symbols = {symbol(p, m): m for p in FUBON_PRODUCTS for m in (near, nxt)}
    near_syms = [symbol(p, near) for p in FUBON_PRODUCTS]
    main_sym = symbol("MXF", near)
    log(f"交易日 {day}；主力 {near}（{main_sym}），昨收 {prev_close:.0f}（{last_day}）；錄 {', '.join(symbols)}")

    while datetime.now(M.TPE) < datetime.fromisoformat(f"{day}T{START_AT}:00").replace(tzinfo=M.TPE):
        time.sleep(20)

    import fubon_check as C  # 延遲匯入：replay 不需要富邦 SDK

    sdk = None
    while sdk is None:
        try:
            sdk = C.connect(Path(args.env))
        except SystemExit as exc:
            log(f"登入失敗：{exc}")
            if datetime.now(M.TPE) >= datetime.fromisoformat(f"{day}T{LOGIN_UNTIL}:00").replace(tzinfo=M.TPE):
                log("到 09:30 還登不進去，今天放棄")
                return 1
            time.sleep(120)

    q: "queue.Queue[str]" = queue.Queue()
    state = {"connected": False}
    futopt = sdk.marketdata.websocket_client.futopt
    futopt.on("message", q.put)
    futopt.on("connect", lambda: state.update(connected=True))
    futopt.on("disconnect", lambda *a: state.update(connected=False))
    futopt.on("error", lambda *a: log(f"WebSocket 錯誤：{a}"))

    def subscribe() -> None:
        futopt.connect()
        for s in symbols:
            futopt.subscribe({"channel": "trades", "symbol": s})
        for s in near_syms:
            futopt.subscribe({"channel": "books", "symbol": s})
        state["connected"] = True
        log("已訂閱：" + "、".join(symbols) + f"（逐筆）＋ {'、'.join(near_syms)}（五檔）")

    subscribe()
    live = LiveCombo(day, near, prev_close, hist, log)
    uploader = Uploader(env_value(Path(args.env), DSN_ENV), log) if args.upload else None
    pushed_at, pushed_events = 0.0, -1
    rest = sdk.marketdata.rest_client.futopt
    # 三個商品的近月都補抓（小台先，模擬單要用）；補抓過的成交之後推送來就跳過
    filled_all = {s: backfill(rest, s, log) for s in near_syms}
    filled = filled_all[main_sym]
    last_t = {s: max(r[0] for r in rows) for s, rows in filled_all.items() if rows}
    for t_us, price, size, _, bid, ask in filled or []:
        live.on_tick(t_us, price, size, bid, ask)
    quotes, quote_raw = setup_quotes(day, near, Path(args.history).parent / "taifex_ticks", log)
    for qs in quotes:
        for t_us, price, size, _, bid, ask in filled_all.get(qs) or []:
            quotes[qs].on_tick(t_us, price, size, bid, ask)
        if isinstance(quotes[qs], LiveCombo) and now_us_start() > open_us_of(day) + OPEN_SLACK_S * M.US and (
                not quotes[qs].t or quotes[qs].t[0] > open_us_of(day) + OPEN_SLACK_S * M.US):
            quotes[qs].disable(now_us_start(), "開盤那段沒有錄到（補抓失敗或不完整）")
    open_us = clock(day, B.SESSION_OPEN)
    now_us = int(time.time() * M.US)
    if now_us > open_us + OPEN_SLACK_S * M.US and (not live.t or live.t[0] > open_us + OPEN_SLACK_S * M.US):
        live.disable(now_us, "開盤那段沒有錄到（補抓失敗或不完整）")
    raw_path = out / f"raw_{day:%Y%m%d}.jsonl"
    tick_csv = out / f"Daily_{day:%Y_%m_%d}_fubon.csv"
    rawf = raw_path.open("a", encoding="utf-8")
    # 補抓成功＝手上有今天從開盤起的完整逐筆 → 重寫整份（程式中途重啟也不會重複）；失敗就接著寫
    tickf = tick_csv.open("w" if filled else "a", newline="", encoding="utf-8")
    tw = csv.writer(tickf)
    if tick_csv.stat().st_size == 0:
        tw.writerow(["成交日期", "商品代號", "到期月份(週別)", "成交時間", "成交價格", "成交數量(B+S)"])
    for s, rows in filled_all.items():
        for t_us, price, size, *_ in rows or []:
            tw.writerow([f"{day:%Y%m%d}", TAIFEX_CODE[s[:3]], near, M.us_to_dt(t_us).strftime("%H%M%S"), price, int(size)])
    stop_at = datetime.fromisoformat(f"{day}T{STOP_AT}:00").replace(tzinfo=M.TPE)
    n_ticks = 0
    while datetime.now(M.TPE) < stop_at:
        try:
            msg = q.get(timeout=0.5)
        except queue.Empty:
            msg = None
        if msg is not None:
            text = msg if isinstance(msg, str) else json.dumps(msg, ensure_ascii=False)
            rawf.write(text + "\n")
            body = json.loads(text)
            data = body.get("data") or {}
            if body.get("event") == "data" and body.get("channel") == "trades" and not data.get("isTrial"):
                sym, t_us = data.get("symbol"), int(data["time"])
                if t_us <= last_t.get(sym, 0):
                    continue  # 補抓已經算過的成交（用成交時間判斷，查詢與推送的序號不一定同一套）
                for tr in data.get("trades") or []:
                    price, size = float(tr["price"]), float(tr["size"])
                    tw.writerow([f"{day:%Y%m%d}", TAIFEX_CODE.get(sym[:3], sym[:3]), symbols.get(sym, sym),
                                 M.us_to_dt(t_us).strftime("%H%M%S"), price, int(size)])
                    if sym == main_sym:
                        live.on_tick(t_us, price, size, tr.get("bid"), tr.get("ask"))
                        n_ticks += 1
                    elif sym in quotes:
                        quotes[sym].on_tick(t_us, price, size, tr.get("bid"), tr.get("ask"))
            elif body.get("event") == "data" and body.get("channel") == "books" and not data.get("isTrial"):
                book_of = live if data.get("symbol") == main_sym else quotes.get(data.get("symbol"))
                if book_of is not None:
                    book_of.on_book(int(data["time"]), data.get("bids") or [], data.get("asks") or [])
        live.decide(int(time.time() * M.US), wall=True)
        for tracker in quotes.values():
            if isinstance(tracker, LiveCombo):
                tracker.decide(int(time.time() * M.US), wall=True)
        if uploader is not None and (live.n_events() != pushed_events or time.time() - pushed_at >= UPLOAD_EVERY_S):
            uploader.push(live.snapshot("live", "fubon_live", main_sym, datetime.now(M.TPE)))
            for qs, tracker in quotes.items():
                uploader.push(tracker.snapshot("live", "fubon_live", qs, datetime.now(M.TPE)), tracker.product)
            pushed_at, pushed_events = time.time(), live.n_events()
        if not state["connected"]:
            log("連線中斷，重新連線")
            time.sleep(RECONNECT_WAIT_S)
            try:
                subscribe()
            except Exception as exc:  # noqa: BLE001  # 記錄後下一輪再試，不讓盤中錄製整個停掉
                log(f"重新連線失敗：{exc}")
    try:
        futopt.disconnect()
        sdk.logout()
    except Exception as exc:  # noqa: BLE001  # 收盤後的清理失敗不影響已錄的資料
        log(f"斷線清理失敗：{exc}")
    rawf.close()
    tickf.close()
    with raw_path.open("rb") as src, gzip.open(str(raw_path) + ".gz", "wb") as dst:
        shutil.copyfileobj(src, dst)
    raw_path.unlink()
    log(f"收盤：{main_sym} 收到 {n_ticks:,} 筆成交；原始訊息已壓縮成 {raw_path.name}.gz")
    finish(day, out, live, tick_csv, history, last_raw, log,
           (uploader, "fubon_live", main_sym) if uploader is not None else None)
    finish_quotes(day, out, quotes, quote_raw, tick_csv, near, uploader, log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
