"""T-254／D-122 §四：大跌預警的特徵、事件合併與 walk-forward 無前視。"""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl

from twquant.ml.market_warning import (
    FEATURES,
    WarningConfig,
    event_spans,
    walk_forward_warning,
    warning_features,
)

D0 = dt.date(2015, 1, 1)
CFG = WarningConfig(
    horizon=5,
    drop=0.10,
    long_window=20,
    short_window=10,
    change_window=5,
    min_events=2,
    refit_sessions=50,
    alarm_pctl=0.85,
    l2=1e-3,
    max_iter=50,
)


def _data(n: int = 600, seed: int = 0) -> tuple[pl.DataFrame, pl.DataFrame]:
    rng = np.random.default_rng(seed)
    close = [100.0]
    for i in range(1, n):
        shock = -0.04 if (i % 120) in range(100, 105) else 0.001  # 每 120 天一次五連跌
        close.append(close[-1] * (1 + shock + rng.normal(0, 0.003)))
    days = [D0 + dt.timedelta(days=i) for i in range(n)]
    index = pl.DataFrame({"date": days, "close": close})
    rows = [
        {"date": d, "stock_id": sid, "close": c * (1 + 0.1 * k)}
        for d, c in zip(days, close, strict=True)
        for k, sid in enumerate(("1101", "2330", "2454"))
    ]
    return index, pl.DataFrame(rows)


def test_ac1_label_is_forward_drawdown_and_tail_is_null() -> None:
    index, prices = _data()
    f = warning_features(index, prices, CFG)
    assert f["label"].tail(CFG.horizon).null_count() == CFG.horizon
    assert f.filter(pl.col("label") == 1.0).height > 0
    assert set(FEATURES) <= set(f.columns)


def test_ac2_events_are_merged_by_gap() -> None:
    labels = [0.0, 1.0, 1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    days = [D0 + dt.timedelta(days=i) for i in range(len(labels))]
    assert event_spans(days, labels, gap=2) == [(1, 4), (9, 9)]


def test_ac3_features_have_no_lookahead() -> None:
    index, prices = _data()
    full = warning_features(index, prices, CFG).select("date", *FEATURES)
    cut = D0 + dt.timedelta(days=400)
    part = warning_features(
        index.filter(pl.col("date") <= cut), prices.filter(pl.col("date") <= cut), CFG
    ).select("date", *FEATURES)
    assert full.filter(pl.col("date") <= cut).equals(part)


def test_ac4_walk_forward_ignores_the_future() -> None:
    """把第 400 天之後的指數整段換掉，第 400 天（含）以前的機率與預警不得改變。"""
    index, prices = _data()
    other, other_px = _data(seed=9)
    cut = D0 + dt.timedelta(days=400)
    mixed = pl.concat([index.filter(pl.col("date") <= cut), other.filter(pl.col("date") > cut)])
    mixed_px = pl.concat(
        [prices.filter(pl.col("date") <= cut), other_px.filter(pl.col("date") > cut)]
    )
    a = walk_forward_warning(warning_features(index, prices, CFG), CFG)
    b = walk_forward_warning(warning_features(mixed, mixed_px, CFG), CFG)
    early = pl.col("date") <= cut - dt.timedelta(days=CFG.horizon + CFG.refit_sessions)
    assert a.filter(early).equals(b.filter(early))


def test_ac5_no_output_before_enough_events() -> None:
    index, prices = _data()
    out = walk_forward_warning(warning_features(index, prices, CFG), CFG)
    assert out["p"][150] is None  # 此時只看過一次事件（min_events=2）
    assert out["p"].drop_nulls().len() > 0
