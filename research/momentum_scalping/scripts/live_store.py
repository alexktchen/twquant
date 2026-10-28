#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
live_store.py — 富邦即時行情先落本機 PG（使用者 2026-10-29：「先寫本機，網站有要再同步」）。

本機 PG 是正本：
  market_live.ticks  每一筆成交（期貨、之後的個股共用；symbol 是富邦代號，如 MXFK6、2330）
  market_live.books  每一次五檔變動：買賣各五檔的價、量存成陣列（一天約 37 萬次變動，存 jsonb 一天 247 MB，陣列小很多）
  serving.futures_*  模擬單狀態：和雲端同名同結構（fubon_live.py 的 Uploader 換成本機 DSN 寫），
                     雲端由 futures_sync.py 從這裡同步上去。

寫入在背景執行緒、每秒 COPY 一批；寫失敗時資料留在記憶體下一秒再試（上限 MAX_BUFFER 筆，超過就停收並大聲記錄），
不擋盤中錄製。富邦原始訊息照樣寫 raw_*.jsonl，PG 出事可以用 fubon_live.py --load-raw 從那裡重灌。
"""
from __future__ import annotations

import gzip
import json
import threading
import time
from datetime import date
from pathlib import Path

import ms_replay as M

SCHEMA = "market_live"
FLUSH_EVERY_S = 1.0
MAX_BUFFER = 2_000_000  # 約一整天三個商品的逐筆＋五檔；PG 一直連不上時的上限
DDL = (
    f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"',
    f'CREATE TABLE IF NOT EXISTS "{SCHEMA}"."ticks" (day date NOT NULL, symbol text NOT NULL, t timestamptz NOT NULL, '
    "price double precision NOT NULL, size integer NOT NULL, bid double precision, ask double precision, src text NOT NULL)",
    # 照時間附加寫入：BRIN 索引只有幾十 KB，查某一天夠快
    f'CREATE INDEX IF NOT EXISTS ticks_t_brin ON "{SCHEMA}"."ticks" USING brin (t)',
    f'CREATE TABLE IF NOT EXISTS "{SCHEMA}"."books" (day date NOT NULL, symbol text NOT NULL, t timestamptz NOT NULL, '
    "bid_px real[] NOT NULL, bid_sz integer[] NOT NULL, ask_px real[] NOT NULL, ask_sz integer[] NOT NULL)",
    f'CREATE INDEX IF NOT EXISTS books_t_brin ON "{SCHEMA}"."books" USING brin (t)',
)
TICK_COLS = "(day, symbol, t, price, size, bid, ask, src)"
BOOK_COLS = "(day, symbol, t, bid_px, bid_sz, ask_px, ask_sz)"


def _num(v):
    return None if v is None else float(v)


def book_row(day: date, symbol: str, t_us: int, bids, asks) -> tuple:
    """富邦五檔 [{"price": .., "size": ..}, ...] → (價陣列, 量陣列) × 買賣。"""
    return (day, symbol, M.us_to_dt(t_us), [float(b["price"]) for b in bids], [int(b["size"]) for b in bids],
            [float(a["price"]) for a in asks], [int(a["size"]) for a in asks])


def connect(dsn: str):
    import psycopg

    conn = psycopg.connect(dsn, prepare_threshold=None, connect_timeout=10)
    with conn.cursor() as cur:
        for stmt in DDL:
            cur.execute(stmt)
    conn.commit()
    return conn


class TickStore:
    """逐筆與五檔寫進本機 PG（背景執行緒、每秒一批）。"""

    def __init__(self, dsn: str, day: date, log):
        self.dsn, self.day, self.log = dsn, day, log
        self.ticks: list = []
        self.books: list = []
        self.lock = threading.Lock()
        self.conn = None
        self.failing = False
        self.dropped = 0
        self.written = {"ticks": 0, "books": 0}
        self.stop_flag = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def tick(self, symbol: str, t_us: int, price: float, size: float, bid=None, ask=None, src: str = "push") -> None:
        self._add(self.ticks, (self.day, symbol, M.us_to_dt(t_us), float(price), int(size), _num(bid), _num(ask), src))

    def book(self, symbol: str, t_us: int, bids, asks) -> None:
        self._add(self.books, book_row(self.day, symbol, t_us, bids, asks))

    def _add(self, buf: list, row) -> None:
        with self.lock:
            if len(self.ticks) + len(self.books) >= MAX_BUFFER:
                self.dropped += 1
                if self.dropped == 1:
                    self.log(f"⚠ 本機 PG 一直寫不進去，記憶體已滿 {MAX_BUFFER:,} 筆，之後不再收；收盤後請用 --load-raw 重灌")
                return
            buf.append(row)

    def replace_backfill(self, symbol: str, rows) -> None:
        """補抓成功＝手上有今天從開盤起的完整逐筆：先清掉這個代號今天的舊資料（程式中途重啟不會重複），再寫補抓的。"""
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(f'DELETE FROM "{SCHEMA}"."ticks" WHERE day = %s AND symbol = %s', (self.day, symbol))
            with cur.copy(f'COPY "{SCHEMA}"."ticks" {TICK_COLS} FROM STDIN') as cp:
                for t_us, price, size, _, bid, ask in rows:
                    cp.write_row((self.day, symbol, M.us_to_dt(t_us), float(price), int(size), _num(bid), _num(ask), "backfill"))
        conn.commit()
        self.written["ticks"] += len(rows)

    def _conn(self):
        if self.conn is None or self.conn.closed:
            self.conn = connect(self.dsn)
        return self.conn

    def flush(self) -> None:
        with self.lock:
            ticks, books = self.ticks, self.books
            self.ticks, self.books = [], []
        if not ticks and not books:
            return
        try:
            conn = self._conn()
            with conn.cursor() as cur:
                if ticks:
                    with cur.copy(f'COPY "{SCHEMA}"."ticks" {TICK_COLS} FROM STDIN') as cp:
                        for r in ticks:
                            cp.write_row(r)
                if books:
                    with cur.copy(f'COPY "{SCHEMA}"."books" {BOOK_COLS} FROM STDIN') as cp:
                        for r in books:
                            cp.write_row(r)
            conn.commit()
            self.written["ticks"] += len(ticks)
            self.written["books"] += len(books)
            if self.failing:
                self.log("本機 PG 恢復，積著的資料已寫進去")
                self.failing = False
        except Exception as exc:  # noqa: BLE001  # 放回去下一秒再試；不讓本機 PG 出事停掉盤中錄製
            with self.lock:
                self.ticks, self.books = ticks + self.ticks, books + self.books
            if not self.failing:
                self.log(f"⚠ 寫本機 PG 失敗（會一直重試）：{exc}")
                self.failing = True
            if self.conn is not None:
                self.conn.close()
                self.conn = None

    def run(self) -> None:
        while not self.stop_flag.is_set():
            time.sleep(FLUSH_EVERY_S)
            self.flush()

    def close(self) -> dict:
        """收盤：停背景執行緒、最後寫一次；還有沒寫進去的就回報（呼叫端提醒要 --load-raw）。"""
        self.stop_flag.set()
        self.thread.join(timeout=FLUSH_EVERY_S * 3)
        self.flush()
        left = len(self.ticks) + len(self.books)
        if self.conn is not None:
            self.conn.close()
        return {**self.written, "unwritten": left, "dropped": self.dropped}


def load_raw(dsn: str, day: date, raw_path: Path, log=print) -> dict:
    """富邦原始訊息（raw_*.jsonl 或 .gz）整天重灌：先清掉那天在 raw 裡出現的代號，再寫一般盤（不含試撮）的逐筆與五檔。
    盤中才開錄、靠補抓的日子，raw 沒有開盤那段（補抓的部分只在 Daily_*_fubon.csv，沒有買賣價）。"""
    gz = Path(str(raw_path) + ".gz")
    src = gz if gz.exists() else raw_path
    if not src.exists():
        raise SystemExit(f"沒有原始訊息：{raw_path}（或 .gz）")
    opener = (lambda: gzip.open(src, "rt", encoding="utf-8")) if src.suffix == ".gz" else (lambda: src.open(encoding="utf-8"))
    ticks, books, bad = [], [], None
    with opener() as f:
        for line in f:
            if bad is not None:
                raise SystemExit(f"{src} 中間有壞掉的一行：{bad[:80]}")
            try:
                body = json.loads(line)
            except json.JSONDecodeError:
                bad = line  # 只容許最後一行沒寫完（錄製當下被中斷）
                continue
            data = body.get("data") or {}
            if body.get("event") != "data" or data.get("isTrial"):
                continue
            sym, t_us = data.get("symbol"), int(data["time"])
            if body.get("channel") == "trades":
                t = M.us_to_dt(t_us)
                for tr in data.get("trades") or []:
                    ticks.append((day, sym, t, float(tr["price"]), int(float(tr["size"])), _num(tr.get("bid")), _num(tr.get("ask")), "raw"))
            elif body.get("channel") == "books":
                books.append(book_row(day, sym, t_us, data.get("bids") or [], data.get("asks") or []))
    if bad is not None and src.suffix == ".gz":
        raise SystemExit(f"{src} 最後一行壞了：{bad[:80]}")
    symbols = sorted({r[1] for r in ticks} | {r[1] for r in books})
    with connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(f'DELETE FROM "{SCHEMA}"."ticks" WHERE day = %s AND symbol = ANY(%s)', (day, symbols))
        cur.execute(f'DELETE FROM "{SCHEMA}"."books" WHERE day = %s AND symbol = ANY(%s)', (day, symbols))
        with cur.copy(f'COPY "{SCHEMA}"."ticks" {TICK_COLS} FROM STDIN') as cp:
            for r in ticks:
                cp.write_row(r)
        with cur.copy(f'COPY "{SCHEMA}"."books" {BOOK_COLS} FROM STDIN') as cp:
            for r in books:
                cp.write_row(r)
        conn.commit()
    log(f"{day} 原始訊息重灌本機 PG：{', '.join(symbols)}；逐筆 {len(ticks):,}、五檔 {len(books):,}")
    return {"symbols": symbols, "ticks": len(ticks), "books": len(books)}
