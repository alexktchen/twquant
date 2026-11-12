"""委託管理：每張委託的狀態機。不合法的狀態轉移直接 raise。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from autotrade.broker.base import Ack, BrokerEvent, Cancelled, Filled, Rejected
from autotrade.model import Fill, OrderRequest


class OrderState(Enum):
    """委託狀態。"""

    SUBMITTED = "submitted"
    ACKED = "acked"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


TERMINAL = {OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED}


class OrderStateError(RuntimeError):
    """回報對不上委託狀態。"""


@dataclass
class Order:
    """一張委託。"""

    order_id: str
    req: OrderRequest
    state: OrderState = OrderState.SUBMITTED
    filled_qty: int = 0

    @property
    def remaining_signed_qty(self) -> int:
        """還沒成交的口數（帶方向）。"""
        return self.req.side.value * (self.req.qty - self.filled_qty)


class OrderManager:
    """記所有委託並套用券商回報。"""

    def __init__(self) -> None:
        self.orders: dict[str, Order] = {}

    def add(self, order_id: str, req: OrderRequest) -> Order:
        """登記一張剛送出的委託。"""
        if order_id in self.orders:
            raise OrderStateError(f"委託編號重複：{order_id}")
        order = Order(order_id, req)
        self.orders[order_id] = order
        return order

    def apply(self, event: BrokerEvent) -> Fill | None:
        """套用一筆回報；是成交就回傳 Fill。"""
        order = self.orders.get(event.order_id)
        if order is None:
            raise OrderStateError(f"收到不認得的委託回報：{event!r}")
        if order.state in TERMINAL:
            raise OrderStateError(
                f"委託 {order.order_id} 已是 {order.state.value}，又收到 {event!r}"
            )
        if isinstance(event, Ack):
            order.state = OrderState.ACKED
            return None
        if isinstance(event, Cancelled):
            order.state = OrderState.CANCELLED
            return None
        if isinstance(event, Rejected):
            order.state = OrderState.REJECTED
            return None
        if isinstance(event, Filled):
            fill = event.fill
            if fill.side != order.req.side or fill.symbol != order.req.symbol:
                raise OrderStateError(f"成交 {fill!r} 跟委託 {order.req!r} 的商品或買賣別不同")
            order.filled_qty += fill.qty
            if order.filled_qty > order.req.qty:
                raise OrderStateError(
                    f"委託 {order.order_id} 成交 {order.filled_qty} 口，超過委託的 {order.req.qty} 口"
                )
            order.state = (
                OrderState.FILLED if order.filled_qty == order.req.qty else OrderState.ACKED
            )
            return fill
        raise OrderStateError(f"不認得的回報型別：{event!r}")

    def working(self) -> list[Order]:
        """還活著的委託。"""
        return [o for o in self.orders.values() if o.state not in TERMINAL]

    def pending_signed_qty(self) -> int:
        """所有活著的委託還沒成交的口數合計（帶方向）。"""
        return sum(o.remaining_signed_qty for o in self.working())

    def stale(self, now: datetime, timeout_s: float) -> list[Order]:
        """送出超過 timeout_s 還沒結束的委託。"""
        return [o for o in self.working() if (now - o.req.ts).total_seconds() > timeout_s]
