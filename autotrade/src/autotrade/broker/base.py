"""券商介面與回報事件。模擬券商與元大轉接器都照這個介面。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from autotrade.model import Fill, OrderRequest, Tick


@dataclass(frozen=True)
class Ack:
    """券商收到委託。"""

    order_id: str
    ts: datetime


@dataclass(frozen=True)
class Filled:
    """成交（可能只是一部分口數）。"""

    order_id: str
    fill: Fill


@dataclass(frozen=True)
class Cancelled:
    """刪單成功。"""

    order_id: str
    ts: datetime


@dataclass(frozen=True)
class Rejected:
    """券商拒絕委託。"""

    order_id: str
    ts: datetime
    reason: str


BrokerEvent = Ack | Filled | Cancelled | Rejected


class Broker(Protocol):
    """券商。回報一律由 on_tick 回傳，呼叫端依序處理。"""

    def submit(self, req: OrderRequest) -> str:
        """送出委託，回傳委託編號。"""
        ...

    def cancel(self, order_id: str) -> None:
        """要求刪單；結果由之後的 Cancelled 或 Filled 回報決定。"""
        ...

    def on_tick(self, tick: Tick) -> list[BrokerEvent]:
        """餵一筆行情，回傳這段時間產生的回報。"""
        ...

    def position(self) -> int:
        """券商端的實際部位（帶方向）。"""
        ...

    def open_order_ids(self) -> frozenset[str]:
        """券商端還活著的委託編號（重連後比對用）。"""
        ...

    def resync(self) -> list[BrokerEvent]:
        """重連後補回斷線期間漏掉的回報（委託、成交、刪單），依發生順序。"""
        ...
