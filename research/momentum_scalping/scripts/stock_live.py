#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_live.py — 個股當沖：盤中強勢股掃描＋網友原版模擬單（只記帳，不送任何委託）。T-458／D-221。

每 30 秒抓一次富邦全市場快照（上市、上櫃普通股）與加權、櫃買指數，交給網友原版的 Scanner 選出前 30 檔；
前 30 檔與持倉中的股票用 WebSocket 訂逐筆，交給原版 Intraday／Trading 決定進出（stock_engine.StockEngine）。
本機留：每 30 秒的全市場快照、逐筆原始訊息（之後重播用）；雲端（--upload）只放網站顯示用的小份狀態。

一定要在 repo 以外的資料夾執行：富邦 SDK 會在「執行目錄」寫含個資的 log/。程式一開始就 chdir 到 --out。

用法：
  盤中：python stock_live.py --env D:/project/twquant/.env --out D:/project/twquant/data/research_ms/stock_live [--upload]
  用 1 分 K 重播一天（檢查／回測）：
        python stock_live.py --out ... --replay-1m 2026-10-28 --bars D:/project/twquant/data/research_ms/stock_1m
  帳戶與參數：--account-json <檔>，格式 {"account": {"capital": 1000000, "watch_count": 30, ...},
              "parameters": {"trading": {...}, "intraday": {...}, "scanner": {...}, "market": {...}}}；沒給就用網友預設。
網站狀態（payload）的欄位是網站 frontend/src/cloud/daytrade.ts 的 DayTradeDay；改這裡要一起改那裡。
"""
from __future__ import annotations

import argparse
import gzip
import heapq
import json
import os
import queue
import sys
import threading
import time
import traceback
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ms_replay as M  # noqa: E402
import stock_engine as E  # noqa: E402

DSN_ENV = "TWQUANT_SUPABASE_DSN"
TABLE_DDL = ('CREATE TABLE IF NOT EXISTS "serving"."stock_paper" (day date PRIMARY KEY, source text NOT NULL, '
             'status text NOT NULL, payload jsonb NOT NULL, updated_at timestamptz NOT NULL)')
HISTORY_DDL = ('CREATE TABLE IF NOT EXISTS "serving"."stock_paper_history" (day date PRIMARY KEY, '
               'payload jsonb NOT NULL, updated_at timestamptz NOT NULL)')
START_AT, OPEN_AT, STOP_AT, LOGIN_UNTIL = "08:30", "09:00", "13:36", "09:30"
SNAPSHOT_EVERY_S = 30.0
UPLOAD_EVERY_S = 15.0
MAX_SUBSCRIPTIONS = 250       # 2026-10-28 實測一次訂 260 檔沒報錯；超過就退訂已出榜、沒持倉的
BOARD_UPLOAD_FIELDS = ("rank", "symbol", "name", "market", "sector", "score", "change_pct", "market_relative_strength",
                       "sector_relative_strength", "distance_to_intraday_high_pct", "trade_ready")
BOARD_HISTORY_EVERY_S = 60     # 復盤的榜單幾秒存一份（掃描是 30 秒一輪）
EVENTS_KEEP = 150             # 網站時間軸最多帶幾則（盤中每 15 秒重抓，Supabase 每月流出有上限）
INDEX_SYMBOLS = {"TAIEX": "IX0001", "TPEX": "IX0043"}
MS_REPO_DEFAULT = (HERE.parents[3] if len(HERE.parents) > 3 else HERE) / "Momentum_Scalping"  # repo 外另放一份時要給 --ms-repo
SETUP_TEXT = {"MOMENTUM/INITIAL_BREAKOUT": "動能・初次突破", "MOMENTUM/FIRST_PULLBACK_REBREAK": "動能・回檔再突破",
              "MOMENTUM/MOMENTUM_CONTINUATION": "動能・延續", "MOMENTUM/PERSISTENT_MOMENTUM_START": "動能・持續啟動",
              "TREND/TREND_INITIAL_ENTRY": "趨勢・初次進場", "TREND/TREND_RESUME": "趨勢・恢復"}


def lots(shares: Any) -> float:
    """原版的數量是股；網站一律寫張（1 張 = 1,000 股，零股就有小數）。"""
    v = float(shares) / 1000
    return int(v) if v.is_integer() else round(v, 3)


def hms(t_us: int) -> str:
    return M.us_to_dt(t_us).strftime("%H:%M:%S")


def env_value(path: Path, key: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(f"{path} 沒有 {key}")


# --------------------------------------------------------------------------- 帳戶、參數
def load_setup(repo: Path, account_json: Optional[str]):
    mods = M.load_modules(str(repo))
    params = M.load_parameters(mods, account_json, [])
    account = {}
    if account_json:
        payload = json.loads(Path(account_json).read_text(encoding="utf-8"))
        account = dict(payload.get("account") or {})
        unknown = set(account) - set(E.DEFAULT_CONFIG)
        if unknown:
            raise SystemExit(f"{account_json} 的 account 有不認得的欄位：{sorted(unknown)}（可用：{sorted(E.DEFAULT_CONFIG)}）")
    config = {**E.DEFAULT_CONFIG, **account}
    return mods, params, config


def config_view(config: Dict[str, Any], params) -> Dict[str, Any]:
    tp = params["trading"]
    pick = {}
    for name in ("max_position_count", "max_single_position_amount", "max_single_position_pct", "max_risk_per_trade_pct",
                 "max_buy_price", "max_entry_day_change_pct", "max_daily_trade_count", "max_daily_loss_pct",
                 "max_entries_per_symbol_per_day", "reentry_cooldown_minutes", "max_consecutive_losses",
                 "loss_cooldown_minutes", "max_stop_distance_pct", "emergency_stop_loss_pct", "max_sector_position_count"):
        if hasattr(tp, name):
            pick[name] = getattr(tp, name)
    return {"capital": config["capital"], "watch_count": config["watch_count"], "fee_discount": config["fee_discount"],
            "min_fee": config["min_fee"], "tax_rate": config["tax_rate"], "latency_ms": config["latency_ms"], "trading": pick}


# --------------------------------------------------------------------------- 網站狀態
class Recorder:
    """收 StockEngine 的事件，組網站要的狀態。"""

    def __init__(self, day: str, engine_holder: dict, log):
        self.day, self.holder, self.log = day, engine_holder, log
        self.events: List[Dict[str, Any]] = []
        self.board_history: List[list] = []   # [HH:MM:SS, [[代號, 漲幅%, 分數], ...]]（復盤：那一刻榜上有誰，照名次）
        self.names: Dict[str, str] = {}
        self.last_board_us = 0
        self.traded: set = set()

    @property
    def engine(self) -> E.StockEngine:
        return self.holder["engine"]

    def note(self, t_us: int, kind: str, symbol: Optional[str], name: str, text: str) -> None:
        self.events.append({"time": hms(t_us), "kind": kind, "symbol": symbol, "name": name, "text": text})
        self.log(f"[{hms(t_us)}] {symbol or ''} {name} {text}")

    def on_event(self, kind: str, t_us: int, info: Dict[str, Any]) -> None:
        if kind == "board":
            if self.board_history and t_us - self.last_board_us < BOARD_HISTORY_EVERY_S * M.US:
                return
            self.last_board_us = t_us
            rows = []
            for r in info["board"]:
                self.names[r["symbol"]] = r.get("name") or self.names.get(r["symbol"], "")
                rows.append([r["symbol"], None if r.get("change_pct") is None else round(float(r["change_pct"]), 1),
                             None if r.get("score") is None else round(float(r["score"]))])
            self.board_history.append([hms(t_us), rows])
            return
        sym, name = info.get("symbol"), info.get("name") or ""
        if kind == "signal":
            setup = SETUP_TEXT.get(f"{info.get('strategy')}/{info.get('setup')}", f"{info.get('strategy')}/{info.get('setup')}")
            self.note(t_us, "signal", sym, name, f"{info.get('why') or setup + '訊號'}，停損 {round(info['stop'], 2):g} → 模擬買進 {lots(info['quantity'])} 張")
        elif kind == "fill":
            self.traded.add(sym)
            self.note(t_us, "fill", sym, name, f"模擬買進 {lots(info['quantity'])} 張 @ {info['price']:g}，停損 {info['stop']:g}")
        elif kind == "exit_signal":
            self.note(t_us, "signal", sym, name, f"{E.EXIT_TEXT.get(info['reason'], info['reason'])}（價 {info['price']:g}）→ 賣出 {lots(info['quantity'])} 張")
        elif kind == "partial":
            self.note(t_us, "exit", sym, name, f"減碼賣出 {lots(info['quantity'])} 張 @ {info['price']:g}（{E.EXIT_TEXT.get(info['reason'], info['reason'])}）")
        elif kind in ("short_signal", "short_fill", "short_exit"):
            k = {"short_signal": "signal", "short_fill": "fill", "short_exit": "exit"}[kind]
            self.note(t_us, k, sym, name, f"〔{E.SHORT_BOOK_NAME[info['book']]}〕{info['text']}")
        elif kind == "closed":
            t = info
            reason = E.EXIT_TEXT.get(t["final_exit_reason"], t["final_exit_reason"])
            self.note(t_us, "exit", t["symbol"], t.get("name") or "",
                      f"模擬賣出 @ {t['avg_exit_price']:g}（{reason}），這筆淨 {t['net_pnl']:+,.0f} 元")

    def market_view(self) -> Dict[str, Any]:
        ctx = self.engine.market_context or {}
        return {"state": ctx.get("market_state"), "trend": ctx.get("intraday_trend"), "score": ctx.get("market_score"),
                "taiex_pct": (ctx.get("taiex") or {}).get("change_pct"), "tpex_pct": (ctx.get("tpex") or {}).get("change_pct"),
                "breadth": (ctx.get("breadth") or {}).get("net_ratio"),
                "momentum_allowed": ctx.get("momentum_long_allowed"), "trend_allowed": ctx.get("trend_long_allowed")}

    def trades_view(self) -> List[Dict[str, Any]]:
        out = []
        for t in self.engine.trades:
            out.append({"symbol": t["symbol"], "name": t["name"], "market": t["market"], "strategy": t["strategy"],
                        "setup": SETUP_TEXT.get(f"{t['strategy']}/{t['setup']}", t["setup"]),
                        "entry_time": t["entry_time"][11:19], "entry_price": t["entry_price"], "quantity": lots(t["quantity"]),
                        "stop_price": t["stop_price"], "exit_time": t["exit_time"][11:19], "exit_price": t["avg_exit_price"],
                        "exit_reason": t["final_exit_reason"], "net_pnl": t["net_pnl"], "net_ret_pct": t["net_ret_pct"],
                        "fees": t["fees"], "tax": t["tax"], "hold_minutes": t["hold_minutes"]})
        return out

    def payload(self, status: str, source: str, config: Dict[str, Any], updated: datetime) -> Dict[str, Any]:
        eng = self.engine
        positions = eng.positions_view()
        realized = round(eng.realized_net, 0)
        open_pnl = sum(p["open_pnl"] or 0 for p in positions)
        return {
            "version": 1, "day": self.day, "status": status, "source": source, "updated_at": updated.isoformat(timespec="seconds"),
            "board_time": hms(eng.board_time) if eng.board_time else None, "market": self.market_view(),
            "board": eng.board, "positions": positions, "trades": self.trades_view(),
            "events": self.events[-EVENTS_KEEP:], "events_total": len(self.events),
            "account": {"capital": config["capital"], "realized": realized, "open_pnl": round(open_pnl, 0),
                        "total": round(realized + open_pnl, 0), "entries": sum(eng.symbol_entry_counts.values()),
                        "closed": len(eng.trades)},
            "config": self.holder["config_view"], "watch": {"in": eng.watch_changes["in"], "out": eng.watch_changes["out"]},
            "short": None if eng.short is None else eng.short.snapshot(),
        }

    def board_payload(self, status: str, source: str, updated: datetime) -> Dict[str, Any]:
        """只上傳榜單（使用者 2026-10-29：「只上傳 30 檔榜單」）：大盤狀態＋強勢股榜，模擬單只留本機。約 5 KB。"""
        eng = self.engine
        return {"version": 1, "day": self.day, "status": status, "source": source, "board_only": True,
                "updated_at": updated.isoformat(timespec="seconds"),
                "board_time": hms(eng.board_time) if eng.board_time else None, "market": self.market_view(),
                "board": [{k: r.get(k) for k in BOARD_UPLOAD_FIELDS} for r in eng.board],
                "watch": {"in": eng.watch_changes["in"], "out": eng.watch_changes["out"]},
                "short": None if eng.short is None else eng.short.snapshot()}  # 放空記帳摘要（約 1～3 KB），使用者 2026-10-30：「把這幾個空策略放上去」

    def history(self, charts: Dict[str, Any]) -> Dict[str, Any]:
        """收盤後：榜單每 30 秒一份、有成交的股票的 1 分 K 與秒級（復盤用，另一張表，只有點開復盤才讀）。"""
        names = dict(self.names)
        for t in self.engine.trades:
            names.setdefault(t["symbol"], t.get("name") or "")
        return {"version": 1, "day": self.day, "names": names, "board_history": self.board_history, "charts": charts}


def charts_for(symbols, minute_bars: Dict[str, List[tuple]], secs: Dict[str, Dict[int, Tuple[float, float]]]):
    """minute_bars[sym] = [(開始 μs, 開, 高, 低, 收, 量)]；secs[sym] = {09:00:00 起第幾秒: (那一秒最後價, _)}。"""
    out = {}
    for sym in sorted(symbols):
        bars, pv, vol = [], 0.0, 0
        for b in minute_bars.get(sym, []):
            pv += (b[2] + b[3] + b[4]) / 3 * b[5]
            vol += b[5]
            bars.append([hms(b[0])[:5], b[1], b[2], b[3], b[4], round(pv / vol, 2) if vol else b[4]])
        out[sym] = {"bars": bars, "secs": None}  # 秒級只留本機 seconds.json（每檔約 6 萬字元，網站復盤用 1 分 K 就夠）
    return out


def seconds_local(secs: Dict[str, Dict[int, Tuple[float, float]]]) -> Dict[str, Any]:
    return {sym: {"t0": "09:00:00", "s": sorted(v), "p": [v[k][0] for k in sorted(v)]} for sym, v in secs.items()}


# --------------------------------------------------------------------------- 上傳
class Uploader:
    """背景執行緒寫 serving.stock_paper（只留最新一份）；失敗記下來、下一份再試，不擋錄製。"""

    def __init__(self, dsn: str, log):
        import psycopg  # 只有上傳才需要

        self.psycopg, self.dsn, self.log = psycopg, dsn, log
        self.latest = None
        self.cv = threading.Condition()
        self.conn = None
        threading.Thread(target=self.run, daemon=True).start()

    def connect(self):
        if self.conn is None or self.conn.closed:
            self.conn = self.psycopg.connect(self.dsn, prepare_threshold=None, connect_timeout=10)
            with self.conn.cursor() as cur:
                cur.execute('CREATE SCHEMA IF NOT EXISTS "serving"')
                cur.execute(TABLE_DDL)
                cur.execute(HISTORY_DDL)
            self.conn.commit()

    def write(self, payload: dict) -> None:
        self.connect()
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        with self.conn.cursor() as cur:
            cur.execute('INSERT INTO "serving"."stock_paper" (day, source, status, payload, updated_at) VALUES (%s, %s, %s, %s::jsonb, %s) '
                        "ON CONFLICT (day) DO UPDATE SET source = EXCLUDED.source, status = EXCLUDED.status, "
                        "payload = EXCLUDED.payload, updated_at = EXCLUDED.updated_at "
                        "WHERE NOT (stock_paper.source = 'fubon_live' AND EXCLUDED.source = 'minute_replay')",
                        (payload["day"], payload["source"], payload["status"], body, payload["updated_at"]))
        self.conn.commit()

    def push(self, payload: dict) -> None:
        with self.cv:
            self.latest = payload
            self.cv.notify()

    def run(self) -> None:
        while True:
            with self.cv:
                while self.latest is None:
                    self.cv.wait()
                payload, self.latest = self.latest, None
            try:
                self.write(payload)
            except Exception as exc:  # noqa: BLE001  # 記錄後等下一份；上傳問題不能停掉盤中錄製
                self.log(f"⚠ 寫雲端失敗：{exc}")
                if self.conn is not None:
                    self.conn.close()
                    self.conn = None

    def flush(self, payload: dict, history: dict) -> None:
        """收盤定稿：同步寫，失敗要讓呼叫端知道。"""
        self.write(payload)
        with self.conn.cursor() as cur:
            cur.execute('INSERT INTO "serving"."stock_paper_history" (day, payload, updated_at) VALUES (%s, %s::jsonb, %s) '
                        "ON CONFLICT (day) DO UPDATE SET payload = EXCLUDED.payload, updated_at = EXCLUDED.updated_at",
                        (payload["day"], json.dumps(history, ensure_ascii=False, allow_nan=False), payload["updated_at"]))
        self.conn.commit()


# --------------------------------------------------------------------------- 富邦資料轉換
def snapshot_rows(stock) -> Tuple[List[Dict[str, Any]], int]:
    rows, newest = [], 0
    for market in ("TSE", "OTC"):
        snap = stock.snapshot.quotes(market=market, type="COMMONSTOCK")
        if not isinstance(snap, dict) or "data" not in snap:
            raise RuntimeError(f"{market} 快照回應不對：{str(snap)[:200]}")
        for r in snap["data"]:
            sym = str(r.get("symbol") or "")
            price = r.get("lastPrice") if r.get("lastPrice") is not None else r.get("closePrice")
            if not E.COMMON.match(sym) or price is None or r.get("openPrice") is None:
                continue
            change = r.get("change") or 0.0
            t_us = int(r.get("lastUpdated") or 0)
            newest = max(newest, t_us)
            rows.append({"market": "TWSE" if market == "TSE" else "TPEX", "symbol": sym, "name": r.get("name"),
                         "price": float(price), "open": float(r["openPrice"]), "high": float(r.get("highPrice") or price),
                         "low": float(r.get("lowPrice") or price), "prev_close": round(float(price) - float(change), 4),
                         "volume": int(r.get("tradeVolume") or 0), "turnover": float(r.get("tradeValue") or 0.0),
                         "event_time": t_us})
    return rows, newest


def index_now(stock) -> Dict[str, Dict[str, float]]:
    out = {}
    for name, sym in INDEX_SYMBOLS.items():
        q = stock.intraday.quote(symbol=sym)
        price = q.get("closePrice") or q.get("lastPrice")
        if price is None or q.get("openPrice") is None:
            raise RuntimeError(f"{sym} 盤中報價還沒有：{str(q)[:200]}")
        out[name] = {"price": float(price), "open": float(q["openPrice"]), "high": float(q.get("highPrice") or price),
                     "low": float(q.get("lowPrice") or price)}
    return out


def index_daily_from_api(stock, day: str) -> Dict[str, List[Tuple[str, float]]]:
    out = {}
    start = (date.fromisoformat(day) - timedelta(days=90)).isoformat()
    for name, sym in INDEX_SYMBOLS.items():
        payload = stock.historical.candles(**{"symbol": sym, "timeframe": "D", "from": start, "to": day, "sort": "asc"})
        out[name] = [(str(c["date"])[:10], float(c["close"])) for c in payload.get("data") or [] if str(c["date"])[:10] < day]
        if len(out[name]) < 5:
            raise SystemExit(f"{sym} 日 K 不足：{out[name][-3:]}")
    return out


def candles_to_bars(day: str, candles) -> List[tuple]:
    bars = []
    for c in candles:
        ts = datetime.fromisoformat(c["date"])
        if ts.tzinfo is None:
            raise ValueError(f"1 分 K 時間沒有時區：{c['date']}")
        if ts.date().isoformat() != day:
            continue
        bars.append((int(ts.timestamp() * M.US), float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"]),
                     int(c.get("volume") or 0)))
    bars.sort()
    return bars


# --------------------------------------------------------------------------- 1 分 K 重播（檢查與回測）
class MinuteMarket:
    """從 stock_minute_download.py 的檔案組出某一天：每檔 1 分 K、前一天收盤、指數 1 分 K 與日 K。"""

    def __init__(self, folder: Path, day: str):
        self.day = day
        # 母體：產業代號最完整的那份（午夜後抓的代號表可能還沒換日，產業代號全空 → 類股強弱全算不出來）
        uni_files = sorted(folder.glob("universe_*.json"))
        if not uni_files:
            raise SystemExit(f"{folder} 沒有 universe_*.json")
        best = max(uni_files, key=lambda f: sum(1 for r in json.loads(f.read_text(encoding="utf-8")) if r.get("industry")))
        self.universe = {(r["market"], r["symbol"]): r for r in json.loads(best.read_text(encoding="utf-8"))}
        covered = sum(1 for r in self.universe.values() if r.get("industry"))
        if covered < 0.9 * len(self.universe):
            raise SystemExit(f"{best.name} 只有 {covered}/{len(self.universe)} 檔有產業代號，類股強弱會算錯")
        self.bars: Dict[str, List[tuple]] = {}
        self.prev_close: Dict[str, float] = {}
        self.index_bars: Dict[str, List[tuple]] = {}
        self.index_daily: Dict[str, List[Tuple[str, float]]] = {}
        # 一檔一檔讀，讀完只留「這一天的 K 棒」與「前一個有資料的交易日最後一根收盤」——不把十幾天全市場都放進記憶體
        # 同一檔可能有好幾份重疊的下載（例如 10-27～10-28 與 10-15～10-28）：同一根 K 只留一份
        files_of = defaultdict(list)
        for path in sorted((folder / "bars").glob("*.json")):
            parts = path.name.split("_")
            if parts[1] != "D":
                files_of[parts[0]].append(path)
        for sym, paths in files_of.items():
            today: Dict[str, Any] = {}
            prev_last = None  # (時間字串, 收盤)
            for path in paths:
                for c in json.loads(path.read_text(encoding="utf-8")).get("data") or []:
                    stamp = str(c["date"])
                    if stamp[11:16] < "09:00":
                        continue  # 盤前的空白 K（櫃買指數 06:45 起只有量、沒有價）
                    if "close" not in c:
                        raise ValueError(f"{path.name} {stamp} 盤中 K 棒沒有收盤價：{c}")
                    if stamp[:10] == day:
                        today[stamp] = c
                    elif stamp[:10] < day and (prev_last is None or stamp > prev_last[0]):
                        prev_last = (stamp, float(c["close"]))
            if not today:
                continue
            if sym in INDEX_SYMBOLS.values():
                self.index_bars[sym] = candles_to_bars(day, today.values())
                continue
            if prev_last is None:
                continue  # 沒有前一天就沒有昨收（下載期間的第一天）
            self.prev_close[sym] = prev_last[1]
            self.bars[sym] = candles_to_bars(day, today.values())
        for name, sym in INDEX_SYMBOLS.items():
            files = sorted((folder / "bars").glob(f"{sym}_D_*.json"))
            if not files:
                raise SystemExit(f"{folder} 沒有 {sym} 日 K")
            data = json.loads(files[-1].read_text(encoding="utf-8")).get("data") or []
            self.index_daily[name] = sorted((str(c["date"])[:10], float(c["close"])) for c in data if str(c["date"])[:10] < day)
            if sym not in self.index_bars:
                raise SystemExit(f"{day} 沒有 {sym} 的 1 分 K")
        self.market_of = {sym: m for (m, sym) in self.universe}

    def snapshot_at(self, t_us: int) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, float]]]:
        """t_us 那一刻已經收完的 K 棒累計（K 棒開始時間 + 60 秒 ≤ t_us）。"""
        rows = []
        for sym, bars in self.bars.items():
            done = [b for b in bars if b[0] + 60 * M.US <= t_us]
            if not done:
                continue
            vol = sum(b[5] for b in done)
            rows.append({"market": self.market_of.get(sym, "TWSE"), "symbol": sym, "name": self.universe.get((self.market_of.get(sym, "TWSE"), sym), {}).get("name", ""),
                         "price": done[-1][4], "open": done[0][1], "high": max(b[2] for b in done), "low": min(b[3] for b in done),
                         "prev_close": self.prev_close[sym], "volume": vol,
                         "turnover": sum((b[2] + b[3] + b[4]) / 3 * b[5] * 1000 for b in done), "event_time": done[-1][0] + 59 * M.US})
        index = {}
        for name, sym in INDEX_SYMBOLS.items():
            done = [b for b in self.index_bars[sym] if b[0] + 60 * M.US <= t_us]
            if done:
                index[name] = {"price": done[-1][4], "open": done[0][1], "high": max(b[2] for b in done), "low": min(b[3] for b in done)}
        return rows, index


def window(key, ticks: List[tuple], after: int, upto: int) -> List[tuple]:
    """某檔在 (after, upto] 之間的逐筆，帶上自己的 key（用函式綁定；寫成迴圈裡的產生器會晚綁定成最後一檔）。"""
    return [(x[0], x[1], key, x) for x in ticks if after < x[0] <= upto]


def replay_minute(args, mods, params, config, log) -> int:
    day = args.replay_1m
    mk = MinuteMarket(Path(args.bars), day)
    holder: Dict[str, Any] = {"config_view": config_view(config, params)}
    rec = Recorder(day, holder, log)
    engine = E.StockEngine(mods, params, day, config, mk.universe, mk.index_daily, rec.on_event)
    holder["engine"] = engine
    engine.short = E.ShortBook(engine, rec.on_event)
    for sym, prev in mk.prev_close.items():  # 重播：漲停價＝昨收 × 1.1 取到跳動單位；可否放空全當可以（報告註明）
        engine.limit_up[sym] = E.round_tick(prev * 1.1, lo=0, hi=1e9)
        engine.short_ok[sym] = True
    if args.assume_day_trade:  # 歷史上沒有每天的當沖資格：全部當作可以當沖（報告要寫明）
        engine.day_trade.update({sym: True for (_m, sym) in mk.universe})
    ticks_of: Dict[str, List[tuple]] = {}
    secs: Dict[str, Dict[int, Tuple[float, float]]] = defaultdict(dict)
    t0 = E.day_start_us(day, 9, 0)
    end = E.day_start_us(day, 13, 31)
    step = int(SNAPSHOT_EVERY_S * M.US)
    last_t = t0
    log(f"{day}：{len(mk.bars)} 檔有 1 分 K（測試資料第一天沒有昨收的不算）")
    for T in range(t0 + step, end + 1, step):
        # 先把 (last_t, T] 之間、正在看的股票的逐筆依時間餵進去
        streams = [window(key, ticks_of.get(key[1]) or [], last_t, T) for key in list(engine.states)]
        for t_us, _serial, key, tick in heapq.merge(*streams, key=lambda z: (z[0], z[1], z[2])):
            engine.on_tick(key, tick)
            if key[1] in rec.traded or key in engine.positions:
                secs[key[1]][(t_us - t0) // M.US] = (tick[2], 0.0)
        engine.clock(T)
        rows, index = mk.snapshot_at(T)
        if len(index) < 2:
            last_t = T
            continue
        engine.set_snapshot(T, rows, index)
        for key in engine.watched():
            if key in engine.states:
                continue
            sym = key[1]
            ticks_of[sym] = E.bar_ticks(day, mk.bars.get(sym, []))
            engine.ensure_state(key, mk.prev_close.get(sym), True if args.assume_day_trade else None)
            engine.seed(key, [x for x in ticks_of[sym] if x[0] <= T])
        last_t = T
    engine.finish()
    out = Path(args.out) / f"replay_{day}"
    out.mkdir(parents=True, exist_ok=True)
    payload = rec.payload("closed", "minute_replay", config, datetime.fromisoformat(f"{day}T13:40:00").replace(tzinfo=M.TPE))
    charts = charts_for(rec.traded, {s: mk.bars.get(s, []) for s in rec.traded}, secs)
    (out / "payload.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (out / "history.json").write_text(json.dumps(rec.history(charts), ensure_ascii=False), encoding="utf-8")
    (out / "seconds.json").write_text(json.dumps(seconds_local(secs)), encoding="utf-8")
    M.write_csv(out / "trades.csv", engine.trades)
    a = payload["account"]
    log(f"{day} 重播完：掃描 {engine.scan_stats['ROUNDS']} 輪、進出榜 {engine.watch_changes['in']}／{engine.watch_changes['out']} 次、"
        f"成交 {a['closed']} 筆、淨 {a['realized']:+,.0f} 元；警告 {dict(engine.warnings)}")
    sb = payload["short"]["books"]
    log(f"放空：失速空 {sb['A']['closed']} 筆 {sb['A']['realized']:+,.0f} 元；過熱空 {sb['B']['closed']} 筆 裸 {sb['B']['realized']:+,.0f}／對沖 {sb['B']['realized_hedged']:+,.0f} 元；擋掉 {payload['short']['blocked']}")
    if args.upload:
        Uploader(env_value(Path(args.env), DSN_ENV), log).flush(payload, rec.history(charts))
        log("已寫進雲端")
    return 0


# --------------------------------------------------------------------------- 盤中
def live(args, mods, params, config, root: Path) -> int:
    day = datetime.now(M.TPE).date().isoformat()
    out = root / day
    out.mkdir(exist_ok=True)
    logf = (out / "live.log").open("a", encoding="utf-8")

    def log(msg: str) -> None:
        line = f"{datetime.now(M.TPE):%H:%M:%S} {msg}"
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    def at(hhmm: str) -> datetime:
        return datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=M.TPE)

    while datetime.now(M.TPE) < at(START_AT):
        time.sleep(20)
    import fubon_check as C  # 延遲匯入：重播不需要富邦 SDK

    sdk = None
    while sdk is None:
        try:
            sdk = C.connect(Path(args.env))
        except SystemExit as exc:
            log(f"登入失敗：{exc}")
            if datetime.now(M.TPE) >= at(LOGIN_UNTIL):
                log("到 09:30 還登不進去，今天放棄")
                return 1
            time.sleep(120)
    stock = sdk.marketdata.rest_client.stock
    while True:  # 代號表要有今天的、而且產業代號齊（2026-10-29 00:xx 抓到的產業代號全空）
        tickers = {}
        for exchange in ("TWSE", "TPEx"):
            for r in (stock.intraday.tickers(type="EQUITY", exchange=exchange, isNormal=True).get("data") or []):
                tickers[str(r.get("symbol"))] = (("TWSE" if exchange == "TWSE" else "TPEX"), r.get("name"), str(r.get("industry") or ""))
        universe = {(m, s): {"symbol": s, "market": m, "name": n, "industry": ind} for s, (m, n, ind) in tickers.items() if E.COMMON.match(s)}
        covered = sum(1 for v in universe.values() if v["industry"])
        if len(universe) >= 1500 and covered >= 0.9 * len(universe):
            break
        log(f"代號表還不完整（普通股 {len(universe)} 檔、有產業代號 {covered} 檔），一分鐘後重抓")
        if datetime.now(M.TPE) >= at("09:05"):
            raise SystemExit("09:05 代號表還不完整，今天放棄（類股強弱會算錯）")
        time.sleep(60)
    index_daily = index_daily_from_api(stock, day)
    holder: Dict[str, Any] = {"config_view": config_view(config, params)}
    rec = Recorder(day, holder, log)
    engine = E.StockEngine(mods, params, day, config, universe, index_daily, rec.on_event)
    holder["engine"] = engine
    engine.short = E.ShortBook(engine, rec.on_event)  # 放空三條記帳（studies/short_paper）
    log(f"交易日 {day}：普通股 {len(universe)} 檔；加權昨收 {index_daily['TAIEX'][-1]}；帳戶 {holder['config_view']}")
    uploader = Uploader(env_value(Path(args.env), DSN_ENV), log) if (args.upload or args.upload_board) else None

    q: "queue.Queue[str]" = queue.Queue()
    ws = sdk.marketdata.websocket_client.stock
    ws.on("message", q.put)
    ws.on("error", lambda *a: log(f"WebSocket 錯誤：{a}"))
    state = {"connected": True}
    ws.on("disconnect", lambda *a: state.update(connected=False))
    ws.connect()
    subscribed: Dict[str, Optional[str]] = {}   # symbol → 訂閱 id
    sub_time: Dict[str, float] = {}
    rawf = gzip.open(out / f"raw_{day.replace('-', '')}.jsonl.gz", "at", encoding="utf-8")
    snapf = gzip.open(out / f"snapshots_{day.replace('-', '')}.jsonl.gz", "at", encoding="utf-8")
    minute_bars: Dict[str, List[tuple]] = {}
    secs: Dict[str, Dict[int, Tuple[float, float]]] = defaultdict(dict)
    t0 = E.day_start_us(day, 9, 0)
    info_cache: Dict[str, Tuple[Optional[float], Optional[bool], dict]] = {}

    def symbol_info(sym: str):
        if sym not in info_cache:
            t = stock.intraday.ticker(symbol=sym)
            info_cache[sym] = (t.get("referencePrice") or t.get("previousClose"), t.get("canDayTrade"),
                               {"attention": t.get("isAttention"), "disposition": t.get("isDisposition"), "limit_up": t.get("limitUpPrice"),
                                "short_ok": bool(t.get("canDayTrade") and t.get("canBelowFlatMarginShortSell") and not t.get("isDisposition"))})
        return info_cache[sym]

    engine.short_lookup = lambda sym: bool(symbol_info(sym)[2]["short_ok"])  # 弱勢順勢空要進場時才查

    def watch(key) -> None:
        sym = key[1]
        if key not in engine.states:
            prev, can, flags = symbol_info(sym)
            engine.ensure_state(key, float(prev) if prev is not None else None, can)
            if flags.get("limit_up") is not None:
                engine.limit_up[sym] = float(flags["limit_up"])
            engine.short_ok[sym] = flags["short_ok"]
            candles = stock.intraday.candles(symbol=sym, timeframe="1").get("data") or []
            now_min = int(time.time() * M.US) // (60 * M.US) * 60 * M.US
            bars = [b for b in candles_to_bars(day, candles) if b[0] + 60 * M.US <= now_min]
            engine.seed(key, E.bar_ticks(day, bars))
        if sym not in subscribed:
            if len(subscribed) >= MAX_SUBSCRIPTIONS:
                keep = {k[1] for k in engine.watched()}
                drop = [s for s in sorted(subscribed, key=lambda s: sub_time[s]) if s not in keep and subscribed[s]]
                if drop:
                    try:
                        ws.unsubscribe({"id": subscribed[drop[0]]})
                        subscribed.pop(drop[0])
                    except Exception as exc:  # noqa: BLE001  # 退訂失敗只記錄；還是會訂新的
                        log(f"⚠ 退訂 {drop[0]} 失敗：{exc}")
            ws.subscribe({"channel": "trades", "symbol": sym})
            subscribed[sym] = None
            sub_time[sym] = time.time()

    stop_at = at(STOP_AT)
    next_snap = max(time.time(), at(OPEN_AT).timestamp() + 5)
    pushed = 0.0
    while datetime.now(M.TPE) < stop_at:
        try:
            msg = q.get(timeout=0.3)
        except queue.Empty:
            msg = None
        if msg is not None:
            text = msg if isinstance(msg, str) else json.dumps(msg, ensure_ascii=False)
            rawf.write(text + "\n")
            body = json.loads(text)
            data = body.get("data")
            if body.get("event") == "subscribed":
                for d in data if isinstance(data, list) else [data or {}]:
                    if d.get("symbol") in subscribed:
                        subscribed[d["symbol"]] = d.get("id")
            elif body.get("event") == "data" and body.get("channel") == "trades" and isinstance(data, dict) and not data.get("isTrial"):
                sym = str(data.get("symbol"))
                key = next((k for k in engine.states if k[1] == sym), None)
                if key is not None:
                    for tr in data.get("trades") or [data]:
                        if tr.get("price") is None:
                            continue
                        t_us = int(tr.get("time") or data.get("time"))
                        tick = (t_us, int(tr.get("serial") or 0), float(tr["price"]), int(tr.get("size") or 0),
                                int(tr["volume"]) if tr.get("volume") is not None else None, tr.get("bid"), tr.get("ask"))
                        engine.on_tick(key, tick)
                        if sym in rec.traded or key in engine.positions:
                            secs[sym][(t_us - t0) // M.US] = (tick[2], 0.0)
        now = time.time()
        engine.clock(int(now * M.US))
        if now >= next_snap:
            next_snap += SNAPSHOT_EVERY_S
            try:
                rows, _newest = snapshot_rows(stock)
                index = index_now(stock)
            except Exception as exc:  # noqa: BLE001  # 這一輪快照拿不到：記下來、下一輪再試（不拿舊資料決策）
                log(f"⚠ 快照失敗：{exc}")
                continue
            t_us = int(time.time() * M.US)
            snapf.write(json.dumps({"t": t_us, "index": index, "rows": rows}, ensure_ascii=False) + "\n")
            try:
                engine.set_snapshot(t_us, rows, index)
            except Exception:  # noqa: BLE001  # 原版程式這一輪拋錯：完整記下來、這輪不換榜，錄製不停（快照已存，收盤後可重播查原因）
                log("⚠ 這一輪掃描失敗：" + traceback.format_exc()[-1500:])
                continue
            for key in engine.watched():
                try:
                    watch(key)
                except Exception as exc:  # noqa: BLE001  # 單檔補資料失敗：記下來，這檔這輪不看
                    log(f"⚠ {key[1]} 補資料／訂閱失敗：{exc}")
        if now - pushed >= UPLOAD_EVERY_S:
            payload = rec.payload("live", "fubon_live", config, datetime.now(M.TPE))
            # 本機一定留一份最新狀態（使用者 2026-10-29：「不要先到 supabase」→ 不加 --upload 也看得到）
            tmp = out / "live_payload.json.tmp"
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(out / "live_payload.json")
            if uploader is not None:
                uploader.push(rec.board_payload("live", "fubon_live", datetime.now(M.TPE)) if args.upload_board else payload)
            pushed = now
        if not state["connected"]:
            log("WebSocket 斷線，重新連線")
            time.sleep(3)
            try:
                ws.connect()
                for sym in list(subscribed):
                    ws.subscribe({"channel": "trades", "symbol": sym})
                state["connected"] = True
            except Exception as exc:  # noqa: BLE001  # 下一輪再試
                log(f"重新連線失敗：{exc}")
    engine.finish()
    rawf.close()
    snapf.close()
    for sym in rec.traded:
        minute_bars[sym] = candles_to_bars(day, stock.intraday.candles(symbol=sym, timeframe="1").get("data") or [])
    try:
        ws.disconnect()
        sdk.logout()
    except Exception as exc:  # noqa: BLE001  # 收盤後清理失敗不影響已錄資料
        log(f"斷線清理失敗：{exc}")
    payload = rec.payload("closed", "fubon_live", config, datetime.now(M.TPE))
    history = rec.history(charts_for(rec.traded, minute_bars, secs))
    (out / "payload.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (out / "history.json").write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")
    (out / "seconds.json").write_text(json.dumps(seconds_local(secs)), encoding="utf-8")
    M.write_csv(out / "trades.csv", engine.trades)
    a = payload["account"]
    log(f"收盤：成交 {a['closed']} 筆、淨 {a['realized']:+,.0f} 元；掃描 {engine.scan_stats['ROUNDS']} 輪；警告 {dict(engine.warnings)}")
    sb = payload["short"]["books"]
    log(f"放空：失速空 {sb['A']['closed']} 筆 {sb['A']['realized']:+,.0f} 元；過熱空 {sb['B']['closed']} 筆 裸 {sb['B']['realized']:+,.0f}／對沖 {sb['B']['realized_hedged']:+,.0f} 元；擋掉 {payload['short']['blocked']}")
    if uploader is not None and args.upload_board:
        uploader.write(rec.board_payload("closed", "fubon_live", datetime.now(M.TPE)))
        log("收盤榜單已寫進雲端（模擬單只在本機）")
    elif uploader is not None:
        uploader.flush(payload, history)
        log("定稿已寫進雲端")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env", help=".env 絕對路徑（盤中、上傳必填）")
    p.add_argument("--out", required=True, help="資料根目錄（repo 外）；每天一個子資料夾")
    p.add_argument("--ms-repo", default=str(MS_REPO_DEFAULT))
    p.add_argument("--account-json", help="帳戶與參數覆蓋（沒給＝網友預設）")
    p.add_argument("--upload", action="store_true", help="整份狀態寫進雲端 serving.stock_paper（網站「當沖」分頁）")
    p.add_argument("--upload-board", action="store_true", help="只把大盤狀態與 30 檔榜單寫進雲端（模擬單只留本機）")
    p.add_argument("--replay-1m", help="用 1 分 K 重播這一天（不連富邦即時）")
    p.add_argument("--bars", help="stock_minute_download.py 的輸出資料夾（--replay-1m 用）")
    p.add_argument("--assume-day-trade", action=argparse.BooleanOptionalAction, default=True,
                   help="重播時沒有當沖資格資料，預設當作都可以當沖")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=True)
    os.chdir(root)  # 富邦 SDK 的 log/ 寫在這裡，不會進 repo
    account_json = args.account_json
    if account_json is None and (root / "account.json").exists():
        account_json = str(root / "account.json")
    mods, params, config = load_setup(Path(args.ms_repo), account_json)
    if args.replay_1m:
        if not args.bars:
            raise SystemExit("--replay-1m 要 --bars")
        return replay_minute(args, mods, params, config, print)
    if not args.env:
        raise SystemExit("盤中要 --env")
    if args.upload and args.upload_board:
        raise SystemExit("--upload 與 --upload-board 只能選一個")
    return live(args, mods, params, config, root)


if __name__ == "__main__":
    raise SystemExit(main())
