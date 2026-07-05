"""驗證協議工具（SPEC-004 §5）。

walk_forward（滾動樣本外）、deflated_sharpe（DSR，需試驗 SR 變異數，REVIEW M11）、
param_plateau（鄰域參數穩健性）、bootstrap_sharpe_ci（績效信賴帶）。純運算（不寫檔）。
"""

from __future__ import annotations

import datetime as dt
import math
import random
import statistics
from dataclasses import dataclass
from statistics import NormalDist
from typing import TYPE_CHECKING

from twquant.backtest.engine import BacktestResult, run_backtest

if TYPE_CHECKING:
    from twquant.backtest.costs import CostConfig
    from twquant.data.warehouse import Warehouse
    from twquant.strategy.config import StrategyConfig

_GAMMA = 0.5772156649015329  # Euler–Mascheroni
_ANNUAL = math.sqrt(252.0)


# ── Deflated Sharpe ────────────────────────────────────────────────
def expected_max_sharpe(trial_sharpes: list[float]) -> float:
    """N 次獨立試驗下、虛無假設的期望最大 SR（Bailey/López de Prado 2014）。

    需試驗 SR 的變異數（只給 trials 數無法算，REVIEW M11）。輸入與輸出同尺度。
    """
    n = len(trial_sharpes)
    if n < 2:
        return trial_sharpes[0] if trial_sharpes else 0.0
    std = math.sqrt(statistics.variance(trial_sharpes))
    if std == 0:
        return statistics.mean(trial_sharpes)
    nd = NormalDist()
    z1 = nd.inv_cdf(1.0 - 1.0 / n)
    z2 = nd.inv_cdf(1.0 - 1.0 / (n * math.e))
    return std * ((1.0 - _GAMMA) * z1 + _GAMMA * z2)


def deflated_sharpe(
    observed_sharpe: float,
    trial_sharpes: list[float],
    n_obs: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """DSR：觀察 SR 在扣除多重測試後仍顯著為正的機率（0~1）。

    輸入須為**每期（非年化）** SR，n_obs = 報酬期數。trial_sharpes 為同一 trial_scope
    下各試驗的 SR（供估期望最大 SR 與其變異數）。
    """
    if n_obs < 2:
        return 0.0
    sr0 = expected_max_sharpe(trial_sharpes)
    denom = math.sqrt(
        max(1.0 - skew * observed_sharpe + (kurtosis - 1.0) / 4.0 * observed_sharpe**2, 1e-12)
    )
    z = (observed_sharpe - sr0) * math.sqrt(n_obs - 1) / denom
    return NormalDist().cdf(z)


def deannualize_sharpe(annual_sharpe: float) -> float:
    """把年化 SR（×√252）換回每期 SR，供 deflated_sharpe 使用。"""
    return annual_sharpe / _ANNUAL


# ── Bootstrap 信賴帶 ───────────────────────────────────────────────
def bootstrap_sharpe_ci(
    daily_returns: list[float], n: int = 2000, seed: int = 0, ci: float = 0.95
) -> tuple[float, float]:
    """iid bootstrap 重抽日報酬 → 年化 Sharpe 的信賴區間 (lo, hi)。"""
    k = len(daily_returns)
    if k < 2:
        return (0.0, 0.0)
    rng = random.Random(seed)
    sharpes: list[float] = []
    for _ in range(n):
        sample = [daily_returns[rng.randrange(k)] for _ in range(k)]
        s = statistics.pstdev(sample)
        m = statistics.fmean(sample)
        sharpes.append(m / s * _ANNUAL if s > 0 else 0.0)
    sharpes.sort()
    lo_i = int((1.0 - ci) / 2.0 * n)
    hi_i = min(int((1.0 + ci) / 2.0 * n), n - 1)
    return (sharpes[lo_i], sharpes[hi_i])


def equity_returns(result: BacktestResult) -> list[float]:
    eq = [e.equity for e in result.equity]
    return [eq[i] / eq[i - 1] - 1.0 for i in range(1, len(eq)) if eq[i - 1] > 0]


# ── Walk-forward ───────────────────────────────────────────────────
@dataclass(frozen=True)
class WalkWindow:
    train_start: dt.date | None
    train_end: dt.date | None
    test_start: dt.date
    test_end: dt.date
    result: BacktestResult


@dataclass(frozen=True)
class WalkForwardResult:
    windows: list[WalkWindow]


def walk_forward(
    strategy_cfg: StrategyConfig,
    cost_cfg: CostConfig,
    warehouse: Warehouse,
    start: dt.date,
    end: dt.date,
    test_days: int,
    train_days: int = 0,
    initial_capital: float = 10_000_000.0,
) -> WalkForwardResult:
    """把 [start,end] 的開市日切成連續、互斥、無縫的樣本外測試窗，逐窗回測（AC-9）。"""
    from twquant.data import calendar as cal

    calendar = cal.load(warehouse)
    sessions = calendar.between(start, end)
    if test_days < 1 or not sessions:
        raise ValueError("test_days 需 ≥1 且區間內須有開市日")

    windows: list[WalkWindow] = []
    for i in range(0, len(sessions), test_days):
        chunk = sessions[i : i + test_days]
        if len(chunk) < 2:
            break
        t_start, t_end = chunk[0], chunk[-1]
        train_start = sessions[max(0, i - train_days)] if train_days and i > 0 else None
        train_end = sessions[i - 1] if train_days and i > 0 else None
        res = run_backtest(
            strategy_cfg, cost_cfg, t_start, t_end, warehouse, initial_capital=initial_capital
        )
        windows.append(
            WalkWindow(
                train_start=train_start,
                train_end=train_end,
                test_start=t_start,
                test_end=t_end,
                result=res,
            )
        )
    return WalkForwardResult(windows=windows)


# ── 參數高原 ───────────────────────────────────────────────────────
@dataclass(frozen=True)
class PlateauCell:
    params: dict[str, object]
    sharpe: float


def param_plateau(
    strategy_cfg: StrategyConfig,
    cost_cfg: CostConfig,
    warehouse: Warehouse,
    start: dt.date,
    end: dt.date,
    signal_index: int,
    param_grid: list[object],
) -> list[PlateauCell]:
    """對某個 signal 的因子 params[window] 掃描 grid，回傳各值的 Sharpe（找高原非尖峰）。"""
    cells: list[PlateauCell] = []
    for val in param_grid:
        signals = [s.model_copy() for s in strategy_cfg.signals]
        sig = signals[signal_index]
        new_params = dict(sig.params)
        new_params["window"] = val
        signals[signal_index] = sig.model_copy(update={"params": new_params})
        cfg = strategy_cfg.model_copy(update={"signals": signals})
        res = run_backtest(cfg, cost_cfg, start, end, warehouse)
        cells.append(PlateauCell(params={"window": val}, sharpe=res.metrics.sharpe))
    return cells
