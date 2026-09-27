"""單筆成交決策（SPEC-004 §3 / SPEC-006 §1）。

回測引擎與 PaperBroker 共用的成交原語：給定一張單與當下約束（漲跌停、參與率上限、
可用現金），回傳成交股數、成本、未成交事件。兩邊共用同一函數是漂移偵測有意義的前提
（SPEC-006 AC-1「鎖住兩邊不分家」）。純函數。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from twquant.backtest.costs import CostConfig, TradeCost, trade_cost

_LOT = 1000  # 整張；零股回測由呼叫端傳 lot=1（D-153 §一 補充二）


@dataclass(frozen=True)
class FillDecision:
    filled_shares: int  # 實際成交股數（0 表未成交）
    cost: TradeCost | None  # filled_shares>0 時的成本
    unfilled: list[tuple[int, str]]  # [(股數, 原因)]；原因 ∈ limit_up/limit_down/participation/cash


def decide_fill(
    *,
    side: str,
    price: float,
    shares: int,
    limit_up: float | None,
    limit_down: float | None,
    adv: float | None,
    max_participation: float,
    available_cash: float,
    cost_cfg: CostConfig,
    lot: int = _LOT,
) -> FillDecision:
    """決定一張單成交多少。side='buy'|'sell'，shares 為正整數股（`lot` 的倍數；預設整張）。"""
    qty = shares
    if side == "buy" and limit_up is not None and price >= limit_up:
        return FillDecision(0, None, [(qty, "limit_up")])
    if side == "sell" and limit_down is not None and price <= limit_down:
        return FillDecision(0, None, [(qty, "limit_down")])

    unfilled: list[tuple[int, str]] = []
    if adv is not None and adv > 0:
        cap = int((max_participation * adv) // (price * lot)) * lot
        if qty > cap:
            unfilled.append((qty - cap, "participation"))
            qty = cap
    if qty <= 0:
        return FillDecision(0, None, unfilled or [(shares, "participation")])

    part = (price * qty) / adv if adv else 0.0
    if side == "buy":
        q = qty
        while q > 0:
            c = trade_cost(side="buy", price=price, shares=q, participation=part, cfg=cost_cfg)
            if price * q + c.total <= available_cash + 1e-6:
                break
            if lot == _LOT:  # 整張：逐張退，行為逐位元不變
                q -= lot
            else:  # 零股：一股一股退會迴圈上萬次，直接跳過短缺的股數（進位到 lot）
                short = price * q + c.total - available_cash
                q -= max(lot, math.ceil(short / (price * lot)) * lot)
        if q <= 0:
            return FillDecision(0, None, [*unfilled, (shares, "cash")])
        c = trade_cost(side="buy", price=price, shares=q, participation=part, cfg=cost_cfg)
        return FillDecision(q, c, unfilled)

    c = trade_cost(side="sell", price=price, shares=qty, participation=part, cfg=cost_cfg)
    return FillDecision(qty, c, unfilled)
