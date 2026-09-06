"""T-072：H20 成本感知連續標籤（SPEC-011 AC-1~7）。"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from pydantic import ValidationError

from twquant.backtest.costs import CostConfig, SlippageConfig, trade_cost
from twquant.config import MlConfig, load_config
from twquant.data.serving.ml_store import read_prices_training
from twquant.factors.registry import get
from twquant.ml.dataset import cost_adjusted_return_labels
from twquant.ml.train import make_model_id, training_config_hash

_ROOT = Path(__file__).resolve().parents[1]
_DAYS = [dt.date(2026, 1, 5 + i) for i in range(6)]


def _prices(rows: list[tuple[dt.date, str, float, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={"date": pl.Date, "stock_id": pl.String, "open": pl.Float64, "amount": pl.Float64},
        orient="row",
    )


def _simple_cost() -> CostConfig:
    return CostConfig(
        commission_rate=0.01,
        commission_discount=1.0,
        commission_min_twd=0.0,
        tax_rate_sell=0.02,
        slippage=SlippageConfig(half_spread_bps=0.0, k_bps=0.0),
    )


def test_ac1_cost_adjusted_label_hand_computed() -> None:
    rows: list[tuple[dt.date, str, float, float]] = []
    for sid, opens in {
        "1101": [100.0, 100.0, 110.0, 121.0, 121.0, 121.0],
        "2202": [100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
    }.items():
        rows.extend((_DAYS[i], sid, price, 1_000_000.0) for i, price in enumerate(opens))
    cfg = _simple_cost()
    out = cost_adjusted_return_labels(
        _prices(rows),
        horizon=1,
        adv_window_sessions=2,
        position_notional_twd=1_100.0,
        downside_penalty=0.5,
        cost_cfg=cfg,
    )
    got = {r["stock_id"]: r["label"] for r in out.filter(pl.col("date") == _DAYS[1]).to_dicts()}

    gross_a = 121.0 / 110.0 - 1.0
    gross_b = 0.0
    market = (gross_a + gross_b) / 2.0
    participation = 1_100.0 / 1_000_000.0
    a_cost = (
        trade_cost(side="buy", price=110.0, shares=10, participation=participation, cfg=cfg).total
        + trade_cost(
            side="sell", price=121.0, shares=10, participation=participation, cfg=cfg
        ).total
    ) / (110.0 * 10)
    b_cost = (
        trade_cost(side="buy", price=100.0, shares=11, participation=participation, cfg=cfg).total
        + trade_cost(
            side="sell", price=100.0, shares=11, participation=participation, cfg=cfg
        ).total
    ) / (100.0 * 11)
    net_a = gross_a - market - a_cost
    net_b = gross_b - market - b_cost
    assert got["1101"] == pytest.approx(net_a)
    assert got["2202"] == pytest.approx(net_b * 1.5)


def test_ac2_future_amount_does_not_change_signal_day_label() -> None:
    base = _prices(
        [(_DAYS[i], sid, 100.0 + i, 1_000_000.0) for sid in ("1101", "2202") for i in range(6)]
    )
    changed = base.with_columns(
        pl.when(pl.col("date") > _DAYS[1])
        .then(pl.col("amount") * 1000.0)
        .otherwise(pl.col("amount"))
        .alias("amount")
    )
    a = cost_adjusted_return_labels(
        base,
        horizon=1,
        adv_window_sessions=2,
        position_notional_twd=500_000.0,
        downside_penalty=0.25,
        cost_cfg=CostConfig(),
    ).filter(pl.col("date") == _DAYS[1])
    b = cost_adjusted_return_labels(
        changed,
        horizon=1,
        adv_window_sessions=2,
        position_notional_twd=500_000.0,
        downside_penalty=0.25,
        cost_cfg=CostConfig(),
    ).filter(pl.col("date") == _DAYS[1])
    assert a.equals(b)


def test_ac3_nulls_are_preserved_for_insufficient_adv_and_future() -> None:
    px = _prices([(_DAYS[i], "1101", 100.0, 1_000_000.0) for i in range(4)])
    out = cost_adjusted_return_labels(
        px,
        horizon=1,
        adv_window_sessions=3,
        position_notional_twd=50.0,
        downside_penalty=0.0,
        cost_cfg=CostConfig(),
    )
    assert out.height == px.height
    assert out["label"].null_count() == px.height
    assert out["label"].drop_nulls().is_empty()


def test_ac4_cost_adjusted_labels_are_deterministic() -> None:
    px = _prices(
        [(_DAYS[i], sid, 50.0 + i, 2_000_000.0) for sid in ("1101", "2202") for i in range(6)]
    )
    a = cost_adjusted_return_labels(
        px,
        horizon=2,
        adv_window_sessions=2,
        position_notional_twd=500_000.0,
        downside_penalty=0.25,
        cost_cfg=CostConfig(),
    )
    b = cost_adjusted_return_labels(
        px,
        horizon=2,
        adv_window_sessions=2,
        position_notional_twd=500_000.0,
        downside_penalty=0.25,
        cost_cfg=CostConfig(),
    )
    assert a.equals(b)


def test_ac5_h20_yaml_and_validation() -> None:
    cfg = load_config(_ROOT / "configs" / "ml_h20_cost_aware.yaml", MlConfig)
    assert cfg.horizon_days == 20
    assert cfg.label.transform == "cost_adjusted_return"
    assert cfg.split.embargo_days >= cfg.horizon_days
    assert cfg.features
    for bad in (
        {"adv_window_sessions": 0},
        {"position_notional_twd": 0.0},
        {"downside_penalty": -0.1},
    ):
        with pytest.raises(ValidationError):
            MlConfig.model_validate(
                {
                    "horizon_days": 20,
                    "split": {"embargo_days": 20},
                    "label": {"transform": "cost_adjusted_return", **bad},
                }
            )


def test_ac6_training_hash_binds_costs_and_phase1_model_id_is_compatible() -> None:
    phase1 = MlConfig()
    assert make_model_id(dt.date(2026, 2, 25), phase1).startswith("lgbm_h10_20260225_")

    phase2 = load_config(_ROOT / "configs" / "ml_h20_cost_aware.yaml", MlConfig)
    base_cost = CostConfig()
    changed_cost = base_cost.model_copy(update={"tax_rate_sell": 0.004})
    assert training_config_hash(phase2, base_cost) != training_config_hash(phase2, changed_cost)
    model_id = make_model_id(dt.date(2026, 2, 25), phase2, cost_cfg=base_cost)
    assert model_id.startswith("lgbm_net_h20_20260225_")
    assert training_config_hash(phase2, base_cost)[:8] in model_id


class _Cursor:
    def __init__(self) -> None:
        self.sql = ""
        self.params: tuple[object, ...] = ()

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[object, ...]) -> None:
        self.sql = sql
        self.params = params

    def fetchall(self) -> list[tuple[dt.date, str, float, float]]:
        return [(_DAYS[0], "1101", 100.0, 5_000_000.0)]


class _Conn:
    def __init__(self) -> None:
        self.cur = _Cursor()

    def cursor(self) -> Any:
        return self.cur


def test_ac7_training_price_query_reads_only_required_columns() -> None:
    conn = _Conn()
    out = read_prices_training(conn, "serving", _DAYS[0], _DAYS[-1])
    assert out.columns == ["date", "stock_id", "open", "amount"]
    normalized = " ".join(conn.cur.sql.split()).lower()
    assert "select date, stock_id, open, amount" in normalized
    assert "where date between %s and %s" in normalized
    assert conn.cur.params == (_DAYS[0], _DAYS[-1])


def test_ac8_h20_thin_factor_is_registered() -> None:
    import twquant.factors.builtins  # noqa: F401

    fdef = get("ml.lgbm_net_20d")
    assert fdef.category == "ml"
    assert fdef.direction == 1
    assert fdef.params["model_prefix"] == "lgbm_net_h20_"


def test_ac3_invalid_entry_price_yields_null_and_does_not_poison_cross_section() -> None:
    """AC-3：open(t+1) 無效（<=0）時該列 label=null，且不得汙染當日其他股票。

    缺陷歷史：`_gross` 的除法原本沒有正值防護（成本路徑有），open=0 產生 inf，
    再被 `mean().over("date")` 擴散到整個橫斷面，使該日所有股票的 label 變成 -inf。
    """
    good = [100.0, 100.0, 110.0, 121.0, 121.0, 121.0]
    # "3303" 在 _DAYS[2]（即 _DAYS[1] 的 t+1）開盤為 0 —— 合法值（schema 允許 >= 0）
    bad = [100.0, 100.0, 0.0, 121.0, 121.0, 121.0]
    rows: list[tuple[dt.date, str, float, float]] = []
    for sid, opens in {"1101": good, "2202": good, "3303": bad}.items():
        rows.extend((_DAYS[i], sid, price, 1_000_000.0) for i, price in enumerate(opens))

    out = cost_adjusted_return_labels(
        _prices(rows),
        horizon=1,
        adv_window_sessions=2,
        position_notional_twd=1_100.0,
        downside_penalty=0.25,
        cost_cfg=_simple_cost(),
    )
    day = out.filter(pl.col("date") == _DAYS[1])
    got = {r["stock_id"]: r["label"] for r in day.to_dicts()}

    # 壞列本身為 null，列仍保留（不填 0、不 drop）
    assert day.height == 3
    assert got["3303"] is None

    # 同日其他股票必須是有限值，且不受壞列影響
    for sid in ("1101", "2202"):
        assert got[sid] is not None, f"{sid} 被壞列汙染成 null"
        assert math.isfinite(got[sid]), f"{sid} 被壞列汙染成 {got[sid]}"

    # 兩檔走勢相同 -> 市場均值應只由有效宇宙（這兩檔）計算，故超額為 0，
    # 標籤等於 -成本（再乘下檔加重）。若壞列進了均值，這個等式不成立。
    entry, exit_ = 110.0, 121.0
    shares = int(1_100.0 // entry)
    cfg = _simple_cost()
    buy = trade_cost(
        side="buy", price=entry, shares=shares, participation=1_100.0 / 1_000_000.0, cfg=cfg
    )
    sell = trade_cost(
        side="sell", price=exit_, shares=shares, participation=1_100.0 / 1_000_000.0, cfg=cfg
    )
    cost_rate = (buy.total + sell.total) / (entry * shares)
    expected = -cost_rate * (1.0 + 0.25)
    assert got["1101"] == pytest.approx(expected, rel=1e-12)
