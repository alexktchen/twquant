"""T-017：策略框架（配置驗證、排程、組合、overlay、目標權重不變量、決定性）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

import twquant.factors.builtins  # noqa: F401  # 註冊因子
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.calendar import TradingCalendar
from twquant.data.warehouse import Warehouse
from twquant.errors import ConfigError
from twquant.strategy import build, combine
from twquant.strategy.config import (
    RebalanceConfig,
    SignalConfig,
    StrategyConfig,
    load_strategy,
)
from twquant.strategy.overlays import breadth_regime_filter, market_breadth


# ── 配置驗證（AC-1）────────────────────────────────────────────────
def test_weights_must_sum_to_one() -> None:
    with pytest.raises(ValueError, match="加總"):
        StrategyConfig(
            name="x",
            signals=[
                SignalConfig(factor="price.mom_20d", weight=0.5),
                SignalConfig(factor="chip.trust_streak", weight=0.3),
            ],
        )


def test_unknown_factor_raises_with_hint(tmp_path: Path) -> None:
    yaml = tmp_path / "s.yaml"
    yaml.write_text(
        "name: bad\nsignals:\n  - factor: fundamental.revenue_yay\n    weight: 1.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="revenue_yoy"):  # 最相近的註冊名
        load_strategy(yaml)


def test_sample_yaml_loads() -> None:
    cfg = load_strategy(Path("configs/strategies/sample_revenue_trust.yaml"))
    assert cfg.name == "sample_revenue_trust"
    assert abs(sum(s.weight for s in cfg.signals) - 1.0) < 1e-9
    assert cfg.rebalance.schedule == "weekly"


# ── 排程（AC-3）────────────────────────────────────────────────────
def test_signal_dates_weekly_mon_holiday() -> None:
    # 2026-06-15 是週一。做一個「該週一休市」的日曆 → 應順延到週二 6/16
    sessions = [
        dt.date(2026, 6, 16),  # 週二（週一 6/15 休市）
        dt.date(2026, 6, 17),
        dt.date(2026, 6, 22),  # 下週一
        dt.date(2026, 6, 23),
    ]
    cal = TradingCalendar(sessions)
    rb = RebalanceConfig(schedule="weekly", day="MON")
    got = build.signal_dates(rb, cal, dt.date(2026, 6, 15), dt.date(2026, 6, 26))
    assert got == [dt.date(2026, 6, 16), dt.date(2026, 6, 22)]  # 首週順延、次週正常週一


def test_signal_dates_monthly() -> None:
    sessions = [dt.date(2026, 5, d) for d in (1, 4, 5)] + [dt.date(2026, 6, d) for d in (1, 2)]
    cal = TradingCalendar(sessions)
    rb = RebalanceConfig(schedule="monthly", day_of_month=1)
    got = build.signal_dates(rb, cal, dt.date(2026, 5, 1), dt.date(2026, 6, 30))
    assert got == [dt.date(2026, 5, 1), dt.date(2026, 6, 1)]


# ── 組合 ───────────────────────────────────────────────────────────
def test_zscore_weighted_orientation() -> None:
    # 兩因子、一天、三檔：a 兩因子都最高 → 分數最高
    df = pl.DataFrame(
        {
            "date": [dt.date(2026, 6, 30)] * 6,
            "stock_id": ["a", "b", "c"] * 2,
            "factor": ["f1"] * 3 + ["f2"] * 3,
            "value": [3.0, 2.0, 1.0, 30.0, 20.0, 10.0],
        }
    )
    score = combine.combined_score(df, {"f1": 0.5, "f2": 0.5}, "zscore_weighted")
    top = score.sort("score", descending=True)["stock_id"].to_list()
    assert top[0] == "a" and top[-1] == "c"


# ── 目標權重端到端（AC-5、AC-7）+ overlay（AC-6）+ 宇宙 ─────────────
def _synthetic_wh(tmp_path: Path, *, amounts: dict[str, float] | None = None) -> Warehouse:
    """25 個連續日、三檔不同動能；amount 可個別設定（測 ADV 過濾）。"""
    base = dt.date(2026, 6, 1)
    slopes = {"1101": 1.0, "2330": 2.0, "2454": 0.5}  # 動能 2330 > 1101 > 2454
    amt = amounts or dict.fromkeys(slopes, 1.0e8)
    rows = []
    for i in range(25):
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
                    "volume": 1000,
                    "amount": amt[sid],
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


def _mom_cfg(**over: object) -> StrategyConfig:
    base: dict[str, object] = {
        "name": "t",
        "signals": [SignalConfig(factor="price.mom_20d", weight=1.0)],
        "selection": {"method": "top_n", "top_n": 2},
        "weighting": "equal",
        "max_weight_per_stock": 0.6,
        "universe": {"min_adv_twd": 30_000_000, "min_price": None},
        "overlays": [],
    }
    base.update(over)
    return StrategyConfig.model_validate(base)


def test_build_targets_invariants_and_determinism(tmp_path: Path) -> None:
    wh = _synthetic_wh(tmp_path)
    cfg = _mom_cfg()
    d = dt.date(2026, 6, 25)
    out = build.build_targets(cfg, wh, d)
    assert out.columns == ["date", "stock_id", "target_weight"]
    assert set(out["stock_id"].to_list()) == {"1101", "2330"}  # 動能最高兩檔（排除 2454）
    w = out["target_weight"].to_list()
    assert sum(w) <= 1.0 + 1e-9
    assert max(w) <= 0.6 + 1e-9
    assert min(w) >= 0.0
    assert out.equals(build.build_targets(cfg, wh, d))  # AC-7 決定性


def test_universe_adv_filter_excludes_illiquid(tmp_path: Path) -> None:
    # 2454 成交值遠低於門檻 → 被宇宙淘汰，即使動能不差
    wh = _synthetic_wh(tmp_path, amounts={"1101": 1e8, "2330": 1e8, "2454": 1e6})
    cfg = _mom_cfg(selection={"method": "top_n", "top_n": 5})
    out = build.build_targets(cfg, wh, dt.date(2026, 6, 25))
    assert "2454" not in out["stock_id"].to_list()


def test_breadth_overlay_bear_floor(tmp_path: Path) -> None:
    # 全部下跌 → 站上均線比例 0 → 曝險應被壓到 exposure_floor
    base = dt.date(2026, 6, 1)
    rows = []
    for i in range(25):
        d = base + dt.timedelta(days=i)
        for sid in ("1101", "2330"):
            c = 200.0 - i  # 一路下跌
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": c,
                    "high": c,
                    "low": c,
                    "close": c,
                    "volume": 1000,
                    "amount": 1e8,
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
    d = dt.date(2026, 6, 25)
    view = wh.view(d)
    assert market_breadth(view, d, 10) == pytest.approx(0.0)
    weights = pl.DataFrame({"date": [d], "stock_id": ["1101"], "target_weight": [0.5]})
    out = breadth_regime_filter(d, weights, view, {"ma_window": 10, "exposure_floor": 0.5})
    assert out["target_weight"][0] == pytest.approx(0.25)  # 0.5 原曝險 × floor 0.5
