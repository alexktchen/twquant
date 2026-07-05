"""投組與風控層（SPEC-005）：多 sleeve 組合、限額、條件式波動目標、kill-switch。

輸入各策略目標權重，輸出帳戶層級最終目標部位。**波動縮放只在此層做**（D-004，
strategy overlay 不縮，避免雙重縮減）。純運算層（不寫檔）；狀態持久化在 twquant.state。
"""

from twquant.portfolio.config import PortfolioConfig, load_portfolio
from twquant.portfolio.core import (
    PortfolioState,
    SleeveSignal,
    TargetBook,
    build_book,
    evaluate_killswitch,
)

__all__ = [
    "PortfolioConfig",
    "PortfolioState",
    "SleeveSignal",
    "TargetBook",
    "build_book",
    "evaluate_killswitch",
    "load_portfolio",
]
