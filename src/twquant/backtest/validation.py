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


@dataclass(frozen=True)
class PairedBlockStats:
    """配對日報酬差的區塊統計（T-127／SPEC-013 §3.7，D-061／R1）。"""

    mean: float  # 日均配對差
    block_se: float | None  # 月度區塊標準誤；只有一個區塊時為 None
    t_stat: float | None
    n_days: int
    n_blocks: int

    @property
    def indistinguishable(self) -> bool:
        """|t| < 2 或無從估計不確定性 → 「未能區分」。

        這是 SPEC-013 §3.7 明列的**合法結果**，由函數自己表態而不是留給讀者
        看著點估計自行判斷——後者正是「事後找故事」的入口。
        """
        return self.t_stat is None or abs(self.t_stat) < 2.0


def paired_block_stats(days: list[dt.date], diffs: list[float]) -> PairedBlockStats:
    """配對日報酬差的均值與**月度區塊**標準誤。

    為什麼不用日頻標準誤：本專案的持股期是 20 個交易日，日報酬高度自相關，
    把每日配對差當獨立樣本會嚴重高估顯著性（D-061／R1）。以「曆月」為區塊，
    先算每月均值再對月均值取標準誤，讓月內的自相關不再假裝成獨立資訊。

    只有一個區塊時 `block_se` 回傳 **None 而非 0**：0 會被下游讀成「極度顯著」，
    是最糟的失敗模式（黃金規則 5 的精神）。
    """
    if len(days) != len(diffs):
        raise ValueError(f"days 與 diffs 長度不符：{len(days)} vs {len(diffs)}")
    if not diffs:
        return PairedBlockStats(0.0, None, None, 0, 0)

    blocks: dict[tuple[int, int], list[float]] = {}
    for d, x in zip(days, diffs, strict=True):
        blocks.setdefault((d.year, d.month), []).append(x)
    means = [sum(v) / len(v) for _, v in sorted(blocks.items())]
    mean = sum(diffs) / len(diffs)
    if len(means) < 2:
        return PairedBlockStats(mean, None, None, len(diffs), len(means))

    m = sum(means) / len(means)
    var = sum((x - m) ** 2 for x in means) / (len(means) - 1)
    se = (var / len(means)) ** 0.5
    t = mean / se if se > 0 else None
    return PairedBlockStats(mean, se, t, len(diffs), len(means))
