"""模擬券商：送出後過了延遲的第一筆行情成交，市價單加滑價。自己記部位，供對帳。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from autotrade.broker.base import Ack, BrokerEvent, Cancelled, Filled
from autotrade.config import SimConfig
from autotrade.model import Fill, OrderRequest, Tick


@dataclass
class _Working:
    req: OrderRequest
    acked: bool = False


class SimBroker:
    """模擬券商。成交假設在 SimConfig。"""

    def __init__(self, cfg: SimConfig) -> None:
        self._latency = timedelta(seconds=cfg.latency_s)
        self._slippage = cfg.slippage_points
        self._working: dict[str, _Working] = {}
        self._cancel_requests: list[str] = []
        self._seq = 0
        self._position = 0

    def submit(self, req: OrderRequest) -> str:
        """送出委託，回傳委託編號。"""
        self._seq += 1
        order_id = f"S{self._seq:06d}"
        self._working[order_id] = _Working(req)
        return order_id

    def cancel(self, order_id: str) -> None:
        """要求刪單；下一筆行情時生效（已成交就刪不到）。"""
        self._cancel_requests.append(order_id)

    def _fill_price(self, req: OrderRequest, tick: Tick) -> float | None:
        if req.limit_price is None:
            return tick.price + req.side.value * self._slippage
        crossed = (tick.price - req.limit_price) * req.side.value <= 0
        return req.limit_price if crossed else None

    def on_tick(self, tick: Tick) -> list[BrokerEvent]:
        """餵一筆行情，回傳回報。"""
        events: list[BrokerEvent] = []
        for order_id in self._cancel_requests:
            if self._working.pop(order_id, None) is not None:
                events.append(Cancelled(order_id, tick.ts))
        self._cancel_requests.clear()
        for order_id, work in list(self._working.items()):
            if tick.ts < work.req.ts + self._latency:
                continue
            if not work.acked:
                work.acked = True
                events.append(Ack(order_id, tick.ts))
            price = self._fill_price(work.req, tick)
            if price is None:
                continue
            fill = Fill(
                ts=tick.ts,
                symbol=work.req.symbol,
                side=work.req.side,
                qty=work.req.qty,
                price=price,
            )
            self._position += fill.signed_qty
            del self._working[order_id]
            events.append(Filled(order_id, fill))
        return events

    def position(self) -> int:
        """模擬券商端的部位。"""
        return self._position
