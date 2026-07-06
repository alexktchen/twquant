"""T-029 容量分析 + T-024 漂移偵測。"""

from __future__ import annotations

import pytest

from twquant.backtest.capacity import estimate_capacity
from twquant.monitoring.drift import drift_check, tracking_error


# ── 容量 ────────────────────────────────────────────────────────────
def test_capacity_binding_stock() -> None:
    # A：weight 0.5、ADV 1e8 → cap 0.05×1e8/0.5 = 1e7
    # B：weight 0.5、ADV 1e9 → cap 0.05×1e9/0.5 = 1e8
    res = estimate_capacity({"A": 0.5, "B": 0.5}, {"A": 1e8, "B": 1e9})
    assert res.binding_stock == "A"  # 低 ADV 者先觸頂
    assert res.capacity_twd == pytest.approx(1e7)
    assert res.per_stock_twd["B"] == pytest.approx(1e8)


def test_capacity_empty() -> None:
    res = estimate_capacity({}, {})
    assert res.capacity_twd == 0.0 and res.binding_stock == ""


# ── 漂移 ────────────────────────────────────────────────────────────
def test_tracking_error_zero_when_identical() -> None:
    r = [0.01, -0.02, 0.005, 0.0, 0.01]
    assert tracking_error(r, r) == pytest.approx(0.0)


def test_drift_breach() -> None:
    paper = [0.01] * 20
    live = [0.01 if i % 2 == 0 else -0.03 for i in range(20)]  # 大幅偏離
    res = drift_check(paper, live, threshold=0.03)
    assert res.tracking_error > 0.03
    assert res.breached is True
    assert res.n == 20


def test_drift_within_threshold() -> None:
    paper = [0.01] * 20
    live = [0.0102] * 20  # 幾乎貼合
    res = drift_check(paper, live, threshold=0.03)
    assert res.breached is False
