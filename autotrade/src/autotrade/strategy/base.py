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
        """有成交（策略自己的委託，或系統強制平倉的委託）。"""
        ...

    def on_reject(self, req: OrderRequest, reason: str) -> None:
        """策略送的委託沒有送出去或被刪掉了（風控擋下、券商拒絕、逾時刪單）。"""
        ...


class NullStrategy:
    """不下單的策略：只用來跑行情、檢查系統接線。"""

    def on_tick(self, tick: Tick, position: int) -> list[OrderRequest]:
        """永遠不送單。"""
        return []

    def on_fill(self, fill: Fill) -> None:
        """不會有成交。"""
        raise RuntimeError(f"NullStrategy 不送單，卻收到成交：{fill!r}")

    def on_reject(self, req: OrderRequest, reason: str) -> None:
        """不會有委託。"""
        raise RuntimeError(f"NullStrategy 不送單，卻收到退單：{req!r}")
