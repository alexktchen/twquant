"""事件日誌：只增不改的 JSONL，每筆帶前一筆的雜湊（雜湊鏈）。當機後由它還原狀態。"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from autotrade.config import Config
from autotrade.engine import Event, Restored
from autotrade.ledger import Ledger
from autotrade.model import Fill, Side, require_taipei
from autotrade.risk import HALT_DAILY_LOSS

GENESIS = "0" * 64


class JournalError(RuntimeError):
    """日誌檔壞了或被改過。"""


@dataclass(frozen=True)
class Record:
    """日誌的一筆。"""

    seq: int
    ts: datetime
    kind: str
    data: dict[str, Any]
    prev: str
    hash: str


def _digest(seq: int, ts: str, kind: str, data: dict[str, Any], prev: str) -> str:
    body = json.dumps(
        [seq, ts, kind, data, prev], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def read_journal(path: Path) -> list[Record]:
    """讀整份日誌並驗證雜湊鏈；有任何一筆對不上就 raise（含列號）。"""
    records: list[Record] = []
    prev = GENESIS
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            try:
                raw = json.loads(line)
                seq, ts, kind, data = raw["seq"], raw["ts"], raw["kind"], raw["data"]
                got_prev, got_hash = raw["prev"], raw["hash"]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise JournalError(
                    f"{path} 第 {line_no} 列讀不懂（可能是當機時寫到一半）：{line[:80]!r}"
                ) from exc
            if seq != len(records) + 1 or got_prev != prev:
                raise JournalError(
                    f"{path} 第 {line_no} 列接不上前一筆：seq={seq}、prev={got_prev[:12]}…，"
                    f"期望 seq={len(records) + 1}、prev={prev[:12]}…"
                )
            expected = _digest(seq, ts, kind, data, prev)
            if got_hash != expected:
                raise JournalError(f"{path} 第 {line_no} 列的雜湊不對：內容被改過")
            records.append(
                Record(
                    seq,
                    require_taipei(datetime.fromisoformat(ts), "日誌時間"),
                    kind,
                    data,
                    prev,
                    got_hash,
                )
            )
            prev = got_hash
    return records


class Journal:
    """寫日誌。開檔時先驗證既有內容，接著往後寫；每筆都 flush 並 fsync。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        existing = read_journal(path) if path.exists() else []
        self._seq = len(existing)
        self._prev = existing[-1].hash if existing else GENESIS
        self._fh = path.open("a", encoding="utf-8", newline="\n")

    def append(self, ts: datetime, kind: str, data: dict[str, Any]) -> Record:
        """寫一筆。"""
        stamp = require_taipei(ts, "日誌時間").isoformat()
        seq = self._seq + 1
        digest = _digest(seq, stamp, kind, data, self._prev)
        line = json.dumps(
            {
                "seq": seq,
                "ts": stamp,
                "kind": kind,
                "data": data,
                "prev": self._prev,
                "hash": digest,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        self._fh.write(line + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())
        record = Record(seq, datetime.fromisoformat(stamp), kind, data, self._prev, digest)
        self._seq, self._prev = seq, digest
        return record

    def write(self, event: Event) -> None:
        """寫一筆引擎事件（可直接當 Engine 的 sink）。"""
        self.append(event.ts, event.kind, {"text": event.text, **event.data})

    def close(self) -> None:
        """關檔。"""
        self._fh.close()


def recover(records: list[Record], cfg: Config, today: date) -> Restored:
    """由日誌重算部位、損益、當日進場次數、停手狀態，並列出結果不明的委託。"""
    ledger = Ledger(cfg.contract)
    remaining: dict[str, int] = {}
    halt: str | None = None
    halt_day: date | None = None
    entries_today = 0
    stops_today: list[tuple[int, str, bool]] = []
    day_start_realized: float | None = None
    for rec in records:
        rec_day = rec.ts.date()
        if rec_day > today:
            raise JournalError(f"日誌第 {rec.seq} 筆的日期 {rec_day} 比今天 {today} 還晚")
        if rec_day == today and day_start_realized is None:
            day_start_realized = ledger.realized_twd
        data = rec.data
        if rec.kind == "order":
            remaining[data["order_id"]] = int(data["qty"])
            if data["entry"] and rec_day == today:
                entries_today += 1
        elif rec.kind == "fill":
            ledger.apply(
                Fill(
                    ts=rec.ts,
                    symbol=data["symbol"],
                    side=Side[data["side"]],
                    qty=int(data["qty"]),
                    price=float(data["price"]),
                )
            )
            left = remaining[data["order_id"]] - int(data["qty"])
            if left < 0:
                raise JournalError(f"日誌第 {rec.seq} 筆：委託 {data['order_id']} 成交超過委託口數")
            if left == 0:
                del remaining[data["order_id"]]
            else:
                remaining[data["order_id"]] = left
        elif rec.kind in ("cancelled", "broker_reject"):
            del remaining[data["order_id"]]
        elif rec.kind == "correction":
            ledger.adopt(int(data["position"]), float(data["avg_price"]))
        elif rec.kind in ("halt", "release"):
            halt, halt_day = data["halt"], rec_day
        elif rec.kind == "control" and rec_day == today:
            stops_today.append(
                (int(data["command_id"]), str(data["created_at"]), bool(data["applied"]))
            )
    if halt == HALT_DAILY_LOSS and halt_day != today:
        halt = None
    return Restored(
        day=today,
        position=ledger.position,
        avg_price=ledger.avg_price,
        realized_twd=ledger.realized_twd,
        costs_twd=ledger.costs_twd,
        day_start_realized=ledger.realized_twd
        if day_start_realized is None
        else day_start_realized,
        entries_today=entries_today,
        halt=halt,
        open_orders=tuple(sorted(remaining)),
        stops_today=tuple(stops_today),
    )
