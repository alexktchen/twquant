"""容量分析（T-029 / STRATEGY §5a）。純函數。

給定目標權重與各股 20 日 ADV，估策略在參與率上限下的資金容量：某股票單次再平衡的
成交金額 = weight × turnover × AUM 不得超過 max_participation × ADV。最緊的那檔決定容量。
第一版假設進場即滿倉（turnover=1）；binding_stock 指出瓶頸標的。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapacityResult:
    capacity_twd: float  # 估計資金容量上限
    binding_stock: str  # 最先觸及參與率上限的瓶頸標的
    per_stock_twd: dict[str, float]  # 各股各自的容量上限


def estimate_capacity(
    weights: dict[str, float],
    adv_twd: dict[str, float],
    max_participation: float = 0.05,
    turnover: float = 1.0,
) -> CapacityResult:
    """容量上限 = min_i (max_participation × ADV_i) / (weight_i × turnover)。"""
    caps: dict[str, float] = {}
    for sid, w in weights.items():
        adv = adv_twd.get(sid, 0.0)
        if w > 0 and adv > 0:
            caps[sid] = max_participation * adv / (w * turnover)
    if not caps:
        return CapacityResult(0.0, "", {})
    binding = min(caps, key=lambda s: caps[s])
    return CapacityResult(capacity_twd=caps[binding], binding_stock=binding, per_stock_twd=caps)
