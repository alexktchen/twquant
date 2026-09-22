"""T-282／D-143：第九輪兩支新因子——`volume.adv_20d`（規則臂的排序量）與
`ml.lgbm_tail_up_60d`（右尾機率的薄因子）。
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

_BASE = dt.date(2026, 5, 1)
_N = 40


def _d(i: int) -> dt.date:
    return _BASE + dt.timedelta(days=i)


def _wh(tmp_path: Path, amounts: dict[str, list[float]]) -> Warehouse:
    rows = []
    for sid, amts in amounts.items():
        for i, a in enumerate(amts):
            d = _d(i)
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": int(a / 100.0),
                    "amount": a,
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


def _value(wh: Warehouse, name: str, sid: str, i: int) -> float | None:
    df = fc.compute([name], wh.view(_d(_N - 1)), dates=[_d(i)])
    row = df.filter(pl.col("stock_id") == sid)
    return None if row.is_empty() else row["value"][0]


def test_ac2_adv_median_ignores_a_single_spike(tmp_path: Path) -> None:
    normal = [1_000_000.0] * (_N - 1)
    wh = _wh(tmp_path, {"2330": [*normal, 500_000_000.0]})
    got = _value(wh, "volume.adv_20d", "2330", _N - 1)
    assert got == pytest.approx(1_000_000.0), f"爆量那天把中位數拉走了：{got}"


def test_ac3_adv_no_lookahead_and_insufficient_window_is_null(tmp_path: Path) -> None:
    wh = _wh(tmp_path, {"2330": [float(1_000_000 + i * 10_000) for i in range(_N)]})
    assert_no_lookahead("volume.adv_20d", wh, [_d(25), _d(32), _d(_N - 1)])
    assert _value(wh, "volume.adv_20d", "2330", 0) is None


def test_ac4_adv_returns_absolute_twd_not_a_percentile(tmp_path: Path) -> None:
    """規則臂的每日補位吃的是原始值；回分位的話「取成交額前 15」就不成立。"""
    wh = _wh(tmp_path, {"2330": [2_000_000.0] * _N, "2454": [1_000_000.0] * _N})
    big = _value(wh, "volume.adv_20d", "2330", _N - 1)
    small = _value(wh, "volume.adv_20d", "2454", _N - 1)
    assert big == pytest.approx(2_000_000.0)
    assert small == pytest.approx(1_000_000.0)


def test_ac5_tail_thin_factor_requires_an_explicit_model_id(tmp_path: Path) -> None:
    """A／B 兩臂的 model_id 前綴相近，前綴回退會讓兩臂分數互相命中。"""
    wh = _wh(tmp_path, {"2330": [1_000_000.0] * _N})
    fdef = get("ml.lgbm_tail_up_60d")
    assert fdef.fn is not None
    with pytest.raises(ValueError, match="須指定 model_id"):
        fdef.fn(wh.view(_d(_N - 1)), dict(fdef.params))


def test_ac6_tail_thin_factor_metadata() -> None:
    fdef = get("ml.lgbm_tail_up_60d")
    assert fdef.category == "ml"
    assert fdef.direction == 1
    assert fdef.tables == ("ml_scores",)
