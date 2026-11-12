"""主引擎：把行情、策略、風控、委託、帳務接起來。單執行緒、一筆行情處理到底。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from autotrade.broker.base import Broker
from autotrade.config import Config
from autotrade.ledger import Ledger
from autotrade.model import OrderRequest, Side, Tick
from autotrade.oms import OrderManager
from autotrade.risk import RiskManager, is_reducing
from autotrade.strategy.base import Strategy


class EndOfDayError(RuntimeError):
    """收盤時還有部位或還有沒結束的委託。"""


@dataclass(frozen=True)
class Event:
    """引擎記下的一件事（之後由 journal 落地）。"""

    ts: datetime
    kind: str
    text: str


@dataclass
class Engine:
    """一個商品、一個策略的交易引擎。"""

    cfg: Config
    strategy: Strategy
    broker: Broker
    ledger: Ledger = field(init=False)
    risk: RiskManager = field(init=False)
    oms: OrderManager = field(init=False)
    events: list[Event] = field(default_factory=list)
    _day: date | None = None
    _day_start_realized: float = 0.0
    _cancel_sent: set[str] = field(default_factory=set)
    _last: Tick | None = None

    def __post_init__(self) -> None:
        self.ledger = Ledger(self.cfg.contract)
        self.risk = RiskManager(self.cfg.risk)
        self.oms = OrderManager()

    def _log(self, ts: datetime, kind: str, text: str) -> None:
        self.events.append(Event(ts, kind, text))

    def day_pnl(self, last_price: float) -> float:
        """當日損益：當日已實現＋未實現。"""
        return (
            self.ledger.realized_twd - self._day_start_realized + self.ledger.open_pnl(last_price)
        )

    def _submit(self, req: OrderRequest, now: datetime) -> bool:
        exposure = self.ledger.position + self.oms.pending_signed_qty()
        decision = self.risk.check(req, exposure, now)
        if not decision.allowed:
            self._log(now, "reject", f"風控擋下 {req.side.name} {req.qty} 口：{decision.reason}")
            return False
        order_id = self.broker.submit(req)
        self.oms.add(order_id, req)
        if not is_reducing(exposure, req.signed_qty):
            self.risk.on_entry_sent()
        self._log(now, "order", f"{order_id} {req.side.name} {req.qty} 口 {req.reason}".rstrip())
        return True

    def _cancel(self, order_id: str, now: datetime, why: str) -> None:
        if order_id not in self._cancel_sent:
            self._cancel_sent.add(order_id)
            self.broker.cancel(order_id)
            self._log(now, "cancel", f"{order_id} 刪單：{why}")

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
        self._log(now, "halt", "緊急停止")
        self._flatten(now, "緊急停止")

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
                    now, "fill", f"{event.order_id} {fill.side.name} {fill.qty} 口 {fill.price}"
                )
                got_fill = True
        if got_fill:
            self.ledger.reconcile(self.broker.position())

        was_halted = self.risk.halt
        self.risk.update_pnl(self.day_pnl(tick.price), now)
        if self.risk.halt is not None and was_halted is None:
            self._log(
                now,
                "halt",
                f"停手（{self.risk.halt}）：當日損益 {self.day_pnl(tick.price):+.0f} 元",
            )

        for order in self.oms.stale(now, self.cfg.risk.order_timeout_s):
            if order.order_id in self._cancel_sent:
                continue
            exit_order = is_reducing(self.ledger.position, order.remaining_signed_qty)
            self._cancel(order.order_id, now, f"超過 {self.cfg.risk.order_timeout_s} 秒沒成交")
            if exit_order:
                # 平倉單卡住代表券商或連線有問題：停手，刪單回報後由 _flatten 重送市價平倉
                self.risk.kill()
                self._log(now, "halt", f"平倉單 {order.order_id} 逾時，緊急停止")

        if self.risk.halt is not None:
            self._flatten(now, f"停手（{self.risk.halt}）")
        elif now.time() >= self.cfg.risk.force_flat_time:
            self._flatten(now, "強制平倉時間")

        for req in self.strategy.on_tick(tick, self.ledger.position):
            self._submit(req, now)

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
