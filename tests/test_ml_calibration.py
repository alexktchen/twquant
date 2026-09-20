"""T-251／T-254（D-122）：walk-forward isotonic 校準、邏輯斯迴歸、市場狀態分類。"""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl
import pytest

from twquant.ml.calibration import isotonic_fit, isotonic_predict, walk_forward_calibrate
from twquant.ml.logistic import fit_logistic, predict_logistic
from twquant.ml.regime import CALM, GRIND, PANIC, market_regime

D0 = dt.date(2020, 1, 1)


def test_ac1_isotonic_is_monotone_and_pools_violations() -> None:
    model = isotonic_fit(np.array([0.1, 0.2, 0.3, 0.4]), np.array([0.0, 1.0, 0.0, 1.0]))
    out = isotonic_predict(model, np.array([0.1, 0.2, 0.3, 0.4]))
    assert list(out) == pytest.approx([0.0, 0.5, 0.5, 1.0])  # 中間兩點違反單調 → 併成 0.5
    assert np.all(np.diff(out) >= 0)


def test_ac2_isotonic_fixes_overconfidence() -> None:
    rng = np.random.default_rng(0)
    raw = rng.uniform(0.5, 1.0, 20_000)
    y = (rng.uniform(size=raw.size) < raw * 0.6).astype(float)  # 真實機率只有模型說的六成
    cal = isotonic_predict(isotonic_fit(raw, y), np.array([0.9]))
    assert cal[0] == pytest.approx(0.54, abs=0.04)


def test_ac3_isotonic_clips_outside_the_fitted_range() -> None:
    model = isotonic_fit(np.array([0.2, 0.8]), np.array([0.0, 1.0]))
    assert list(isotonic_predict(model, np.array([0.0, 1.0]))) == pytest.approx([0.0, 1.0])


def _oos(n_days: int = 120, per_day: int = 50) -> tuple[pl.DataFrame, list[dt.date]]:
    rng = np.random.default_rng(1)
    days = [D0 + dt.timedelta(days=i) for i in range(n_days)]
    rows = []
    for d in days:
        score = rng.uniform(0, 1, per_day)
        label = (rng.uniform(size=per_day) < score * 0.5).astype(float)
        rows += [
            {"date": d, "stock_id": f"{1000 + j}", "score": float(s), "label": float(y)}
            for j, (s, y) in enumerate(zip(score, label, strict=True))
        ]
    return pl.DataFrame(rows), days


def _windows(days: list[dt.date], size: int = 20) -> list[tuple[dt.date, dt.date]]:
    return [(days[i], days[min(i + size, len(days)) - 1]) for i in range(0, len(days), size)]


def test_ac4_early_windows_are_null_until_enough_history() -> None:
    oos, days = _oos()
    out = walk_forward_calibrate(oos, _windows(days), days, embargo=5, min_windows=2)
    first_two = out.filter(pl.col("date") <= _windows(days)[1][1])
    assert first_two["p_cal"].null_count() == first_two.height
    assert out.filter(pl.col("date") >= _windows(days)[3][0])["p_cal"].null_count() == 0


def test_ac5_calibration_never_sees_labels_that_were_not_yet_known() -> None:
    """把第 4 窗（含）之後的標籤全部翻轉，第 4 窗的校準結果不得改變。"""
    oos, days = _oos()
    wins = _windows(days)
    cut = wins[3][0]
    flipped = oos.with_columns(
        pl.when(pl.col("date") >= cut)
        .then(1.0 - pl.col("label"))
        .otherwise(pl.col("label"))
        .alias("label")
    )
    a = walk_forward_calibrate(oos, wins, days, embargo=5, min_windows=2)
    b = walk_forward_calibrate(flipped, wins, days, embargo=5, min_windows=2)
    in_window = (pl.col("date") >= cut) & (pl.col("date") <= wins[3][1])
    assert a.filter(in_window)["p_cal"].to_list() == b.filter(in_window)["p_cal"].to_list()


def test_ac6_embargo_drops_the_most_recent_samples() -> None:
    oos, days = _oos()
    wins = _windows(days)
    tail = (pl.col("date") >= days[55]) & (pl.col("date") < wins[3][0])  # 第 4 窗前 5 天內
    poked = oos.with_columns(pl.when(tail).then(1.0).otherwise(pl.col("label")).alias("label"))
    a = walk_forward_calibrate(oos, wins, days, embargo=5, min_windows=2)
    b = walk_forward_calibrate(poked, wins, days, embargo=5, min_windows=2)
    in_window = (pl.col("date") >= wins[3][0]) & (pl.col("date") <= wins[3][1])
    assert a.filter(in_window)["p_cal"].to_list() == b.filter(in_window)["p_cal"].to_list()


def test_ac7_logistic_recovers_direction_and_stays_in_unit_interval() -> None:
    rng = np.random.default_rng(2)
    x = rng.normal(size=(4000, 2))
    p = 1 / (1 + np.exp(-(1.5 * x[:, 0] - 0.5 * x[:, 1] - 1.0)))
    y = (rng.uniform(size=4000) < p).astype(float)
    model = fit_logistic(x, y, l2=1e-6, max_iter=50)
    assert model.coef[0] > 0 > model.coef[1]
    out = predict_logistic(model, x)
    assert 0.0 < out.min() and out.max() < 1.0
    assert abs(out.mean() - y.mean()) < 0.02


def test_ac8_logistic_refuses_single_class_or_nan() -> None:
    with pytest.raises(ValueError, match="兩類"):
        fit_logistic(np.zeros((10, 1)), np.zeros(10), l2=1e-6, max_iter=10)
    with pytest.raises(ValueError, match="NaN"):
        fit_logistic(np.array([[np.nan], [1.0]]), np.array([0.0, 1.0]), l2=1e-6, max_iter=10)


def _index(closes: list[float]) -> pl.DataFrame:
    return pl.DataFrame(
        {"date": [D0 + dt.timedelta(days=i) for i in range(len(closes))], "close": closes}
    )


def test_ac9_regime_separates_grind_from_panic() -> None:
    calm = [100.0 + 0.1 * i for i in range(300)]
    grind = [calm[-1] * (1 - 0.002) ** i for i in range(1, 61)]  # 每天 −0.2%，波動沒放大
    out = market_regime(
        _index(calm + grind), ma_window=60, vol_window=20, vol_lookback=250, vol_pctl=0.9
    )
    assert out["regime"][250] is None  # 波動 20 日＋回看 250 日：第 271 天才有值
    assert out["regime"][290] == CALM
    assert out["regime"][-1] == GRIND
    crash = [calm[-1] * f for f in (0.93, 0.99, 0.91, 0.97, 0.88, 0.95, 0.86, 0.9, 0.84, 0.88)]
    out2 = market_regime(
        _index(calm + crash), ma_window=60, vol_window=20, vol_lookback=250, vol_pctl=0.9
    )
    assert out2["regime"][-1] == PANIC


def test_ac10_regime_has_no_lookahead_and_warmup_is_null() -> None:
    closes = [100.0 + np.sin(i / 7) * 5 + i * 0.01 for i in range(400)]
    full = market_regime(
        _index(closes), ma_window=60, vol_window=20, vol_lookback=250, vol_pctl=0.9
    )
    part = market_regime(
        _index(closes[:350]), ma_window=60, vol_window=20, vol_lookback=250, vol_pctl=0.9
    )
    assert full.head(350)["regime"].to_list() == part["regime"].to_list()
    assert full["regime"][0] is None  # 暖機期不知道就是不知道，不得當成 calm
