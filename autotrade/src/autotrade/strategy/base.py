"""策略介面。策略只看行情與自己的成交，不碰券商與帳務。"""

from __future__ import annotations

from typing import Protocol

from autotrade.model import Fill, OrderRequest, Tick


class Strategy(Protocol):
    """策略。"""

    def on_tick(self, tick: Tick, position: int) -> list[OrderRequest]:
        """餵一筆行情與目前實際部位，回傳想送的委託（可以是空的）。"""
        ...

    def on_fill(self, fill: Fill) -> None:
        """自己的委託成交了。"""
        ...


class NullStrategy:
    """不下單的策略：只用來跑行情、檢查系統接線。"""

    def on_tick(self, tick: Tick, position: int) -> list[OrderRequest]:
        """永遠不送單。"""
        return []

    def on_fill(self, fill: Fill) -> None:
        """不會有成交。"""
        raise RuntimeError(f"NullStrategy 不送單，卻收到成交：{fill!r}")
