"""績效指標（SPEC-004 §4）。純函數：吃日頻權益序列與逐筆 ledger。"""

from __future__ import annotations

import math
from dataclasses import dataclass

_TRADING_DAYS = 252.0


@dataclass(frozen=True)
class Metrics:
    cagr: float
    sharpe: float
    sortino: float
    mdd: float
    calmar: float
    turnover: float  # 年化單邊
    win_rate: float
    n_days: int


def _returns(equity: list[float]) -> list[float]:
    return [equity[i] / equity[i - 1] - 1.0 for i in range(1, len(equity)) if equity[i - 1] > 0]


def _std(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var)


def _max_drawdown(equity: list[float]) -> float:
    peak = equity[0]
    mdd = 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1.0)
    return mdd


def compute_metrics(equity: list[float], turnover_annualized: float) -> Metrics:
    """由權益曲線算指標。turnover 由引擎累計（Σ|Δw|/2 年化）傳入。"""
    if len(equity) < 2 or equity[0] <= 0:
        return Metrics(0.0, 0.0, 0.0, 0.0, 0.0, turnover_annualized, 0.0, len(equity))
    rets = _returns(equity)
    n = len(rets)
    mean = sum(rets) / n if n else 0.0
    std = _std(rets)
    downside = [r for r in rets if r < 0]
    dstd = _std(downside) if len(downside) > 1 else 0.0

    years = n / _TRADING_DAYS
    cagr = (equity[-1] / equity[0]) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    sharpe = mean / std * math.sqrt(_TRADING_DAYS) if std > 0 else 0.0
    sortino = mean / dstd * math.sqrt(_TRADING_DAYS) if dstd > 0 else 0.0
    mdd = _max_drawdown(equity)
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0
    win_rate = sum(1 for r in rets if r > 0) / n if n else 0.0
    return Metrics(
        cagr=cagr,
        sharpe=sharpe,
        sortino=sortino,
        mdd=mdd,
        calmar=calmar,
        turnover=turnover_annualized,
        win_rate=win_rate,
        n_days=len(equity),
    )
