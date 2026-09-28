"""每日選股看板（D-163／D-164）：兩條線每天新增、剔除、目前持股，以及下一個交易日的計畫。

資料來自回測引擎從 `replay_start` 一路重播到今天（同一套成交語意：隔日開盤、零股、參與率、
漲跌停），這裡只把帳本（ledger）與分割調整轉成「每天誰進、誰出、手上有誰」。
**不含任何報酬數字**：2026-09-09 起是第二保留期（D-109），頁面只列名單與持有天數。
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import Field

from twquant.backtest.engine import NextPlan, SplitAdjustment, Trade
from twquant.config import BaseConfig

BOARD_FILE = "pick_board.json"
BoardKind = Literal["rebalance", "slots"]


class BoardStrategy(BaseConfig):
    """看板上的一條線。"""

    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    config: str = Field(min_length=1)  # configs/strategies/<config>.yaml
    summary: str = Field(min_length=1)
    # 補位型才有意義：每天模型前 n 名裡「過去 watch_lookback_sessions 天沒進過前 n」的新名字（觀察名單）
    watch: bool = False


class PickBoardConfig(BaseConfig):
    """configs/pick_board.yaml。"""

    replay_start: dt.date  # 重播起點：之前沒有持股（空手起跑），所以要比顯示窗早一段暖機
    display_sessions: int = Field(ge=1)  # 頁面顯示最近幾個交易日
    initial_capital: float = Field(gt=0.0)
    lot_size: int = Field(ge=1)
    max_participation: float = Field(gt=0.0, le=1.0)
    watch_lookback_sessions: int = Field(ge=1)  # 「新名字」＝過去幾個交易日沒進過前 n 名
    strategies: tuple[BoardStrategy, ...] = Field(min_length=1)


@dataclass
class _Open:
    shares: int
    entry_date: dt.date
    entry_ix: int


@dataclass
class DayChanges:
    """一個交易日（開盤成交日）的進出與收盤後持股。"""

    date: dt.date
    entered: list[dict[str, object]] = field(default_factory=list)
    exited: list[dict[str, object]] = field(default_factory=list)
    holdings: list[dict[str, object]] = field(default_factory=list)


def replay_days(
    sessions: Sequence[dt.date],
    ledger: Sequence[Trade],
    splits: Sequence[SplitAdjustment],
) -> list[DayChanges]:
    """把帳本逐日重建成部位：第一次買進＝新增，賣到 0 股＝剔除；中途加碼、減碼不算進出。

    分割只調股數、不是交易。股數變負或帳本日期不在 `sessions` 裡 → raise（帳本與日曆對不上）。
    """
    ix = {d: i for i, d in enumerate(sessions)}
    by_day: dict[dt.date, list[Trade]] = {}
    for t in ledger:
        if t.date not in ix:
            raise ValueError(f"帳本日期 {t.date}（{t.stock_id}）不在重播日曆裡")
        by_day.setdefault(t.date, []).append(t)
    split_by_day: dict[dt.date, list[SplitAdjustment]] = {}
    for s in splits:
        split_by_day.setdefault(s.date, []).append(s)

    held: dict[str, _Open] = {}
    out: list[DayChanges] = []
    for i, day in enumerate(sessions):
        for s in split_by_day.get(day, []):
            if s.stock_id in held:
                held[s.stock_id].shares = s.shares_after
        rec = DayChanges(date=day)
        for t in by_day.get(day, []):
            if t.side == "buy":
                pos = held.get(t.stock_id)
                if pos is None:
                    held[t.stock_id] = _Open(t.shares, day, i)
                    rec.entered.append({"stock_id": t.stock_id, "reason": t.reason})
                else:
                    pos.shares += t.shares
            elif t.side == "sell":
                pos = held.get(t.stock_id)
                if pos is None or t.shares > pos.shares:
                    have = pos.shares if pos else 0
                    raise ValueError(f"{day} {t.stock_id} 賣 {t.shares} 股，手上只有 {have} 股")
                pos.shares -= t.shares
                if pos.shares == 0:
                    del held[t.stock_id]
                    rec.exited.append(
                        {
                            "stock_id": t.stock_id,
                            "reason": t.reason,
                            "entry_date": pos.entry_date.isoformat(),
                            "held_sessions": i - pos.entry_ix,
                        }
                    )
            else:
                raise ValueError(f"帳本 side 只能是 buy／sell，收到 {t.side!r}")
        rec.holdings = [
            {
                "stock_id": sid,
                "entry_date": pos.entry_date.isoformat(),
                "held_sessions": i - pos.entry_ix,
            }
            for sid, pos in sorted(held.items(), key=lambda kv: (kv[1].entry_ix, kv[0]))
        ]
        out.append(rec)
    return out


def new_names(
    ranked: Sequence[tuple[dt.date, Sequence[str]]], lookback: int
) -> dict[dt.date, list[str]]:
    """每天前 n 名裡的新名字：過去 `lookback` 個交易日都沒進過前 n 名（同 `scripts/a20_picks.py`）。

    `ranked` 依日期由舊到新。前 `lookback` 天沒有完整回看，不輸出（避免把「沒看到」當成「新」）。
    """
    out: dict[dt.date, list[str]] = {}
    for i, (day, top) in enumerate(ranked):
        if i < lookback:
            continue
        recent = {sid for _, prev in ranked[i - lookback : i] for sid in prev}
        out[day] = [sid for sid in top if sid not in recent]
    return out


def plan_payload(plan: NextPlan | None, holdings: set[str]) -> dict[str, object]:
    """下一個交易日開盤的計畫：買誰、賣誰。再平衡日由目標名單與目前持股的差算出。"""
    if plan is None:
        return {"rebalance": False, "buy": [], "sell": []}
    if plan.rebalance is not None:
        target = {sid for sid, w in plan.rebalance if w > 0.0}
        return {
            "rebalance": True,
            "buy": sorted(target - holdings),
            "sell": [{"stock_id": sid, "reason": "rebalance"} for sid in sorted(holdings - target)],
        }
    return {
        "rebalance": False,
        "buy": [sid for sid in plan.entries if sid not in holdings],
        "sell": [{"stock_id": sid, "reason": why} for sid, why in plan.exits],
    }


def strategy_payload(
    strat: BoardStrategy,
    kind: BoardKind,
    hold_sessions: int | None,
    slots_n: int | None,
    days: Sequence[DayChanges],
    plan: NextPlan | None,
    display_sessions: int,
    watch: dict[dt.date, list[str]] | None = None,
) -> dict[str, object]:
    """一條線的看板內容：最近 `display_sessions` 個交易日的進出與持股、下一個交易日的計畫。

    `watch`（補位型）：每天模型前 n 名裡的新名字；顯示窗內缺哪一天就 raise（不給半套名單）。
    """
    if not days:
        raise ValueError(f"{strat.code}：重播沒有任何交易日")
    shown = list(days)[-display_sessions:]
    last = {str(h["stock_id"]) for h in shown[-1].holdings}
    if watch is not None:
        missing = [d.date for d in shown if d.date not in watch]
        if missing:
            raise ValueError(f"{strat.code}：觀察名單缺 {missing[:3]} 等 {len(missing)} 天")
    return {
        "code": strat.code,
        "name": strat.name,
        "summary": strat.summary,
        "config": strat.config,
        "kind": kind,
        "hold_sessions": hold_sessions,
        "slots": slots_n,
        "days": [
            {
                "date": d.date.isoformat(),
                "entered": d.entered,
                "exited": d.exited,
                "holdings": d.holdings,
                **({"watch_new": watch[d.date]} if watch is not None else {}),
            }
            for d in shown
        ],
        "next": plan_payload(plan, last),
    }


def board_payload(
    *,
    as_of: dt.date,
    generated_at: dt.datetime,
    cfg: PickBoardConfig,
    strategies: Sequence[dict[str, object]],
    stocks: dict[str, dict[str, str]],
) -> dict[str, object]:
    """整份看板：兩條線＋畫面要用到的股票名稱、產業（只帶出現過的代號）。"""
    seen: set[str] = set()
    for s in strategies:
        for d in s["days"]:  # type: ignore[attr-defined]
            for key in ("entered", "exited", "holdings"):
                seen.update(str(r["stock_id"]) for r in d[key])
            seen.update(str(x) for x in d.get("watch_new", []))
        nxt = s["next"]
        seen.update(str(x) for x in nxt["buy"])  # type: ignore[index]
        seen.update(str(r["stock_id"]) for r in nxt["sell"])  # type: ignore[index]
    return {
        "schema_version": 1,
        "as_of": as_of.isoformat(),
        "generated_at": generated_at.isoformat(),
        "replay_start": cfg.replay_start.isoformat(),
        "strategies": list(strategies),
        "stocks": {sid: stocks.get(sid, {}) for sid in sorted(seen)},
    }


def save_board(data_dir: Path, payload: dict[str, object]) -> Path:
    """寫到 `<data_dir>/pick_board/pick_board.json`（每天覆寫；歷史由重播可重建）。"""
    root = data_dir / "pick_board"
    root.mkdir(parents=True, exist_ok=True)
    path = root / BOARD_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def load_board(data_dir: Path) -> dict[str, object] | None:
    """讀看板；還沒產生過回 None（API 回空狀態，不是錯誤）。"""
    path = data_dir / "pick_board" / BOARD_FILE
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} 不是 JSON 物件")
    return data
