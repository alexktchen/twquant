"""成本模型與台股檔位/漲跌停（SPEC-004 §2/§3）。

價格相依：半價差 = max(0.5×tick(price)/price, half_spread_bps)（REVIEW M1，低價股
tick 造成的最小價差遠大於固定 bps）。漲跌停以 prices_daily 旗標為準，缺失才用檔位
精算 proxy（前收×1.10 向下取檔 / ×0.90 向上取檔，非固定比率，REVIEW H1）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from pydantic import Field

from twquant.config import BaseConfig

# 檔位表（SPEC-004 §3）：價格區間 → 最小跳動
_TICKS: list[tuple[float, float]] = [
    (10.0, 0.01),
    (50.0, 0.05),
    (100.0, 0.1),
    (500.0, 0.5),
    (1000.0, 1.0),
]
_TICK_TOP = 5.0  # ≥1000


def tick(price: float) -> float:
    """該價位的最小跳動單位。"""
    for upper, t in _TICKS:
        if price < upper:
            return t
    return _TICK_TOP


def floor_to_tick(price: float) -> float:
    t = tick(price)
    return math.floor(price / t + 1e-9) * t


def ceil_to_tick(price: float) -> float:
    t = tick(price)
    return math.ceil(price / t - 1e-9) * t


def limit_up_proxy(prev_close: float) -> float:
    """漲停價 proxy = 前收 × 1.10 向下取至檔位。"""
    return floor_to_tick(prev_close * 1.10)


def limit_down_proxy(prev_close: float) -> float:
    """跌停價 proxy = 前收 × 0.90 向上取至檔位。"""
    return ceil_to_tick(prev_close * 0.90)


class SlippageConfig(BaseConfig):
    model: str = "adv_impact"
    half_spread_bps: float = 5.0
    k_bps: float = 25.0


class DataGuardConfig(BaseConfig):
    """D-029 資料防衛：非漲跌停可解釋的相鄰列跳價（停牌復牌/減資/資料錯誤）不入損益。"""

    jump_ratio: float = 0.35


class CostConfig(BaseConfig):
    """交易成本設定（configs/costs.yaml，SPEC-004 §2）。"""

    commission_rate: float = 0.001425
    commission_discount: float = 0.35
    commission_min_twd: float = 20.0
    tax_rate_sell: float = 0.003
    cost_scale: float = Field(default=1.0, ge=0.0)
    slippage: SlippageConfig = SlippageConfig()
    data_guard: DataGuardConfig = DataGuardConfig()


@dataclass(frozen=True)
class TradeCost:
    commission: float
    tax: float
    slippage: float

    @property
    def total(self) -> float:
        return self.commission + self.tax + self.slippage


def _half_spread_frac(price: float, cfg: CostConfig) -> float:
    """價格相依半價差（小數）= max(0.5×tick/price, half_spread_bps)。"""
    tick_frac = 0.5 * tick(price) / price
    return max(tick_frac, cfg.slippage.half_spread_bps / 1e4)


def trade_cost(
    *, side: str, price: float, shares: int, participation: float, cfg: CostConfig
) -> TradeCost:
    """單筆成交成本。side='buy'|'sell'；participation = 成交金額 / 20 日 ADV。"""
    notional = price * shares
    commission = max(
        notional * cfg.commission_rate * cfg.commission_discount, cfg.commission_min_twd
    )
    tax = notional * cfg.tax_rate_sell if side == "sell" else 0.0
    part = max(participation, 0.0)
    slip_frac = _half_spread_frac(price, cfg) + cfg.slippage.k_bps / 1e4 * math.sqrt(part)
    slippage = notional * slip_frac
    scale = cfg.cost_scale
    return TradeCost(
        commission=commission * scale,
        tax=tax * scale,
        slippage=slippage * scale,
    )
