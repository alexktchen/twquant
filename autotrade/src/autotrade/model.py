"""資料型別：逐筆、委託、成交。時間一律 tz-aware Asia/Taipei。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")


def require_taipei(ts: datetime, what: str) -> datetime:
    """確認時間帶時區並轉成 Asia/Taipei；naive datetime 直接 raise。"""
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"{what} 是沒有時區的時間：{ts!r}（一律要 tz-aware Asia/Taipei）")
    return ts.astimezone(TAIPEI)


class Side(Enum):
    """買賣別；value 是部位變動的方向。"""

    BUY = 1
    SELL = -1


@dataclass(frozen=True)
class Tick:
    """一筆成交與當下的買賣價。price 單位是點，qty 單位是口。"""

    ts: datetime
    price: float
    qty: int
    bid: float | None = None
    ask: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "ts", require_taipei(self.ts, "Tick.ts"))
        if self.price <= 0 or self.qty <= 0:
            raise ValueError(f"Tick 價量要大於 0：price={self.price} qty={self.qty} ts={self.ts}")


@dataclass(frozen=True)
class OrderRequest:
    """策略想送的委託。limit_price 為 None 表示市價。"""

    ts: datetime
    symbol: str
    side: Side
    qty: int
    limit_price: float | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "ts", require_taipei(self.ts, "OrderRequest.ts"))
        if self.qty <= 0:
            raise ValueError(f"委託口數要大於 0：{self.qty}（{self.symbol} {self.side.name}）")

    @property
    def signed_qty(self) -> int:
        """帶方向的口數：買為正、賣為負。"""
        return self.side.value * self.qty


@dataclass(frozen=True)
class Fill:
    """一筆成交回報。"""

    ts: datetime
    symbol: str
    side: Side
    qty: int
    price: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "ts", require_taipei(self.ts, "Fill.ts"))
        if self.qty <= 0 or self.price <= 0:
            raise ValueError(f"成交價量要大於 0：price={self.price} qty={self.qty} {self.symbol}")

    @property
    def signed_qty(self) -> int:
        """帶方向的口數：買為正、賣為負。"""
        return self.side.value * self.qty
