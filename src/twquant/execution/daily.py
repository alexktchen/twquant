"""每日執行流程（SPEC-006 §2）：盤前一致性檢查 → 訂單生成 → 下單 → 對帳。

盤前券商部位/現金 vs 系統帳本不符（> 容差）→ 拒絕執行（AC-3）。對帳發現券商多出
系統不知道的成交 → 回報需凍結次日下單（AC-5）。純運算（不寫檔；落地由呼叫端）。
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from twquant.execution.broker import Order, make_order

if TYPE_CHECKING:
    from twquant.execution.broker import Broker, Fill, OrderAck, Position

_LOT = 1000


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    reason: str


def preflight_check(
    broker_positions: list[Position],
    broker_cash: float,
    ledger_positions: dict[str, int],
    ledger_cash: float,
    tol_shares: int = 1,
    tol_cash: float = 100.0,
) -> PreflightResult:
    """券商部位/現金 vs 帳本一致性檢查（誤差 > 容差 → 拒絕執行）。"""
    bpos = {p.stock_id: p.shares for p in broker_positions}
    for k in set(bpos) | set(ledger_positions):
        if abs(bpos.get(k, 0) - ledger_positions.get(k, 0)) > tol_shares:
            return PreflightResult(
                False,
                f"部位不符：{k}（券商 {bpos.get(k, 0)} vs 帳本 {ledger_positions.get(k, 0)}）",
            )
    if abs(broker_cash - ledger_cash) > tol_cash:
        return PreflightResult(False, f"現金不符：券商 {broker_cash:.0f} vs 帳本 {ledger_cash:.0f}")
    return PreflightResult(True, "")


def build_orders(
    target_weights: dict[str, float],
    current_positions: dict[str, int],
    market: dict[str, dict[str, Any]],  # sid -> {"price": ..., ...}
    portfolio_value: float,
    day: dt.date,
) -> list[Order]:
    """TargetBook 目標權重 → 與現有部位比對 → 訂單清單（帶 idempotency_key）。"""
    orders: list[Order] = []
    for i, sid in enumerate(sorted(set(target_weights) | set(current_positions))):
        m = market.get(sid)
        price = m.get("price") if m else None
        if not price or price <= 0:
            continue
        w = target_weights.get(sid, 0.0)
        desired = int((w * portfolio_value) // (price * _LOT)) * _LOT if w > 0 else 0
        delta = desired - current_positions.get(sid, 0)
        if delta > 0:
            orders.append(make_order(day, sid, "buy", delta, float(price), i))
        elif delta < 0:
            orders.append(make_order(day, sid, "sell", -delta, float(price), i))
    return orders


@dataclass(frozen=True)
class ReconcileResult:
    ok: bool
    unexpected: list[tuple[str, str, int]]  # (stock_id, side, shares) 券商有但系統未送


def reconcile(acks: list[OrderAck], fills: list[Fill]) -> ReconcileResult:
    """對帳：券商回報成交 vs 系統送單。券商多出的成交 → 需凍結次日（AC-5）。"""
    expected: Counter[tuple[str, str, int]] = Counter(
        (a.stock_id, a.side, a.filled_shares) for a in acks if a.filled_shares > 0
    )
    got: Counter[tuple[str, str, int]] = Counter((f.stock_id, f.side, f.shares) for f in fills)
    diff = got - expected  # 券商有、系統帳未涵蓋
    unexpected = sorted(diff.elements())
    return ReconcileResult(ok=not unexpected, unexpected=unexpected)


@dataclass(frozen=True)
class SessionResult:
    aborted: bool
    reason: str
    acks: list[OrderAck]
    reconcile: ReconcileResult | None


def run_session(
    broker: Broker,
    target_weights: dict[str, float],
    market: dict[str, dict[str, Any]],
    portfolio_value: float,
    day: dt.date,
    ledger_positions: dict[str, int],
    ledger_cash: float,
    tol_shares: int = 1,
    tol_cash: float = 100.0,
) -> SessionResult:
    """盤前檢查 → 生單 → 下單 → 對帳。盤前不符即中止且不下任何單（AC-3）。"""
    pf = preflight_check(
        broker.positions(), broker.cash(), ledger_positions, ledger_cash, tol_shares, tol_cash
    )
    if not pf.ok:
        return SessionResult(aborted=True, reason=pf.reason, acks=[], reconcile=None)
    orders = build_orders(target_weights, ledger_positions, market, portfolio_value, day)
    acks = [broker.place(o) for o in orders]
    rec = reconcile(acks, broker.fills(day))
    return SessionResult(aborted=False, reason="", acks=acks, reconcile=rec)
