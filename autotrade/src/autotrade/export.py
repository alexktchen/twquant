"""把引擎的狀態整理成一份給網站看的快照（單向；網站不能回頭控制交易程式）。這裡不寫檔、不連資料庫。"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from autotrade.config import MODES, ConfigError
from autotrade.control import ControlState
from autotrade.engine import Engine
from autotrade.ledger import Ledger
from autotrade.model import require_taipei

SCHEMA = 1
SOURCES = ("replay", "live")
STATUSES = ("live", "closed")

STATE_OK = "ok"
STATE_HALTED = "halted"
STATE_SUSPENDED = "suspended"
STATE_LINK_DOWN = "link_down"

# 值得給人看的大事；一般的委託、成交、刪單不算（成交另外列在 fills）
NOTABLE = frozenset(
    {
        "halt",
        "release",
        "stop",
        "correction",
        "link",
        "broker_reject",
        "foreign_order",
        "reject",
        "control",
        "control_link",
    }
)

# 跟 twquant 的 AUTOTRADE_STATUS_SPEC 一字不差（兩邊各有測試守著）；一天、一種模式一列
STATUS_DDL = (
    'CREATE TABLE IF NOT EXISTS "serving"."autotrade_status" '
    "(day date, mode text, source text, status text, payload jsonb, updated_at timestamptz, "
    "PRIMARY KEY (day, mode))"
)
STATUS_UPSERT = (
    'INSERT INTO "serving"."autotrade_status" (day, mode, source, status, payload, updated_at) '
    "VALUES (%s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (day, mode) DO UPDATE SET source = EXCLUDED.source, status = EXCLUDED.status, "
    "payload = EXCLUDED.payload, updated_at = EXCLUDED.updated_at"
)

REQUIRED_KEYS = frozenset(
    {
        "schema",
        "day",
        "mode",
        "source",
        "status",
        "updated_at",
        "symbol",
        "strategy",
        "state",
        "halt",
        "position",
        "avg_price",
        "last_price",
        "last_ts",
        "open_pnl_twd",
        "realized_twd",
        "day_pnl_twd",
        "costs_twd",
        "entries",
        "limits",
        "contract",
        "sim",
        "control",
        "fills",
        "corrected",
        "events",
    }
)


def state_of(engine: Engine) -> str:
    """程式現在的狀態；同時發生時連線中斷優先。"""
    if not engine.link_up:
        return STATE_LINK_DOWN
    if engine.risk.suspended:
        return STATE_SUSPENDED
    if engine.risk.halt is not None:
        return STATE_HALTED
    return STATE_OK


def _day_fills(engine: Engine, day: date) -> tuple[list[dict[str, Any]], float]:
    """當天的成交與每筆之後多出來的已實現損益（含成本）；回傳（成交、當日成本）。

    用一本從空手開始的帳重算，所以部位被修正過的日子逐筆只是估算（快照的 corrected 會標出來）。
    """
    shadow = Ledger(engine.cfg.contract)
    rows: list[dict[str, Any]] = []
    for fill in engine.ledger.fills:
        if fill.ts.date() != day:
            continue
        before = shadow.realized_twd
        shadow.apply(fill)
        rows.append(
            {
                "ts": fill.ts.isoformat(),
                "side": fill.side.name,
                "qty": fill.qty,
                "price": fill.price,
                "pnl_twd": shadow.realized_twd - before,
            }
        )
    return rows, shadow.costs_twd


def snapshot(
    engine: Engine,
    now: datetime,
    *,
    source: str,
    status: str,
    strategy: str,
    control: ControlState | None = None,
) -> dict[str, Any]:
    """引擎當下的狀態。金額是元，時間帶 Asia/Taipei 時區。"""
    now = require_taipei(now, "snapshot 的 now")
    if source not in SOURCES:
        raise ValueError(f"source 要是 {SOURCES} 其中之一，拿到 {source!r}")
    if status not in STATUSES:
        raise ValueError(f"status 要是 {STATUSES} 其中之一，拿到 {status!r}")
    if engine.cfg.export is None:
        raise ConfigError("設定檔沒有 [export]，不能輸出狀態")
    last, day = engine.last_tick, engine.day
    if last is None or day is None:
        raise ValueError("還沒收到行情，沒有狀態可以輸出")
    fills, costs = _day_fills(engine, day)
    day_events = [e for e in engine.events if e.ts.date() == day]
    events = [
        {"ts": e.ts.isoformat(), "kind": e.kind, "text": e.text}
        for e in day_events
        if e.kind in NOTABLE
    ]
    open_pnl = engine.ledger.open_pnl(last.price)
    day_pnl = engine.day_pnl(last.price)
    risk = engine.cfg.risk
    contract = engine.cfg.contract
    sim = (
        {"latency_s": engine.cfg.sim.latency_s, "slippage_points": engine.cfg.sim.slippage_points}
        if engine.cfg.mode == "sim"
        else None
    )
    return {
        "schema": SCHEMA,
        "day": day.isoformat(),
        "mode": engine.cfg.mode,
        "source": source,
        "status": status,
        "updated_at": now.isoformat(),
        "symbol": engine.cfg.contract.symbol,
        "strategy": strategy,
        "state": state_of(engine),
        "halt": engine.risk.halt,
        "paused": engine.paused,
        "armed": engine.armed,
        "position": engine.ledger.position,
        "avg_price": engine.ledger.avg_price,
        "last_price": last.price,
        "last_ts": last.ts.isoformat(),
        "open_pnl_twd": open_pnl,
        "realized_twd": day_pnl - open_pnl,
        "day_pnl_twd": day_pnl,
        "costs_twd": costs,
        "entries": engine.risk.entries_today,
        "limits": {
            "max_position": risk.max_position,
            "max_entries_per_day": risk.max_entries_per_day,
            "daily_loss_limit_twd": risk.daily_loss_limit_twd,
            "position_stop_loss_twd": risk.position_stop_loss_twd,
        },
        "contract": {
            "point_value": contract.point_value,
            "commission_twd": contract.commission_twd,
            "tax_rate": contract.tax_rate,
        },
        "sim": sim,
        "control": {
            "listening": control is not None and control.listening,
            "ok": None if control is None else control.ok,
            "checked_at": None
            if control is None or control.checked_at is None
            else control.checked_at.isoformat(),
            "poll_s": None if control is None else control.poll_s,
            "last_command_id": engine.last_command_id,
        },
        "fills": fills,
        "corrected": any(e.kind == "correction" for e in day_events),
        "events": _trim_events(events, engine.cfg.export.max_events),
    }


def _trim_events(events: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """只留 limit 則：先丟最舊的「遠端連線通／不通」，再丟最舊的其他事件（斷斷續續的連線不該把停手擠出去）。"""
    keep = list(events)
    while len(keep) > limit:
        links = [i for i, e in enumerate(keep) if e["kind"] == "control_link"]
        keep.pop(links[0] if links else 0)
    return keep


def dumps(snap: dict[str, Any]) -> str:
    """快照轉成 JSON 文字（鍵排序、中文不轉義；同樣的快照輸出一字不差）。"""
    return json.dumps(snap, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# 沒有在聽遠端緊急停止時，狀態裡那一塊長這樣（跟 snapshot 不帶 control 時一樣）
NOT_LISTENING: dict[str, Any] = {
    "listening": False,
    "ok": None,
    "checked_at": None,
    "poll_s": None,
    "last_command_id": 0,
}


def status_row(text: str) -> tuple[str, str, str, str, str, str]:
    """把狀態檔的內容轉成資料表的一列（day、mode、source、status、payload、updated_at）；不合格就 raise。"""
    snap = json.loads(text)
    if not isinstance(snap, dict):
        raise ValueError(f"狀態檔要是一個 JSON 物件，拿到 {type(snap).__name__}")
    if "control" not in snap:
        # 加遠端緊急停止（A-011）之前產生的狀態檔沒有這一塊：那時候本來就沒有在聽，照實補上
        snap["control"] = dict(NOT_LISTENING)
        text = dumps(snap)
    missing = sorted(REQUIRED_KEYS - snap.keys())
    if missing:
        raise ValueError(f"狀態檔缺欄位：{missing}")
    if snap["schema"] != SCHEMA:
        raise ValueError(f"狀態檔的版本是 {snap['schema']!r}，這支程式只認得 {SCHEMA}")
    if snap["mode"] not in MODES or snap["source"] not in SOURCES or snap["status"] not in STATUSES:
        raise ValueError(
            f"狀態檔的 mode／source／status 不認得："
            f"{snap['mode']!r}／{snap['source']!r}／{snap['status']!r}"
        )
    day = date.fromisoformat(str(snap["day"])).isoformat()
    updated = require_taipei(datetime.fromisoformat(str(snap["updated_at"])), "updated_at")
    return day, snap["mode"], snap["source"], snap["status"], text, updated.isoformat()


def env_value(text: str, name: str) -> str | None:
    """從 .env 的內容找一個變數的值（NAME=value，可有 export 前綴與引號）；沒有這個變數回 None。"""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").lstrip()
        key, sep, value = line.partition("=")
        if sep and key.strip() == name:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value
    return None
