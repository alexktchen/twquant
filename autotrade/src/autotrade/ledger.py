"""帳務：由成交算部位、均價、損益與成本，並跟券商回報的部位對帳。"""

from __future__ import annotations

from dataclasses import dataclass, field

from autotrade.config import ContractConfig
from autotrade.model import Fill


class ReconcileError(RuntimeError):
    """帳上部位跟券商回報的不一樣。"""


@dataclass
class Ledger:
    """單一商品的帳。position 帶方向（多為正）；金額單位是元。"""

    contract: ContractConfig
    position: int = 0
    avg_price: float = 0.0
    realized_twd: float = 0.0
    costs_twd: float = 0.0
    fills: list[Fill] = field(default_factory=list)

    def cost_of(self, fill: Fill) -> float:
        """一筆成交的成本：手續費＋期交稅（四捨五入到元）。"""
        tax = round(fill.price * self.contract.point_value * self.contract.tax_rate) * fill.qty
        return self.contract.commission_twd * fill.qty + tax

    def apply(self, fill: Fill) -> None:
        """記一筆成交。"""
        if fill.symbol != self.contract.symbol:
            raise ValueError(f"成交的商品 {fill.symbol} 不是這本帳的 {self.contract.symbol}")
        cost = self.cost_of(fill)
        self.costs_twd += cost
        self.realized_twd -= cost
        signed = fill.signed_qty
        if self.position == 0 or (self.position > 0) == (signed > 0):
            total = abs(self.position) + fill.qty
            self.avg_price = (self.avg_price * abs(self.position) + fill.price * fill.qty) / total
            self.position += signed
        else:
            closing = min(fill.qty, abs(self.position))
            direction = 1 if self.position > 0 else -1
            self.realized_twd += (
                (fill.price - self.avg_price) * direction * closing * self.contract.point_value
            )
            self.position += signed
            if self.position == 0:
                self.avg_price = 0.0
            elif (self.position > 0) != (direction > 0):
                self.avg_price = fill.price  # 反手：剩下的口數是新倉
        self.fills.append(fill)

    def open_pnl(self, last_price: float) -> float:
        """用最新價算未實現損益（不含平倉成本）。"""
        return (last_price - self.avg_price) * self.position * self.contract.point_value

    def adopt(self, broker_position: int, price: float) -> None:
        """部位對不上時，把帳改成券商回報的部位（券商是準的）。均價用當下價格，已實現損益不動。"""
        self.position = broker_position
        self.avg_price = price if broker_position != 0 else 0.0

    def reconcile(self, broker_position: int) -> None:
        """跟券商回報的部位對帳，不一樣就 raise。"""
        if broker_position != self.position:
            raise ReconcileError(
                f"{self.contract.symbol} 部位對不上：帳上 {self.position} 口，"
                f"券商回報 {broker_position} 口"
            )
