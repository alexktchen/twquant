"""每日選股看板（D-163／D-164／D-165）：兩條線每天的推薦、新增、剔除、目前持股與整體損益。

資料來自回測引擎從 `replay_start` 一路重播到今天（同一套成交語意：隔日開盤、零股、參與率、
漲跌停），這裡把帳本（ledger）與分割調整轉成「每天誰進、誰出、手上有誰」，並帶出淨值曲線。
D-165（使用者裁決）起顯示損益；第二保留期（D-109）照舊：**不得依這些數字改任何參數**。
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import Field

from twquant.backtest.engine import NextPlan, SplitAdjustment, Trade
from twquant.config import BaseConfig

BOARD_FILE = "pick_board.json"
BoardKind = Literal["rebalance", "slots"]


class ReferenceResult(BaseConfig):
    """HTML 版「研究回測參考」引用的一個結果包（T-330）：只登記標籤與路徑，數字從包裡的 metrics 讀。"""

    label: str = Field(min_length=1)
    path: str = Field(min_length=1)  # 相對 data_dir，例如 tracks_e_long_nodiv/E0_lw_e0_ctrl_1x.json


class BoardStrategy(BaseConfig):
    """看板上的一條線。"""

    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    config: str = Field(min_length=1)  # configs/strategies/<config>.yaml
    summary: str = Field(min_length=1)
    # 每天的推薦名單（模型前 n 名，位子滿不滿都列）與其中的新名字（過去 watch_lookback_sessions 天沒進過前 n）
    watch: bool = False
    references: tuple[ReferenceResult, ...] = ()  # HTML 版的研究回測參考（T-330）


class PickBoardConfig(BaseConfig):
    """configs/pick_board.yaml。"""

    replay_start: dt.date  # 重播起點：之前沒有持股（空手起跑），所以要比顯示窗早一段暖機
    display_sessions: int = Field(ge=1)  # 頁面顯示最近幾個交易日
    initial_capital: float = Field(gt=0.0)
    lot_size: int = Field(ge=1)
    max_participation: float = Field(gt=0.0, le=1.0)
    watch_lookback_sessions: int = Field(ge=1)  # 「新名字」＝過去幾個交易日沒進過前 n 名
    html_window_sessions: int = Field(
        ge=1
    )  # HTML 版（T-329）推薦名單「近 N 日上榜／漲幅」看最後幾個交易日
    strategies: tuple[BoardStrategy, ...] = Field(min_length=1)


@dataclass
class _Open:
    shares: int
    entry_date: dt.date
    entry_ix: int
    entry_rec: dict[str, object]  # 當天「新增」那一筆：出場或重播結束時補上漲跌
    cost: float = 0.0  # 買進成交金額合計（不含費用）
    proceeds: float = 0.0  # 已賣出成交金額合計（減碼）
    last_close: float | None = None

    def change(self, close: float | None) -> float | None:
        """成交價算的股價漲跌：(已賣金額＋剩餘股數×收盤) ÷ 買進金額 − 1。不含股利與費用。"""
        if close is None or self.cost <= 0.0:
            return None
        return (self.proceeds + self.shares * close) / self.cost - 1.0


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
    closes: Mapping[dt.date, Mapping[str, float]] | None = None,
) -> list[DayChanges]:
    """把帳本逐日重建成部位：第一次買進＝新增，賣到 0 股＝剔除；中途加碼、減碼不算進出。

    分割只調股數、不是交易。股數變負或帳本日期不在 `sessions` 裡 → raise（帳本與日曆對不上）。
    給了 `closes`（每天收盤價，未還原）就附上漲跌（D-165）：持股＝買進到當天收盤、剔除＝買進到賣出、
    新增＝買進到賣出（已出場）或到重播最後一天收盤（仍持有）。停牌沒有收盤價時沿用最近一次收盤
    （引擎估值同樣凍結）；買進當天就沒有收盤價 → raise。
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
                    entry: dict[str, object] = {"stock_id": t.stock_id, "reason": t.reason}
                    pos = held[t.stock_id] = _Open(0, day, i, entry)
                    rec.entered.append(entry)
                pos.shares += t.shares
                pos.cost += t.shares * t.price
            elif t.side == "sell":
                pos = held.get(t.stock_id)
                if pos is None or t.shares > pos.shares:
                    have = pos.shares if pos else 0
                    raise ValueError(f"{day} {t.stock_id} 賣 {t.shares} 股，手上只有 {have} 股")
                pos.shares -= t.shares
                pos.proceeds += t.shares * t.price
                if pos.shares == 0:
                    del held[t.stock_id]
                    exit_rec: dict[str, object] = {
                        "stock_id": t.stock_id,
                        "reason": t.reason,
                        "entry_date": pos.entry_date.isoformat(),
                        "held_sessions": i - pos.entry_ix,
                    }
                    if closes is not None:
                        realized = pos.change(0.0)
                        exit_rec["change"] = realized
                        pos.entry_rec["change"] = realized
                        pos.entry_rec["change_to"] = "exit"
                    rec.exited.append(exit_rec)
            else:
                raise ValueError(f"帳本 side 只能是 buy／sell，收到 {t.side!r}")
        if closes is not None:
            today = closes.get(day, {})
            for sid, pos in held.items():
                px = today.get(sid)
                if px is not None:
                    pos.last_close = px
                elif pos.last_close is None:
                    raise ValueError(f"{day} {sid} 買進當天沒有收盤價，算不出漲跌")
        rec.holdings = [
            {
                "stock_id": sid,
                "entry_date": pos.entry_date.isoformat(),
                "held_sessions": i - pos.entry_ix,
                **({"change": pos.change(pos.last_close)} if closes is not None else {}),
            }
            for sid, pos in sorted(held.items(), key=lambda kv: (kv[1].entry_ix, kv[0]))
        ]
        out.append(rec)
    if closes is not None:
        for pos in held.values():  # 仍持有：新增那一筆的漲跌算到重播最後一天收盤
            pos.entry_rec["change"] = pos.change(pos.last_close)
            pos.entry_rec["change_to"] = "now"
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


def picks_payload(top: Sequence[str], held: set[str], new: set[str]) -> list[dict[str, object]]:
    """當天的推薦＝模型前 n 名，不論位子滿不滿；逐檔標出收盤後是否持有、是否為新名字。"""
    return [
        {"stock_id": sid, "rank": i + 1, "held": sid in held, "new": sid in new}
        for i, sid in enumerate(top)
    ]


def _day_picks(
    top: list[str] | None, day: DayChanges, watch: dict[dt.date, list[str]] | None
) -> dict[str, object]:
    if top is None:
        return {"picks": None, "picks_missing": "no_scores"}
    new = set(watch[day.date]) if watch is not None else set()
    held = {str(h["stock_id"]) for h in day.holdings}
    return {"picks": picks_payload(top, held, new)}


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
    slots_n: int | None,  # 補位型＝位子數；月換股型＝每次挑幾檔
    days: Sequence[DayChanges],
    plan: NextPlan | None,
    display_sessions: int,
    watch: dict[dt.date, list[str]] | None = None,
    ranked: dict[dt.date, list[str] | None] | None = None,
    equity: Sequence[tuple[dt.date, float]] = (),
    picks_ordered: bool = True,
) -> dict[str, object]:
    """一條線的看板內容：最近 `display_sessions` 個交易日的進出與持股、下一個交易日的計畫。

    `watch`：每天模型前 n 名裡的新名字；`ranked`：每天模型前 n 名（推薦名單）。
    兩者顯示窗內缺哪一天就 raise（不給半套名單）；`ranked` 某天為 None＝模型當天沒有打分，
    輸出 `picks: null` 與 `picks_missing: "no_scores"`，由頁面明示。`picks_ordered`＝名單順序就是模型排名
    （月換股型的目標名單是等權重，順序只是代號序，頁面不顯示名次）。
    `equity`：整段重播的每日淨值（含費用與股利），畫整體損益曲線用（D-165）。
    """
    if not days:
        raise ValueError(f"{strat.code}：重播沒有任何交易日")
    shown = list(days)[-display_sessions:]
    last = {str(h["stock_id"]) for h in shown[-1].holdings}
    if watch is not None:
        missing = [d.date for d in shown if d.date not in watch]
        if missing:
            raise ValueError(f"{strat.code}：觀察名單缺 {missing[:3]} 等 {len(missing)} 天")
    if ranked is not None:
        missing = [d.date for d in shown if d.date not in ranked]
        if missing:
            raise ValueError(f"{strat.code}：推薦名單缺 {missing[:3]} 等 {len(missing)} 天")
    return {
        "code": strat.code,
        "name": strat.name,
        "summary": strat.summary,
        "config": strat.config,
        "kind": kind,
        "hold_sessions": hold_sessions,
        "slots": slots_n,
        "picks_ordered": picks_ordered,
        "equity": [{"date": d.isoformat(), "value": v} for d, v in equity],
        "days": [
            {
                "date": d.date.isoformat(),
                "entered": d.entered,
                "exited": d.exited,
                "holdings": d.holdings,
                **({"watch_new": watch[d.date]} if watch is not None else {}),
                **(_day_picks(ranked[d.date], d, watch) if ranked is not None else {}),
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
            seen.update(str(r["stock_id"]) for r in d.get("picks") or [])
        nxt = s["next"]
        seen.update(str(x) for x in nxt["buy"])  # type: ignore[index]
        seen.update(str(r["stock_id"]) for r in nxt["sell"])  # type: ignore[index]
    return {
        "schema_version": 1,
        "as_of": as_of.isoformat(),
        "generated_at": generated_at.isoformat(),
        "replay_start": cfg.replay_start.isoformat(),
        "initial_capital": cfg.initial_capital,
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
