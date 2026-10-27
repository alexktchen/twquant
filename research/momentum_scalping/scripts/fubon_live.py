#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fubon_live.py — 富邦即時行情：錄小台／台指逐筆與小台五檔（只存本機），同時即時跑固定組合
「ORB 只做多 + 噪音區只放空」的模擬單（只記帳，不送任何委託）。

規則、參數、成交方式全部來自 intraday_bidir_test.py（與 paper/、forward_paper.py 同一套，2026-10-26 凍結）：
訊號在 T 發出 → 以 T + 0.5 秒後的第一筆成交價成交，買 +1 點、賣 −1 點。
收盤後用同一個逐筆引擎（B.run_orb／B.run_noise）把錄到的逐筆重算一次，當作正式結果，並和盤中即時紀錄對照。

一定要在 repo 以外的資料夾執行：富邦 SDK 會在「執行目錄」寫含個資的 log/。程式一開始就 chdir 到 --out。

用法：
  python fubon_live.py --env D:/project/twquant/.env --out D:/project/twquant/data/research_ms/fubon_live \
      --history D:/project/twquant/data/research_ms/taifex_30d/csv
  檢查用（不連富邦）：加 --replay-day 2026-10-22，用 --history 裡那天的期交所逐筆重播
  --upload：把狀態寫進雲端 serving.futures_paper（網站「小台模擬」分頁讀它；DSN 取 .env 的 TWQUANT_SUPABASE_DSN）

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
FIELDS = ["day", "sleeve", "side", "signal_time", "entry_time", "entry_price", "exit_time", "exit_price",
          "exit_reason", "points", "net_pnl", "source"]


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


class LiveCombo:
    """單執行緒、只看已到的逐筆：ORB 只做多 + NOISE 只放空，兩個子帳戶各 1 口。"""

    def __init__(self, day: date, contract: str, prev_close: float, history, log):
        self.day, self.contract, self.prev_close, self.log = day, contract, prev_close, log
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

    def disable(self, t_us: int, reason: str) -> None:
        if self.disabled is None:
            self.disabled = reason
            self.note(t_us, "info", None, f"{reason}：今天只錄資料、不做模擬單")

    def note(self, t_us: int, kind: str, sleeve, text: str) -> None:
        self.events.append({"time": hms(t_us)[:8], "kind": kind, "sleeve": sleeve, "text": text})
        self.log(f"[{hms(t_us)[:8]}] {text}")

    # ── 逐筆 ──
    def on_tick(self, t_us: int, price: float, size: float) -> None:
        if not clock(self.day, "08:45") <= t_us <= clock(self.day, "13:45") + 999_999:
            return
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
        net = points * B.POINT_VALUE - 2 * B.COMMISSION - B.TAX * (ep + xp) * B.POINT_VALUE
        row = {"day": str(self.day), "sleeve": sleeve, "side": side, "signal_time": hms(signal_t), "entry_time": hms(et),
               "entry_price": ep, "exit_time": hms(xt), "exit_price": xp, "exit_reason": reason,
               "points": round(points, 2), "net_pnl": round(net, 1), "source": "live"}
        self.trades.append(row)
        word = "賣出" if side > 0 else "回補"
        self.note(xt, "exit", sleeve, f"模擬{word} {xp:.0f}（{REASON_TEXT.get(reason, reason)}），這筆淨 {net:+,.0f} 元")

    def seconds_payload(self) -> dict:
        """秒級復盤：每一秒（有成交的那幾秒）最後成交價與 VWAP；三個陣列都是差分整數（VWAP ×10），省空間。"""
        keys = sorted(self.secs)
        price = [self.secs[k][0] for k in keys]
        if any(x != int(x) for x in price):
            raise ValueError(f"{self.day} 小台成交價有小數，秒級資料的整數編碼不適用")
        vw = [round(self.secs[k][1] * 10) for k in keys]

        def delta(xs):
            return [b - a for a, b in zip([0, *xs[:-1]], xs)]

        return {"version": 1, "day": str(self.day), "t0": B.SESSION_OPEN + ":00", "s": delta(keys),
                "p": delta([int(x) for x in price]), "w": delta(vw)}

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
            open_pnl = round(side * (xp - entry) * B.POINT_VALUE - 2 * B.COMMISSION - B.TAX * (entry + xp) * B.POINT_VALUE, 1)
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
        }


class Uploader:
    """把最新狀態寫進雲端 serving.futures_paper（背景執行緒、只留最新一份；失敗記錄後下次再試，不擋錄製）。"""

    def __init__(self, dsn: str, log):
        import threading

        import psycopg  # 只有上傳才需要

        self.psycopg, self.dsn, self.log = psycopg, dsn, log
        self.box: "queue.Queue[dict]" = queue.Queue(maxsize=1)
        self.conn = None
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def push(self, payload: dict) -> None:
        try:
            self.box.get_nowait()
        except queue.Empty:
            pass
        self.box.put(payload)

    def write(self, payload: dict) -> None:
        if self.conn is None or self.conn.closed:
            self.conn = self.psycopg.connect(self.dsn, prepare_threshold=None, connect_timeout=10)
            with self.conn.cursor() as cur:
                cur.execute('CREATE SCHEMA IF NOT EXISTS "serving"')
                cur.execute(TABLE_DDL)
            self.conn.commit()
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
            payload = self.box.get()
            try:
                self.write(payload)
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
    """富邦當天逐筆查詢（一般盤、不含試撮）：回傳 [(t_us, price, size, serial)]，依時間排序。失敗回 None。"""
    rows, offset = [], 0
    try:
        while True:
            page = futopt.intraday.trades(symbol=symbol_, offset=offset, limit=BACKFILL_LIMIT)
            data = (page or {}).get("data") or []
            for tr in data:
                if tr.get("isTrial"):
                    continue
                rows.append((int(tr["time"]), float(tr["price"]), float(tr["size"]), int(tr.get("serial") or 0)))
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


def history_days(folder: Path, day: date):
    """期交所逐筆：今天之前最後 14 個交易日（B.Day）與今天主力合約的昨收來源。"""
    raw = F.read_taifex(folder, PRODUCT_TAIFEX)
    names = [d for d in sorted(raw) if d < str(day)]
    if len(names) < B.NOISE_LOOKBACK:
        raise SystemExit(f"{folder} 在 {day} 之前只有 {len(names)} 天逐筆，噪音區需要 {B.NOISE_LOOKBACK} 天")
    days = [B.Day(n, raw[n], None) for n in names[-B.NOISE_LOOKBACK:]]
    return days, raw[names[-1]], names[-1]


def official(day: date, tick_csv: Path, folder: Path, history_raw_last, contract: str):
    """收盤後：用逐筆引擎重算（與 forward_paper 同一套函數）。"""
    tmp = tick_csv.parent / "official_input"
    tmp.mkdir(exist_ok=True)
    shutil.copy(tick_csv, tmp / f"Daily_{day:%Y_%m_%d}.csv")
    today = F.read_taifex(tmp, PRODUCT_TAIFEX)[str(day)]
    prev = history_raw_last["closes"].get(today["contract"])
    d = B.Day(str(day), today, prev)
    hist, _, _ = history_days(folder, day)
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
             "net_pnl": t["net_pnl"], "source": "official"} for s, t in rows], today["contract"]


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
    (out / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")
    log("\n".join(lines))
    if final is not None:
        uploader, source, symbol_ = final
        snap = live.snapshot("closed", source, symbol_, datetime.now(M.TPE))
        key = lambda r: (r["sleeve"], float(r["entry_price"]), float(r["exit_price"]), r["exit_reason"])  # noqa: E731
        snap["official"] = rows
        snap["official_match"] = sorted(map(key, live.trades)) == sorted(map(key, rows))
        seconds = live.seconds_payload()
        uploader.flush(snap, seconds)
        (out / "payload.json").write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        (out / "seconds.json").write_text(json.dumps(seconds), encoding="utf-8")
        log(f"定稿已寫進雲端（{'與收盤重算一致' if snap['official_match'] else '與收盤重算不一致，請查'}）")


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


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--env", help=".env 絕對路徑（即時模式必填）")
    p.add_argument("--out", required=True, help="資料根目錄（repo 外）；每天一個子資料夾")
    p.add_argument("--history", required=True, help="期交所逐筆工作資料夾（底下 csv/ 是 Daily_*.csv；前 14 天與昨收）")
    p.add_argument("--replay-day", help="檢查用：重播這天的期交所逐筆，不連富邦")
    p.add_argument("--upload", action="store_true", help="狀態寫進雲端 serving.futures_paper（網站「小台模擬」）")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 排程視窗是 cp950，印「⚠」會當掉
    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=True)
    os.chdir(root)  # 富邦 SDK 的 log/ 寫在這裡，不會進 repo
    if args.replay_day:
        return replay(args)

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
    history = Path(args.history) / "csv"
    hist, last_raw, last_day = history_days(history, day)
    prev_close = last_raw["closes"].get(near)
    if prev_close is None:
        raise SystemExit(f"{last_day} 的逐筆裡沒有 {near} 的收盤價，算不出噪音區")
    symbols = {symbol("MXF", near): near, symbol("MXF", nxt): nxt, symbol("TXF", near): near}
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
        futopt.subscribe({"channel": "books", "symbol": main_sym})
        state["connected"] = True
        log("已訂閱：" + "、".join(symbols) + f"（逐筆）＋ {main_sym}（五檔）")

    subscribe()
    live = LiveCombo(day, near, prev_close, hist, log)
    uploader = Uploader(env_value(Path(args.env), DSN_ENV), log) if args.upload else None
    pushed_at, pushed_events = 0.0, -1
    rest = sdk.marketdata.rest_client.futopt
    filled = backfill(rest, main_sym, log)
    last_t = 0
    for t_us, price, size, _ in filled or []:
        live.on_tick(t_us, price, size)
        last_t = max(last_t, t_us)
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
    for t_us, price, size, _ in filled or []:
        tw.writerow([f"{day:%Y%m%d}", PRODUCT_TAIFEX, near, M.us_to_dt(t_us).strftime("%H%M%S"), price, int(size)])
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
                if sym == main_sym and t_us <= last_t:
                    continue  # 補抓已經算過的成交（用成交時間判斷，查詢與推送的序號不一定同一套）
                for tr in data.get("trades") or []:
                    price, size = float(tr["price"]), float(tr["size"])
                    tw.writerow([f"{day:%Y%m%d}", PRODUCT_TAIFEX if sym.startswith("MXF") else "TX", symbols.get(sym, sym),
                                 M.us_to_dt(t_us).strftime("%H%M%S"), price, int(size)])
                    if sym == main_sym:
                        live.on_tick(t_us, price, size)
                        n_ticks += 1
        live.decide(int(time.time() * M.US), wall=True)
        if uploader is not None and (len(live.events) != pushed_events or time.time() - pushed_at >= UPLOAD_EVERY_S):
            uploader.push(live.snapshot("live", "fubon_live", main_sym, datetime.now(M.TPE)))
            pushed_at, pushed_events = time.time(), len(live.events)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
