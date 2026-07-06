"""漂移偵測（T-024 / LOOP §3）。純函數。

漂移 = 「PaperBroker 影子損益 vs 實盤損益」日度差的滾動 N 日年化標準差（tracking
error）。> 門檻（預設 3%）→ 寫 REDUCED 旗標（責任鏈：monitoring 只寫旗標，SPEC-005
狀態機讀後自動轉 REDUCED）；連 5 日 → HALTED 待裁決。此模組只算指標與判定，不寫狀態。
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

_ANNUAL = math.sqrt(252.0)


@dataclass(frozen=True)
class DriftResult:
    tracking_error: float  # 年化 tracking error
    breached: bool  # 是否超過門檻
    n: int  # 用於計算的日數


def tracking_error(
    paper_returns: list[float], live_returns: list[float], window: int = 20
) -> float:
    """近 window 日「影子 − 實盤」日報酬差的年化標準差。不足 2 日 → 0。"""
    diffs = [p - r for p, r in zip(paper_returns, live_returns, strict=False)]
    recent = diffs[-window:]
    if len(recent) < 2:
        return 0.0
    return statistics.pstdev(recent) * _ANNUAL


def drift_check(
    paper_returns: list[float],
    live_returns: list[float],
    threshold: float = 0.03,
    window: int = 20,
) -> DriftResult:
    """判定漂移是否超標（tracking error > threshold）。"""
    te = tracking_error(paper_returns, live_returns, window)
    n = min(len(paper_returns), len(live_returns))
    return DriftResult(tracking_error=te, breached=te > threshold, n=n)
