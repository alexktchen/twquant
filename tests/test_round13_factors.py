"""T-308／D-153 §一：第十三輪的兩支薄因子——`ml.lgbm_tail_up_20d`（A20 的機率／池內百分位）
與 `ml.pool_random`（同池固定 seed 亂數，對照用）。兩支都只讀 `ml_scores`，沒分數的股票沒有值。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

import twquant.factors.builtins  # noqa: F401
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.factors import compute as fc
from twquant.factors.registry import get
from twquant.factors.testing import assert_no_lookahead

_BASE = dt.date(2026, 5, 4)
_N = 30
_SIDS = ("1101", "2330", "2454", "3008")
_POOL = ("1101", "2330", "2454")  # 3008 沒有分數：不在池內
_MODEL = "lgbm_quietA20_tu_h20_20260430_deadbeef"
_UP = "ml.lgbm_tail_up_20d"
_RND = "ml.pool_random"


def _d(i: int) -> dt.date:
    return _BASE + dt.timedelta(days=i)


def _tw(days: list[dt.date], hour: int) -> pl.Series:
    return pl.Series(
        [dt.datetime(d.year, d.month, d.day, hour, tzinfo=TZ) for d in days], dtype=schemas.TW_DT
    )


def _prices() -> pl.DataFrame:
    rows = [(d, s) for d in (_d(i) for i in range(_N)) for s in _SIDS]
    close = [100.0 + i for i in range(len(rows))]
    return pl.DataFrame(
        {
            "date": [r[0] for r in rows],
            "stock_id": [r[1] for r in rows],
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": [1000] * len(rows),
            "amount": [1e6] * len(rows),
            "limit_up_price": pl.Series([None] * len(rows), dtype=pl.Float64),
            "limit_down_price": pl.Series([None] * len(rows), dtype=pl.Float64),
            "available_at": _tw([r[0] for r in rows], 18),
        }
    )


def _scores() -> pl.DataFrame:
    rows = []
    for i in range(_N):
        for k, s in enumerate(_POOL):
            score = 0.02 + 0.03 * ((i + k) % 3)  # 0.02／0.05／0.08 輪流
            rows.append((_d(i), s, _MODEL, score, (k + 1) / len(_POOL)))
    return pl.DataFrame(
        {
            "date": [r[0] for r in rows],
            "stock_id": [r[1] for r in rows],
            "model_id": [r[2] for r in rows],
            "score": [r[3] for r in rows],
            "rank_pct": [r[4] for r in rows],
            "available_at": _tw([r[0] for r in rows], 19),
        }
    )


@pytest.fixture
def wh(tmp_path: Path) -> Warehouse:
    w = Warehouse(tmp_path / "lake")
    w.write("clean", "prices_daily", _prices())
    w.write("clean", "ml_scores", _scores())
    return w


def _values(wh: Warehouse, name: str, params: dict[str, object], i: int) -> dict[str, float]:
    df = fc.compute([name], wh.view(_d(_N - 1)), dates=[_d(i)], params={name: params})
    return {str(r["stock_id"]): float(r["value"]) for r in df.drop_nulls("value").to_dicts()}


def test_ac1_tail_up_20d_requires_model_id_and_a_known_value_col(wh: Warehouse) -> None:
    fdef = get(_UP)
    assert fdef.fn is not None
    with pytest.raises(ValueError, match="須指定 model_id"):
        fdef.fn(wh.view(_d(_N - 1)), dict(fdef.params))
    with pytest.raises(ValueError, match="value_col"):
        fdef.fn(wh.view(_d(_N - 1)), {**fdef.params, "model_id": _MODEL, "value_col": "label"})


def test_ac2_tail_up_20d_returns_score_or_rank_pct_only_for_pooled_stocks(wh: Warehouse) -> None:
    got = _values(wh, _UP, {"model_id": _MODEL}, 10)
    assert set(got) == set(_POOL), "沒分數的 3008 不該有值"
    assert got["1101"] == pytest.approx(0.02 + 0.03 * ((10 + 0) % 3))
    pct = _values(wh, _UP, {"model_id": _MODEL, "value_col": "rank_pct"}, 10)
    assert pct == {"1101": pytest.approx(1 / 3), "2330": pytest.approx(2 / 3), "2454": 1.0}


def test_ac3_metadata() -> None:
    for name in (_UP, _RND):
        fdef = get(name)
        assert fdef.category == "ml"
        assert fdef.direction == 1
        assert fdef.tables == ("ml_scores",)
        assert fdef.lookback_days == 0


def test_ac4_no_lookahead_on_both_value_cols(wh: Warehouse) -> None:
    cuts = [_d(5), _d(12), _d(_N - 1)]
    assert_no_lookahead(_UP, wh, cuts, params={"model_id": _MODEL})
    assert_no_lookahead(_UP, wh, cuts, params={"model_id": _MODEL, "value_col": "rank_pct"})
    assert_no_lookahead(_RND, wh, cuts, params={"model_id": _MODEL, "seed": 20260924})


def test_ac5_pool_random_is_deterministic_per_seed_and_confined_to_the_pool(
    wh: Warehouse,
) -> None:
    fdef = get(_RND)
    assert fdef.fn is not None
    with pytest.raises(ValueError, match="須指定 model_id"):
        fdef.fn(wh.view(_d(_N - 1)), dict(fdef.params))
    a = _values(wh, _RND, {"model_id": _MODEL, "seed": 20260924}, 10)
    b = _values(wh, _RND, {"model_id": _MODEL, "seed": 20260924}, 10)
    assert a == b, "同一個 seed 兩次要逐位元相同（回測可重現）"
    assert set(a) == set(_POOL)
    assert all(0.0 <= v < 1.0 for v in a.values())
    other = _values(wh, _RND, {"model_id": _MODEL, "seed": 7}, 10)
    assert other != a, "換 seed 要換名單"
    across = _values(wh, _RND, {"model_id": _MODEL, "seed": 20260924}, 11)
    assert across != a, "不同日期的亂數不該相同（否則排序每天一樣）"
