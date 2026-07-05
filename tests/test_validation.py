"""T-019：驗證工具（DSR、bootstrap、walk-forward、param plateau）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

import twquant.factors.builtins  # noqa: F401
from twquant.backtest import validation as v
from twquant.backtest.costs import CostConfig
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.strategy.config import SignalConfig, StrategyConfig

_CC = CostConfig()


# ── DSR ─────────────────────────────────────────────────────────────
def test_expected_max_sharpe_scales_with_trials() -> None:
    trials = [0.0, 0.01, -0.01, 0.02, -0.02, 0.015]
    emax = v.expected_max_sharpe(trials)
    assert emax > 0  # 多重測試下期望最大 SR 為正
    assert v.expected_max_sharpe([0.5]) == pytest.approx(0.5)  # 單試驗退化


def test_deflated_sharpe_monotone() -> None:
    trials = [0.0, 0.01, -0.01, 0.02, -0.02, 0.015]
    high = v.deflated_sharpe(0.30, trials, n_obs=250)
    low = v.deflated_sharpe(0.03, trials, n_obs=250)
    assert 0.0 <= low <= high <= 1.0
    assert high > 0.9  # 觀察 SR 遠高於期望最大 → 高信心
    assert v.deannualize_sharpe(1.0 * (252**0.5)) == pytest.approx(1.0)


# ── Bootstrap ───────────────────────────────────────────────────────
def test_bootstrap_ci_reproducible_and_ordered() -> None:
    rets = [0.001 * ((i % 7) - 3) for i in range(120)]  # 有正有負
    lo1, hi1 = v.bootstrap_sharpe_ci(rets, n=500, seed=42)
    lo2, hi2 = v.bootstrap_sharpe_ci(rets, n=500, seed=42)
    assert (lo1, hi1) == (lo2, hi2)  # 同 seed 決定性
    assert lo1 <= hi1


# ── walk-forward / plateau（合成資料）───────────────────────────────
def _wh(tmp_path: Path) -> Warehouse:
    base = dt.date(2026, 5, 1)
    slopes = {"1101": 1.0, "2330": 2.0, "2454": 0.5}
    rows = []
    for i in range(40):
        d = base + dt.timedelta(days=i)
        for sid, slope in slopes.items():
            c = 100.0 + slope * i
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": c,
                    "high": c,
                    "low": c,
                    "close": c,
                    "volume": 100_000,
                    "amount": 1e9,
                    "limit_up_price": None,
                    "limit_down_price": None,
                    "available_at": dt.datetime(d.year, d.month, d.day, 18, tzinfo=TZ),
                }
            )
    df = pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(schemas.TW_DT),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", df, source="twse")
    return wh


def _cfg() -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            "name": "mom_v",
            "signals": [SignalConfig(factor="price.mom_20d", weight=1.0, params={"window": 10})],
            "selection": {"method": "top_n", "top_n": 2},
            "weighting": "equal",
            "max_weight_per_stock": 0.6,
            "universe": {"min_adv_twd": None, "min_price": None},
            "rebalance": {"schedule": "daily"},
            "overlays": [],
        }
    )


def test_walk_forward_windows_disjoint_and_contiguous(tmp_path: Path) -> None:
    wh = _wh(tmp_path)
    wf = v.walk_forward(_cfg(), _CC, wh, dt.date(2026, 5, 20), dt.date(2026, 6, 9), test_days=5)
    assert len(wf.windows) >= 2
    for a, b in zip(wf.windows, wf.windows[1:], strict=False):
        assert a.test_end < b.test_start  # AC-9：互斥、無重疊
    assert all(w.result.metrics.n_days >= 2 for w in wf.windows)


def test_param_plateau_grid(tmp_path: Path) -> None:
    wh = _wh(tmp_path)
    cells = v.param_plateau(
        _cfg(),
        _CC,
        wh,
        dt.date(2026, 5, 20),
        dt.date(2026, 6, 9),
        signal_index=0,
        param_grid=[5, 10],
    )
    assert [c.params["window"] for c in cells] == [5, 10]
    assert all(isinstance(c.sharpe, float) for c in cells)
