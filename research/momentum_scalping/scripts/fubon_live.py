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
DECISION_GRACE_S = 3.0          # 到了決策時點、還沒收到之後的成交時，最多再等幾秒才用手上的逐筆決策
RECONNECT_WAIT_S = 5.0
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
        ready = (lambda T: now_us >= T + DECISION_GRACE_S * M.US) if wall else (lambda T: now_us >= T)
        if not self.orb_done and self.t and ready(self.orb_at):
            self.orb_done = True
            seg = self.p[:self.last_before(self.orb_at) + 1]
            if seg[-1] > seg[0]:
                self.pending.append((self.orb_at + B.LATENCY_US, "ORB_LONG", 1, "entry", self.orb_at))
                self.orb = {"side": 1, "stop": min(seg), "signal_t": self.orb_at}
                self.log(f"ORB 開盤 5 分鐘收紅（{seg[0]:.0f} → {seg[-1]:.0f}）→ 模擬買進 1 口，停損 {min(seg):.0f}")
            else:
                self.log(f"ORB 開盤 5 分鐘{'收黑' if seg[-1] < seg[0] else '平盤'}（{seg[0]:.0f} → {seg[-1]:.0f}）→ 只做多，今天不做")
        while self.next_check < len(self.checks) and ready(self.checks[self.next_check]):
            T = self.checks[self.next_check]
            self.next_check += 1
            if T >= self.eod or not self.t:
                continue
            i = self.last_before(T)
            m = B.NOISE_CHECKS[self.next_check - 1]
            lb = min(self.p[0], self.prev_close) * (1 - self.sigma[m])
            target = -1 if self.p[i] < min(lb, self.vwap[i]) else 0
            self.log(f"噪音區 {hms(T)[:5]}：價 {self.p[i]:.0f}、下緣 {lb:.0f}、VWAP {self.vwap[i]:.1f} → "
                     f"{'持空' if target else '空手'}")
            if target != self.noise_pos:
                kind = "entry" if target else "exit"
                self.pending.append((T + B.LATENCY_US, "NOISE_SHORT", -1 if target else 1, kind, T))
                self.noise_pos = target
        if not self.eod_done and ready(self.eod):
            self.eod_done = True
            if self.orb is not None and "entry" in self.orb and "exit" not in self.orb and "trigger" not in self.orb:
                self.pending.append((self.eod + B.LATENCY_US, "ORB_LONG", -1, "EOD", self.eod))
            if self.noise_pos:
                self.pending.append((self.eod + B.LATENCY_US, "NOISE_SHORT", 1, "EOD", self.eod))
                self.noise_pos = 0

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
                    self.log(f"ORB 進場價 {price:.0f} 已在停損 {o['stop']:.0f} 之下 → 取消（不算交易）")
                    self.orb = None
                    continue
                o["target"] = price + B.ORB_TARGET_R * risk
                self.log(f"[模擬成交] ORB 買進 {price:.0f} @ {hms(self.t[i])}，停損 {o['stop']:.0f}、停利 {o['target']:.0f}")
            elif sleeve == "ORB_LONG":
                self.book("ORB_LONG", 1, self.orb["signal_t"], self.orb["entry"], (self.t[i], price), kind)
                self.orb["exit"] = True
            elif kind == "entry":
                self.noise_entry, self.noise_signal = (self.t[i], price), signal_t
                self.log(f"[模擬成交] 噪音區放空 {price:.0f} @ {hms(self.t[i])}")
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
            self.log(f"ORB {'碰到停損' if reason == 'STOP' else '碰到停利'} {self.p[i]:.0f} @ {hms(self.t[i])}")

    def book(self, sleeve, side, signal_t, entry, exit_, reason) -> None:
        (et, ep), (xt, xp) = entry, exit_
        points = side * (xp - ep)
        net = points * B.POINT_VALUE - 2 * B.COMMISSION - B.TAX * (ep + xp) * B.POINT_VALUE
        row = {"day": str(self.day), "sleeve": sleeve, "side": side, "signal_time": hms(signal_t), "entry_time": hms(et),
               "entry_price": ep, "exit_time": hms(xt), "exit_price": xp, "exit_reason": reason,
               "points": round(points, 2), "net_pnl": round(net, 1), "source": "live"}
        self.trades.append(row)
        self.log(f"[模擬平倉] {sleeve} {ep:.0f} → {xp:.0f}（{reason}），淨 {net:+,.0f} 元")


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


def finish(day: date, out: Path, live: LiveCombo, tick_csv: Path, folder: Path, last_raw, log) -> None:
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
    finish(day, out, live, tick_csv, folder, last_raw, print)
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
    raw_path = out / f"raw_{day:%Y%m%d}.jsonl"
    tick_csv = out / f"Daily_{day:%Y_%m_%d}_fubon.csv"
    rawf = raw_path.open("a", encoding="utf-8")
    tickf = tick_csv.open("a", newline="", encoding="utf-8")
    tw = csv.writer(tickf)
    if tick_csv.stat().st_size == 0:
        tw.writerow(["成交日期", "商品代號", "到期月份(週別)", "成交時間", "成交價格", "成交數量(B+S)"])
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
                for tr in data.get("trades") or []:
                    price, size = float(tr["price"]), float(tr["size"])
                    tw.writerow([f"{day:%Y%m%d}", PRODUCT_TAIFEX if sym.startswith("MXF") else "TX", symbols.get(sym, sym),
                                 M.us_to_dt(t_us).strftime("%H%M%S"), price, int(size)])
                    if sym == main_sym:
                        live.on_tick(t_us, price, size)
                        n_ticks += 1
        live.decide(int(time.time() * M.US), wall=True)
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
    finish(day, out, live, tick_csv, history, last_raw, log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
