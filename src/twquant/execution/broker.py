"""券商協定與 PaperBroker（SPEC-006 §1）。

PaperBroker 成交呼叫 backtest.fill.decide_fill——與回測同一套成交邏輯（AC-1）。
idempotency：同一 idempotency_key 重送回既有 ack（AC-2）。HALTED → place raise（AC-6）。
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

from twquant.backtest.fill import decide_fill
from twquant.errors import ExecutionError

if TYPE_CHECKING:
    from twquant.backtest.costs import CostConfig

_LOT = 1000


@dataclass(frozen=True)
class Order:
    stock_id: str
    side: Literal["buy", "sell"]
    shares: int  # 1000 倍數
    limit_price: float
    idempotency_key: str
    order_type: Literal["limit"] = "limit"
    tif: Literal["ROD"] = "ROD"


@dataclass(frozen=True)
class OrderAck:
    order_id: str
    idempotency_key: str
    stock_id: str
    side: str
    filled_shares: int
    avg_price: float
    commission: float
    tax: float
    slippage: float
    unfilled: list[tuple[int, str]]
    status: Literal["filled", "partial", "rejected"]


@dataclass(frozen=True)
class Position:
    stock_id: str
    shares: int


@dataclass(frozen=True)
class Fill:
    date: dt.date
    stock_id: str
    side: str
    shares: int
    price: float
    commission: float
    tax: float
    slippage: float


def make_order(
    day: dt.date, stock_id: str, side: str, shares: int, limit_price: float, seq: int = 0
) -> Order:
    """組出帶決定性 idempotency_key 的訂單（date+stock+side+seq 的 hash）。"""
    key = hashlib.sha256(f"{day}|{stock_id}|{side}|{seq}".encode()).hexdigest()[:16]
    return Order(
        stock_id=stock_id,
        side=side,  # type: ignore[arg-type]
        shares=shares,
        limit_price=limit_price,
        idempotency_key=key,
    )


class Broker(Protocol):
    name: str

    def place(self, order: Order) -> OrderAck: ...
    def positions(self) -> list[Position]: ...
    def cash(self) -> float: ...
    def fills(self, day: dt.date) -> list[Fill]: ...


@dataclass
class PaperBroker:
    """紙上券商：成交用 decide_fill（與回測同源）。"""

    cost_cfg: CostConfig
    _cash: float
    day: dt.date = dt.date(1970, 1, 1)  # 當前交易日（fills 依此標記）
    market: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )  # sid -> {adv, limit_up, limit_down}
    max_participation: float = 0.05
    status: str = "NORMAL"
    name: str = "paper"
    _holdings: dict[str, int] = field(default_factory=dict)
    _fills: list[Fill] = field(default_factory=list)
    _acks: dict[str, OrderAck] = field(default_factory=dict)

    def place(self, order: Order) -> OrderAck:
        if self.status == "HALTED":
            raise ExecutionError("kill-switch HALTED：拒絕下單")
        if order.idempotency_key in self._acks:
            return self._acks[order.idempotency_key]  # 重送防重複（AC-2）

        m = self.market.get(order.stock_id, {})
        d = decide_fill(
            side=order.side,
            price=order.limit_price,
            shares=order.shares,
            limit_up=m.get("limit_up"),
            limit_down=m.get("limit_down"),
            adv=m.get("adv"),
            max_participation=self.max_participation,
            available_cash=self._cash,
            cost_cfg=self.cost_cfg,
        )
        filled = d.filled_shares
        price = order.limit_price
        if filled > 0 and d.cost is not None:
            c = d.cost
            if order.side == "buy":
                self._cash -= price * filled + c.total
                self._holdings[order.stock_id] = self._holdings.get(order.stock_id, 0) + filled
            else:
                self._cash += price * filled - c.total
                new = self._holdings.get(order.stock_id, 0) - filled
                if new <= 0:
                    self._holdings.pop(order.stock_id, None)
                else:
                    self._holdings[order.stock_id] = new
            self._fills.append(
                Fill(
                    date=self.day,
                    stock_id=order.stock_id,
                    side=order.side,
                    shares=filled,
                    price=price,
                    commission=c.commission,
                    tax=c.tax,
                    slippage=c.slippage,
                )
            )
            status: Literal["filled", "partial", "rejected"] = (
                "filled" if not d.unfilled else "partial"
            )
            comm, tax, slip = c.commission, c.tax, c.slippage
        else:
            status, comm, tax, slip = "rejected", 0.0, 0.0, 0.0

        ack = OrderAck(
            order_id=order.idempotency_key,
            idempotency_key=order.idempotency_key,
            stock_id=order.stock_id,
            side=order.side,
            filled_shares=filled,
            avg_price=price,
            commission=comm,
            tax=tax,
            slippage=slip,
            unfilled=d.unfilled,
            status=status,
        )
        self._acks[order.idempotency_key] = ack
        return ack

    def positions(self) -> list[Position]:
        return [Position(stock_id=s, shares=n) for s, n in sorted(self._holdings.items())]

    def cash(self) -> float:
        return self._cash

    def fills(self, day: dt.date) -> list[Fill]:
        return [f for f in self._fills if f.date == day]
