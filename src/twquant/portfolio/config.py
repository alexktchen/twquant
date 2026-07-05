"""投組配置（SPEC-005 §1，configs/portfolio.yaml）。"""

from __future__ import annotations

from pathlib import Path

from pydantic import model_validator

from twquant.config import BaseConfig, load_config


class SleeveConfig(BaseConfig):
    strategy: str
    risk_budget: float


class VolEstimator(BaseConfig):
    window_days: int = 60
    method: str = "ewma"
    halflife: int = 20


class VolConditional(BaseConfig):
    percentile_window_days: int = 252
    reduce_above_pct: float = 90.0


class VolTargetConfig(BaseConfig):
    enabled: bool = False
    annual_vol: float = 0.15
    estimator: VolEstimator = VolEstimator()
    conditional: VolConditional = VolConditional()
    max_leverage: float = 1.0


class LimitsConfig(BaseConfig):
    max_weight_per_stock: float = 0.05
    max_weight_per_industry: float = 0.25
    max_daily_turnover: float = 0.30


class PortfolioConfig(BaseConfig):
    sleeves: list[SleeveConfig]
    vol_target: VolTargetConfig = VolTargetConfig()
    limits: LimitsConfig = LimitsConfig()

    @model_validator(mode="after")
    def _has_sleeves(self) -> PortfolioConfig:
        if not self.sleeves:
            raise ValueError("sleeves 不可為空")
        return self

    @property
    def budgets(self) -> dict[str, float]:
        return {s.strategy: s.risk_budget for s in self.sleeves}


class RiskConfig(BaseConfig):
    """kill-switch 觸發閾值（configs/risk.yaml，D-009 初始值）。"""

    daily_loss_pct: float = 0.03  # 單日虧損 > 此 → NO_NEW_ENTRY
    drawdown_reduce_pct: float = 0.15  # 回撤 > 此 → REDUCED
    drawdown_reduce_recover_pct: float = 0.10  # 回撤收斂 < 此 → 回 NORMAL（遲滯）
    drawdown_halt_pct: float = 0.20  # 回撤 > 此 → HALTED（人工恢復）
    reduced_scale: float = 0.5


def load_portfolio(path: str | Path) -> PortfolioConfig:
    return load_config(path, PortfolioConfig)
