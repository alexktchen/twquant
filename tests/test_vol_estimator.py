"""D-004 條件式波動目標的估計器（T-296／D-149 §二）。

`vol_scale` 在 2026-07 就寫好了，但產生 `VolContext` 的估計器從未實作。這裡把 SPEC-005
的定義釘住：60 日指數權重（半衰期 20）標準差 × √252、自身 252 日的百分位、
「≥ 第 90 百分位**且**高於 15%」才觸發、觸發時縮到 15% ÷ 波動、只降不升。
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from twquant.config import load_config
from twquant.errors import ConfigError
from twquant.portfolio.config import PortfolioConfig, VolEstimator, VolTargetConfig
from twquant.portfolio.core import (
    VolContext,
    ewma_vol_series,
    vol_context,
    vol_multiplier,
    vol_scale,
    vol_triggered,
)

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"
_VT = VolTargetConfig(enabled=True)  # 模型預設值＝D-004：0.15／ewma 60／半衰期 20／252／90／1.0
_PERIODS = 252.0
_WINDOW = _VT.estimator.window_days
_NEED = _WINDOW + _VT.conditional.percentile_window_days - 1  # 311 個報酬


def _returns(n: int, seed: int = 7, sd: float = 0.02) -> np.ndarray:
    return np.random.default_rng(seed).normal(0.0, sd, n)


def _alternating(n: int, size: float) -> np.ndarray:
    return np.array([size if i % 2 else -size for i in range(n)])


def test_ac1_ewma_matches_polars_definition() -> None:
    """每一點都等於 polars `ewm_std(half_life=20, adjust=True, bias=False)` 在最近 60 個報酬上的值 × √252。"""
    r = _returns(400)
    got = ewma_vol_series(r, _VT.estimator, _PERIODS)
    assert got.shape == (400 - _WINDOW + 1,)
    want = [
        float(
            pl.Series(r[i - _WINDOW + 1 : i + 1]).ewm_std(
                half_life=_VT.estimator.halflife, adjust=True, bias=False
            )[-1]
        )
        * math.sqrt(_PERIODS)
        for i in range(_WINDOW - 1, 400)
    ]
    np.testing.assert_allclose(got, want, rtol=0.0, atol=1e-12)


def test_ac2_no_lookahead_each_point_uses_only_its_past() -> None:
    """反前視：f(data[:t]) 的最後一點 == f(data)[t]；把未來改掉，過去的估計逐位元不動。"""
    r = _returns(400)
    full = ewma_vol_series(r, _VT.estimator, _PERIODS)
    for k in (_WINDOW, 123, 300, 400):
        assert ewma_vol_series(r[:k], _VT.estimator, _PERIODS)[-1] == full[k - _WINDOW]
    poked = r.copy()
    poked[350:] = 0.5
    cut = 350 - _WINDOW + 1
    assert np.array_equal(ewma_vol_series(poked, _VT.estimator, _PERIODS)[:cut], full[:cut])


def test_ac3_percentile_is_rank_within_own_window() -> None:
    """百分位＝最近 252 個估計值裡 ≤ 今日的比例：剛轉入高波動 → 100；剛轉入低波動 → 最低那一格。"""
    calm, wild = _alternating(_NEED - _WINDOW, 0.001), _alternating(_WINDOW, 0.03)
    ctx = vol_context(np.concatenate([calm, wild]), _VT, _PERIODS)
    assert ctx.vol_percentile == 100.0
    assert ctx.realized_vol > _VT.annual_vol
    assert vol_triggered(ctx, _VT)
    # 幅度逐日遞減 → 估計值嚴格遞減 → 今日是窗內最低，只有自己 ≤ 自己
    fading = np.array(
        [(0.03 * (1.0 - t / _NEED) + 0.0005) * (1 if t % 2 else -1) for t in range(_NEED)]
    )
    ctx = vol_context(fading, _VT, _PERIODS)
    assert ctx.vol_percentile == pytest.approx(100.0 / _VT.conditional.percentile_window_days)
    assert not vol_triggered(ctx, _VT)
    # 多給的歷史只用最後那一段：前面塞什麼都不影響
    longer = np.concatenate([_alternating(500, 0.08), calm, wild])
    assert vol_context(longer, _VT, _PERIODS) == vol_context(
        np.concatenate([calm, wild]), _VT, _PERIODS
    )


def test_ac4_history_shorter_than_needed_raises() -> None:
    with pytest.raises(ValueError):
        vol_context(_returns(_NEED - 1), _VT, _PERIODS)
    assert vol_context(_returns(_NEED), _VT, _PERIODS).realized_vol > 0.0  # 剛好夠
    with pytest.raises(ValueError):
        ewma_vol_series(_returns(_WINDOW - 1), _VT.estimator, _PERIODS)


def test_ac5_non_finite_returns_and_unknown_method_raise() -> None:
    r = _returns(400)
    r[100] = np.nan
    with pytest.raises(ValueError):
        ewma_vol_series(r, _VT.estimator, _PERIODS)
    with pytest.raises(ConfigError):
        ewma_vol_series(_returns(400), VolEstimator(method="simple"), _PERIODS)


def test_ac6_trigger_and_multiplier_follow_spec_005_ac2() -> None:
    """SPEC-005 AC-2：2×目標且 >90 百分位 → 縮到目標（0.5 倍）；1.2×目標但 <90 百分位 → 1 倍。"""
    assert vol_triggered(VolContext(0.30, 95.0), _VT)
    assert vol_multiplier(VolContext(0.30, 95.0), 0.30, _VT) == pytest.approx(0.5)
    assert not vol_triggered(VolContext(0.18, 80.0), _VT)
    assert vol_multiplier(VolContext(0.18, 80.0), 0.18, _VT) == 1.0
    # 「且」：百分位夠高但絕對值不到 15% → 不觸發（D-004 的兩個條件缺一不可）
    assert not vol_triggered(VolContext(0.12, 99.0), _VT)
    # 邊界：剛好第 90 百分位算觸發（vol_scale 原本就是 `< reduce_above_pct` 才放行）
    assert vol_triggered(VolContext(0.30, 90.0), _VT)
    # F2 的形狀：觸發看一條序列、倍數用另一個波動
    assert vol_multiplier(VolContext(0.25, 95.0), 0.60, _VT) == pytest.approx(0.25)
    # 只降不升：被縮的組合波動已低於目標 → 1 倍
    assert vol_multiplier(VolContext(0.25, 95.0), 0.10, _VT) == 1.0
    assert vol_multiplier(VolContext(0.30, 95.0), 0.30, VolTargetConfig(enabled=False)) == 1.0
    assert vol_multiplier(VolContext(0.30, 95.0), 0.0, _VT) == 1.0


def test_ac7_vol_scale_keeps_its_original_semantics() -> None:
    """vol_scale 改走共用倍數後，對一整格（波動, 百分位）的輸出與原公式逐位元相同。"""
    sleeves = [{"strategy": "x", "risk_budget": 1.0}]
    cfg = PortfolioConfig.model_validate({"sleeves": sleeves, "vol_target": _VT.model_dump()})
    w = {"2330": 0.6, "2454": 0.4}
    for rv in (0.0, 0.05, 0.15, 0.1500001, 0.2, 0.3, 0.9):
        for pct in (0.0, 50.0, 89.9, 90.0, 100.0):
            if rv <= 0.0 or pct < 90.0 or rv <= 0.15:
                want = dict(w)
            else:
                scale = min(0.15 / rv, 1.0)
                want = {s: x * scale for s, x in w.items()}
            assert vol_scale(w, rv, pct, cfg) == want, (rv, pct)
    off = PortfolioConfig.model_validate({"sleeves": sleeves, "vol_target": {"enabled": False}})
    assert vol_scale(w, 0.9, 100.0, off) == w


def test_ac8_portfolio_yaml_carries_the_d004_values() -> None:
    """`configs/portfolio.yaml` 的 vol_target 就是 D-004 登記的那組；第十一輪逐字沿用它。"""
    vt = load_config(_CONFIG_DIR / "portfolio.yaml", PortfolioConfig).vol_target
    assert vt.enabled and vt.annual_vol == 0.15 and vt.max_leverage == 1.0
    assert (vt.estimator.method, vt.estimator.window_days, vt.estimator.halflife) == (
        "ewma",
        60,
        20,
    )
    assert (vt.conditional.percentile_window_days, vt.conditional.reduce_above_pct) == (252, 90.0)
