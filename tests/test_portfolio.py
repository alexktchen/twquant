"""T-020：投組層（sleeve 組合/歸因、限額、條件式波動、kill-switch、狀態持久化）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

from twquant.portfolio import core
from twquant.portfolio.config import PortfolioConfig, RiskConfig, load_portfolio
from twquant.portfolio.core import (
    PortfolioState,
    SleeveSignal,
    build_book,
    evaluate_killswitch,
)
from twquant.state import load_state, save_state

_RISK = RiskConfig()
_NOW = dt.datetime(2026, 6, 30, 20, 0)


def _cfg(**over: object) -> PortfolioConfig:
    base: dict[str, object] = {
        "sleeves": [
            {"strategy": "A", "risk_budget": 0.5},
            {"strategy": "B", "risk_budget": 0.5},
        ],
        "vol_target": {"enabled": False},
        "limits": {"max_weight_per_stock": 0.05, "max_daily_turnover": 0.30},
    }
    base.update(over)
    return PortfolioConfig.model_validate(base)


def _sleeve(name: str, w: dict[str, float]) -> SleeveSignal:
    df = pl.DataFrame({"stock_id": list(w), "target_weight": [w[s] for s in w]})
    return SleeveSignal(strategy=name, date=dt.date(2026, 6, 30), weights=df)


# ── 組合 + 歸因（AC-1）──────────────────────────────────────────────
def test_combine_attribution() -> None:
    signals = [_sleeve("A", {"2330": 0.5, "1101": 0.5}), _sleeve("B", {"2330": 1.0})]
    acct, attr = core.combine_sleeves(signals, {"A": 0.5, "B": 0.5})
    # 2330 = 0.5×0.5 + 1.0×0.5 = 0.75；1101 = 0.5×0.5 = 0.25
    assert acct["2330"] == pytest.approx(0.75)
    assert acct["1101"] == pytest.approx(0.25)
    assert sum(attr["2330"].values()) == pytest.approx(acct["2330"])  # 歸因加總 = 帳戶權重


# ── 周轉率上限（AC-4）───────────────────────────────────────────────
def test_turnover_cap() -> None:
    # 目標全新 80% 曝險、現有空手 → 周轉 40%；上限 30% → 壓到 30%
    target = {"a": 0.4, "b": 0.4}
    capped = core.turnover_cap(target, current={}, max_turnover=0.30)
    to = sum(abs(capped.get(k, 0.0)) for k in capped) / 2.0
    assert to == pytest.approx(0.30, abs=1e-9)
    assert capped["a"] > 0 and capped["b"] > 0  # 方向不變


# ── 條件式波動目標（AC-2）───────────────────────────────────────────
def test_conditional_vol_target() -> None:
    cfg = _cfg(vol_target={"enabled": True, "annual_vol": 0.15})
    w = {"a": 0.5, "b": 0.5}
    # 波動 0.30（2×目標）且 95 百分位 > 90 → 縮至 0.15/0.30 = 0.5
    hi = core.vol_scale(w, realized_vol=0.30, vol_percentile=95.0, cfg=cfg)
    assert hi["a"] == pytest.approx(0.25)
    # 波動 0.18（1.2×目標）但 50 百分位 < 90 → 不變（條件式）
    lo = core.vol_scale(w, realized_vol=0.18, vol_percentile=50.0, cfg=cfg)
    assert lo["a"] == pytest.approx(0.5)


# ── kill-switch 四態（AC-5/6）───────────────────────────────────────
def test_killswitch_transitions() -> None:
    base = PortfolioState(status="NORMAL", equity_high_water=100.0)
    # 單日虧損 5% → NO_NEW_ENTRY
    s1 = evaluate_killswitch(base, equity_today=95.0, equity_prev=100.0, cfg=_RISK, now=_NOW)
    assert s1.status == "NO_NEW_ENTRY"
    # 回撤 16% → REDUCED
    s2 = evaluate_killswitch(base, equity_today=84.0, equity_prev=84.5, cfg=_RISK, now=_NOW)
    assert s2.status == "REDUCED"
    # 回撤 22% → HALTED
    s3 = evaluate_killswitch(base, equity_today=78.0, equity_prev=78.5, cfg=_RISK, now=_NOW)
    assert s3.status == "HALTED"
    # HALTED 黏著（不自動恢復）
    s4 = evaluate_killswitch(s3, equity_today=100.0, equity_prev=99.0, cfg=_RISK, now=_NOW)
    assert s4.status == "HALTED"


def test_apply_state_reduced_and_halted() -> None:
    target = {"a": 0.4, "b": 0.4}
    reduced = core.apply_state(target, {}, PortfolioState(status="REDUCED"), 0.5)
    assert reduced["a"] == pytest.approx(0.2)
    halted = core.apply_state(target, {}, PortfolioState(status="HALTED"), 0.5)
    assert halted == {}  # AC-6：HALTED 全零


def test_no_new_entry_no_weight_increase() -> None:
    target = {"a": 0.5, "b": 0.3}  # 想加碼 a、新增 b
    current = {"a": 0.2}
    out = core.apply_state(target, current, PortfolioState(status="NO_NEW_ENTRY"), 0.5)
    assert out == {"a": 0.2}  # a 不得升、b 不得新增


# ── build_book 端到端 + HALTED（AC-6）───────────────────────────────
def test_build_book_halted_zero() -> None:
    signals = [_sleeve("A", {"2330": 1.0}), _sleeve("B", {"1101": 1.0})]
    book = build_book(
        signals, {}, PortfolioState(status="HALTED", trigger_reason="test"), _cfg(), _RISK
    )
    assert book.positions.is_empty()
    assert book.status == "HALTED" and book.reason == "test"


# ── 狀態持久化 + fail-safe（AC-5）───────────────────────────────────
def test_state_roundtrip_and_failsafe(tmp_path: Path) -> None:
    # 缺失 → HALTED
    assert load_state(tmp_path).status == "HALTED"
    # 存 NORMAL 再讀回
    save_state(tmp_path, PortfolioState(status="NORMAL", equity_high_water=1e7))
    loaded = load_state(tmp_path)
    assert loaded.status == "NORMAL" and loaded.equity_high_water == pytest.approx(1e7)
    # 損毀 → HALTED
    from twquant.state import state_path

    state_path(tmp_path).write_text("{ not json", encoding="utf-8")
    assert load_state(tmp_path).status == "HALTED"


def test_sample_portfolio_yaml_loads() -> None:
    cfg = load_portfolio(Path("configs/portfolio.yaml"))
    assert cfg.budgets["sample_revenue_trust"] == pytest.approx(1.0)
    assert cfg.vol_target.enabled is True
