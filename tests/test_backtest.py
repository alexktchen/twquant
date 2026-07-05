"""T-018：回測引擎（成本、檔位、漲跌停、會計恆等、整數張、決定性）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

import twquant.factors.builtins  # noqa: F401
from twquant.backtest import costs as ct
from twquant.backtest.engine import _fill, _State, run_backtest
from twquant.backtest.metrics import compute_metrics
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.strategy.config import SignalConfig, StrategyConfig

_CC = ct.CostConfig()


# ── 檔位與漲跌停 proxy ─────────────────────────────────────────────
def test_tick_table() -> None:
    assert ct.tick(9.9) == 0.01
    assert ct.tick(15.0) == 0.05
    assert ct.tick(80.0) == 0.1
    assert ct.tick(250.0) == 0.5
    assert ct.tick(700.0) == 1.0
    assert ct.tick(1500.0) == 5.0


def test_limit_proxy_ticks() -> None:
    # 前收 100 → 漲停 110 向下取檔（tick 0.5）= 110.0；跌停 90 向上取檔 = 90.0
    assert ct.limit_up_proxy(100.0) == pytest.approx(110.0)
    assert ct.limit_down_proxy(100.0) == pytest.approx(90.0)
    # 前收 15 → 漲停 16.5 向下取至 0.05 檔 = 16.5
    assert ct.limit_up_proxy(15.0) == pytest.approx(16.5)


# ── 成本（AC-2）─────────────────────────────────────────────────────
def test_trade_cost_buy_and_sell() -> None:
    # 買 1 張 @ 500：手續費 = 500000×0.001425×0.35 = 249.375；無稅
    buy = ct.trade_cost(side="buy", price=500.0, shares=1000, participation=0.0, cfg=_CC)
    assert buy.commission == pytest.approx(500_000 * 0.001425 * 0.35)
    assert buy.tax == 0.0
    # 半價差 = max(0.5×tick(500)/500, 5bps)=max(0.5×1/500, 0.0005)=0.001 → 滑價=500000×0.001
    assert buy.slippage == pytest.approx(500_000 * 0.001)
    # 賣出加證交稅
    sell = ct.trade_cost(side="sell", price=500.0, shares=1000, participation=0.0, cfg=_CC)
    assert sell.tax == pytest.approx(500_000 * 0.003)


def test_commission_minimum() -> None:
    # 小額買：手續費不足 20 → 取 20
    c = ct.trade_cost(side="buy", price=10.0, shares=1000, participation=0.0, cfg=_CC)
    assert c.commission == pytest.approx(20.0)


# ── _fill 分支（AC-3 漲停、參與率、現金）─────────────────────────────
def test_fill_limit_up_unfilled() -> None:
    st = _State(cash=1e7)
    row = {"open": 100.0, "limit_up_price": 99.0, "_adv": 1e12, "_prev_close": 95.0}
    _fill(st, "2330", 1000, dt.date(2026, 6, 30), row, _CC, 0.05)
    assert st.cash == pytest.approx(1e7)  # 現金未動
    assert "2330" not in st.holdings
    assert st.unfilled and st.unfilled[0].reason == "limit_up"


def test_fill_participation_cap() -> None:
    st = _State(cash=1e12)
    # ADV 小 → 5% 上限只能買少量；其餘記 unfilled
    row = {"open": 100.0, "limit_up_price": None, "_adv": 1_000_000.0, "_prev_close": 95.0}
    _fill(st, "1101", 100_000, dt.date(2026, 6, 30), row, _CC, 0.05)
    # 上限 = 0.05×1e6 / (100×1000) = 0.5 張 → floor 0 張 → 全部 unfilled
    assert any(u.reason == "participation" for u in st.unfilled)


def test_fill_buy_integer_lots_and_cash() -> None:
    st = _State(cash=1e7)
    row = {"open": 100.0, "limit_up_price": None, "_adv": 1e12, "_prev_close": 95.0}
    _fill(st, "1101", 5000, dt.date(2026, 6, 30), row, _CC, 0.05)
    assert st.holdings["1101"] == 5000
    assert st.holdings["1101"] % 1000 == 0
    assert st.cash < 1e7  # 現金已扣（含成本）
    assert st.cash >= 0


# ── 指標 ────────────────────────────────────────────────────────────
def test_metrics_drawdown() -> None:
    eq = [100.0, 110.0, 88.0, 99.0]  # 峰 110 → 谷 88：MDD = -0.2
    m = compute_metrics(eq, turnover_annualized=0.0)
    assert m.mdd == pytest.approx(-0.2)
    assert m.n_days == 4


# ── 引擎端到端（AC-4/6/8）───────────────────────────────────────────
def _synthetic_wh(tmp_path: Path) -> Warehouse:
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
                    "amount": 1.0e9,
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
            "name": "mom_bt",
            "signals": [SignalConfig(factor="price.mom_20d", weight=1.0)],
            "selection": {"method": "top_n", "top_n": 2},
            "weighting": "equal",
            "max_weight_per_stock": 0.6,
            "universe": {"min_adv_twd": None, "min_price": None},
            "rebalance": {"schedule": "daily"},
            "overlays": [],
        }
    )


def test_backtest_accounting_and_determinism(tmp_path: Path) -> None:
    wh = _synthetic_wh(tmp_path)
    res = run_backtest(_cfg(), _CC, dt.date(2026, 5, 25), dt.date(2026, 6, 9), wh, 1_000_000.0)
    # AC-4：整數張
    assert all(t.shares % 1000 == 0 for t in res.ledger)
    # AC-6：現金 ≥ 0、權益 = 現金 + 持倉市值
    for e in res.equity:
        assert e.cash >= -1e-6
        assert e.equity == pytest.approx(e.cash + e.positions_value, abs=1e-6)
    assert res.ledger  # 有成交
    # AC-8：決定性
    res2 = run_backtest(_cfg(), _CC, dt.date(2026, 5, 25), dt.date(2026, 6, 9), wh, 1_000_000.0)
    assert [e.equity for e in res.equity] == [e.equity for e in res2.equity]
