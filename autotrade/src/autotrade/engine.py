"""主引擎：把行情、策略、風控、委託、帳務接起來。單執行緒、一筆行情處理到底。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from autotrade.broker.base import Broker, Cancelled, Rejected
from autotrade.config import Config
from autotrade.ledger import Ledger
from autotrade.model import OrderRequest, Side, Tick
from autotrade.oms import OrderManager
from autotrade.risk import RiskManager, is_reducing
from autotrade.strategy.base import Strategy


class EndOfDayError(RuntimeError):
    """收盤時還有部位或還有沒結束的委託。"""


class RecoveryError(RuntimeError):
    """從日誌還原狀態時發現不能直接接手的情況。"""


@dataclass(frozen=True)
class Event:
    """引擎記下的一件事。text 給人看，data 給程式還原狀態用（journal 落地）。"""

    ts: datetime
    kind: str
    text: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Restored:
    """從日誌還原出來、要交給引擎接手的狀態。"""

    day: date
    position: int
    avg_price: float
    realized_twd: float
    costs_twd: float
    day_start_realized: float
    entries_today: int
    halt: str | None
    open_orders: tuple[str, ...]


@dataclass
class Engine:
    """一個商品、一個策略的交易引擎。"""

    cfg: Config
    strategy: Strategy
    broker: Broker
    ledger: Ledger = field(init=False)
    risk: RiskManager = field(init=False)
    oms: OrderManager = field(init=False)
    sink: Callable[[Event], None] | None = None
    events: list[Event] = field(default_factory=list)
    _day: date | None = None
    _day_start_realized: float = 0.0
    _cancel_sent: set[str] = field(default_factory=set)
    _strategy_orders: dict[str, OrderRequest] = field(default_factory=dict)
    _last: Tick | None = None

    def __post_init__(self) -> None:
        self.ledger = Ledger(self.cfg.contract)
        self.risk = RiskManager(self.cfg.risk)
        self.oms = OrderManager()

    def _log(self, ts: datetime, kind: str, text: str, **data: Any) -> None:
        event = Event(ts, kind, text, data)
        self.events.append(event)
        if self.sink is not None:
            self.sink(event)

    def restore(self, state: Restored) -> None:
        """接手從日誌還原的狀態。還有結果不明的委託就拒絕，要先到券商確認。"""
        if state.open_orders:
            raise RecoveryError(
                f"日誌裡有結果不明的委託 {list(state.open_orders)}，先到券商確認成交或刪單再啟動"
            )
        if self.events or self.ledger.fills:
            raise RecoveryError("引擎已經開始運作，不能再還原狀態")
        self.ledger.position = state.position
        self.ledger.avg_price = state.avg_price
        self.ledger.realized_twd = state.realized_twd
        self.ledger.costs_twd = state.costs_twd
        self.risk.day = state.day
        self.risk.entries_today = state.entries_today
        self.risk.halt = state.halt
        self._day = state.day
        self._day_start_realized = state.day_start_realized
        self.ledger.reconcile(self.broker.position())

    def day_pnl(self, last_price: float) -> float:
        """當日損益：當日已實現＋未實現。"""
        return (
            self.ledger.realized_twd - self._day_start_realized + self.ledger.open_pnl(last_price)
        )

    def _submit(self, req: OrderRequest, now: datetime, from_strategy: bool = False) -> bool:
        exposure = self.ledger.position + self.oms.pending_signed_qty()
        decision = self.risk.check(req, exposure, now)
        if not decision.allowed:
            self._log(now, "reject", f"風控擋下 {req.side.name} {req.qty} 口：{decision.reason}")
            if from_strategy:
                self.strategy.on_reject(req, decision.reason)
            return False
        order_id = self.broker.submit(req)
        self.oms.add(order_id, req)
        if from_strategy:
            self._strategy_orders[order_id] = req
        entry = not is_reducing(exposure, req.signed_qty)
        if entry:
            self.risk.on_entry_sent()
        self._log(
            now,
            "order",
            f"{order_id} {req.side.name} {req.qty} 口 {req.reason}".rstrip(),
            order_id=order_id,
            side=req.side.name,
            qty=req.qty,
            limit_price=req.limit_price,
            entry=entry,
        )
        return True

    def _cancel(self, order_id: str, now: datetime, why: str) -> None:
        if order_id not in self._cancel_sent:
            self._cancel_sent.add(order_id)
            self.broker.cancel(order_id)
            self._log(now, "cancel", f"{order_id} 刪單：{why}", order_id=order_id)

    def _flatten(self, now: datetime, why: str) -> None:
        """刪掉還沒成交的新倉單，並把部位平掉。"""
        for order in self.oms.working():
            if not is_reducing(self.ledger.position, order.remaining_signed_qty):
                self._cancel(order.order_id, now, why)
        exposure = self.ledger.position + sum(
            o.remaining_signed_qty
            for o in self.oms.working()
            if is_reducing(self.ledger.position, o.remaining_signed_qty)
        )
        if exposure != 0:
            side = Side.SELL if exposure > 0 else Side.BUY
            req = OrderRequest(
                ts=now, symbol=self.cfg.contract.symbol, side=side, qty=abs(exposure), reason=why
            )
            self._submit(req, now)

    def kill(self, now: datetime) -> None:
        """緊急停止：停手並平倉，要人工解除。"""
        self.risk.kill()
        self._log(now, "halt", "緊急停止", halt=self.risk.halt)
        self._flatten(now, "緊急停止")

    def release_kill(self, now: datetime) -> None:
        """人工解除緊急停止。"""
        self.risk.release_kill()
        self._log(now, "release", "解除緊急停止", halt=self.risk.halt)

    def on_tick(self, tick: Tick) -> None:
        """處理一筆行情。"""
        now = tick.ts
        if tick.ts.date() != self._day:
            self._day = tick.ts.date()
            self._day_start_realized = self.ledger.realized_twd
        self._last = tick
        self.risk.on_quote(now)

        got_fill = False
        for event in self.broker.on_tick(tick):
            fill = self.oms.apply(event)
            if fill is not None:
                self.ledger.apply(fill)
                self.strategy.on_fill(fill)
                self._log(
                    now,
                    "fill",
                    f"{event.order_id} {fill.side.name} {fill.qty} 口 {fill.price}",
                    order_id=event.order_id,
                    symbol=fill.symbol,
                    side=fill.side.name,
                    qty=fill.qty,
                    price=fill.price,
                )
                got_fill = True
            elif isinstance(event, Cancelled):
                self._log(now, "cancelled", f"{event.order_id} 已刪單", order_id=event.order_id)
                if event.order_id in self._strategy_orders:
                    self.strategy.on_reject(self._strategy_orders[event.order_id], "已刪單")
            elif isinstance(event, Rejected):
                self._log(
                    now,
                    "broker_reject",
                    f"{event.order_id} 被券商拒絕：{event.reason}",
                    order_id=event.order_id,
                )
                if event.order_id in self._strategy_orders:
                    self.strategy.on_reject(self._strategy_orders[event.order_id], event.reason)
        if got_fill:
            self.ledger.reconcile(self.broker.position())

        was_halted = self.risk.halt
        self.risk.update_pnl(self.day_pnl(tick.price), now)
        if self.risk.halt is not None and was_halted is None:
            self._log(
                now,
                "halt",
                f"停手（{self.risk.halt}）：當日損益 {self.day_pnl(tick.price):+.0f} 元",
                halt=self.risk.halt,
            )

        for order in self.oms.stale(now, self.cfg.risk.order_timeout_s):
            if order.order_id in self._cancel_sent:
                continue
            exit_order = is_reducing(self.ledger.position, order.remaining_signed_qty)
            self._cancel(order.order_id, now, f"超過 {self.cfg.risk.order_timeout_s} 秒沒成交")
            if exit_order:
                # 平倉單卡住代表券商或連線有問題：停手，刪單回報後由 _flatten 重送市價平倉
                self.risk.kill()
                self._log(
                    now, "halt", f"平倉單 {order.order_id} 逾時，緊急停止", halt=self.risk.halt
                )

        if self.risk.halt is not None:
            self._flatten(now, f"停手（{self.risk.halt}）")
        elif now.time() >= self.cfg.risk.force_flat_time:
            self._flatten(now, "強制平倉時間")

        for req in self.strategy.on_tick(tick, self.ledger.position):
            self._submit(req, now, from_strategy=True)

    def finish(self) -> dict[str, float | int]:
        """收盤：確認沒有部位、沒有活著的委託，回傳當日摘要。"""
        working = [o.order_id for o in self.oms.working()]
        if self.ledger.position != 0 or working:
            raise EndOfDayError(f"收盤時部位 {self.ledger.position} 口、還有委託 {working}")
        self.ledger.reconcile(self.broker.position())
        return {
            "fills": len(self.ledger.fills),
            "realized_twd": self.ledger.realized_twd - self._day_start_realized,
            "costs_twd": self.ledger.costs_twd,
            "entries": self.risk.entries_today,
        }
