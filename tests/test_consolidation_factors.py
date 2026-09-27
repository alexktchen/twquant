"""T-302：盤整型態五支因子（定義由 D-151 §三 事前寫死）。

每支都過 `assert_no_lookahead`（CLAUDE.md §4 的強制護欄），另外釘住：窗內不足回 null、
宣告的 lookback 蓋得住計算窗、以及每支因子的語意——壓縮是低百分位、糾結是小差距、
POC 落在量最多的價位、一價到底的量不會掉、守住大量區的天數數得對。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import polars as pl
import pytest

import twquant.factors.builtins  # noqa: F401
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.factors import compute as fc
from twquant.factors.consolidation import rolling_weak_pct, volume_profile
from twquant.factors.registry import get
from twquant.factors.testing import assert_no_lookahead

_BASE = dt.date(2025, 1, 1)
NAMES = (
    "price.bb_squeeze_120",
    "price.ma_tangle",
    "price.vp_poc_dist_60",
    "price.vp_support_hold_20",
    "price.vp_near_share_60",
)
Bar = tuple[float, float, float, float, float]  # open, high, low, close, volume


def _d(i: int) -> dt.date:
    return _BASE + dt.timedelta(days=i)


def _wh(tmp_path: Path, bars: dict[str, list[Bar]]) -> Warehouse:
    rows = [
        {
            "date": _d(i),
            "stock_id": sid,
            "open": o,
            "high": h,
            "low": low,
            "close": c,
            "volume": int(v),
            "amount": c * v,
            "limit_up_price": None,
            "limit_down_price": None,
            "available_at": dt.datetime(_d(i).year, _d(i).month, _d(i).day, 18, tzinfo=TZ),
        }
        for sid, series in bars.items()
        for i, (o, h, low, c, v) in enumerate(series)
    ]
    df = pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(schemas.TW_DT),
        pl.col("volume").cast(pl.Int64),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", df, source="twse")
    return wh


def _walk(n: int, seed: int) -> list[Bar]:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.02, n)))
    out: list[Bar] = []
    for i, c in enumerate(close):
        prev = close[i - 1] if i else c
        hi, lo = max(prev, c) * 1.01, min(prev, c) * 0.99
        out.append(
            (float(prev), float(hi), float(lo), float(c), float(rng.integers(1_000, 50_000)))
        )
    return out


_N = 200


def _value(wh: Warehouse, name: str, sid: str, i: int) -> float | None:
    df = fc.compute([name], wh.view(_d(_N - 1)), dates=[_d(i)])
    row = df.filter(pl.col("stock_id") == sid)
    return None if row.is_empty() else float(row["value"][0])


@pytest.mark.parametrize("name", NAMES)
def test_ac1_no_lookahead(name: str, tmp_path: Path) -> None:
    wh = _wh(tmp_path, {"2330": _walk(_N, 1), "2454": _walk(_N, 2)})
    assert_no_lookahead(name, wh, [_d(150), _d(175), _d(_N - 1)])


@pytest.mark.parametrize("name", NAMES)
def test_ac2_insufficient_window_returns_null(name: str, tmp_path: Path) -> None:
    """窗內不足一律 null，不得用較短的窗充數（黃金規則 5）。"""
    wh = _wh(tmp_path, {"2330": _walk(_N, 1)})
    assert _value(wh, name, "2330", 10) is None, f"{name} 在第 11 天就給了值"


@pytest.mark.parametrize(
    ("name", "rows"),
    [
        ("price.bb_squeeze_120", 20 + 120 - 1),
        ("price.ma_tangle", 60),
        ("price.vp_poc_dist_60", 60),
        ("price.vp_support_hold_20", 60 + 20 - 1),
        ("price.vp_near_share_60", 60),
    ],
)
def test_ac3_registered_lookback_covers_the_window(name: str, rows: int, tmp_path: Path) -> None:
    """宣告的 lookback 必須 ≥ 需要的列數；剛好湊滿那一列要有值、少一列要是 null。"""
    assert get(name).lookback_days >= rows
    wh = _wh(tmp_path, {"2330": _walk(_N, 3)})
    assert _value(wh, name, "2330", rows - 1) is not None
    assert _value(wh, name, "2330", rows - 2) is None


def test_ac4_squeeze_is_a_low_percentile_and_expansion_a_high_one() -> None:
    x = np.concatenate([np.linspace(1.0, 2.0, 150), np.array([0.5])])  # 最後一天突然最窄
    assert rolling_weak_pct(x, 120)[-1] == pytest.approx(1.0 / 120)
    assert rolling_weak_pct(np.linspace(1.0, 2.0, 150), 120)[-1] == 1.0  # 一路變寬 → 今天最寬
    y = np.linspace(1.0, 2.0, 150)
    y[100] = np.nan
    assert np.isnan(rolling_weak_pct(y, 120)[-1])  # 窗內有缺值 → 不給值


def test_ac5_ma_tangle_is_zero_when_flat_and_positive_when_trending(tmp_path: Path) -> None:
    flat: list[Bar] = [(100.0, 101.0, 99.0, 100.0, 1_000.0)] * _N
    trend: list[Bar] = [(100.0 + i, 101.0 + i, 99.0 + i, 100.0 + i, 1_000.0) for i in range(_N)]
    wh = _wh(tmp_path, {"1101": flat, "2330": trend})
    assert _value(wh, "price.ma_tangle", "1101", _N - 1) == 0.0
    got = _value(wh, "price.ma_tangle", "2330", _N - 1)
    # 等差上漲：MA5 − MA60 ＝ (60−5)/2 ＝ 27.5
    assert got == pytest.approx(27.5 / (100.0 + _N - 1))


def test_ac6_poc_sits_where_the_volume_traded() -> None:
    """55 天在 99~101 大量成交、5 天在 109~111 少量成交 → POC 在 100 附近，不在 110。"""
    low = np.array([99.0] * 55 + [109.0] * 5)
    high = np.array([101.0] * 55 + [111.0] * 5)
    vol = np.array([50_000.0] * 55 + [1_000.0] * 5)
    close = np.array([100.0] * 55 + [110.0] * 5)
    poc, near = volume_profile(low, high, vol, close, window=60, bins=20, band=0.03)
    # 區間 99~111 切 20 格、格寬 0.6：99~101 的大量均勻攤進前三格而同量，
    # 依「同量取價位最低那格」→ 第一格中價 99.3。重點是落在大量區、不在 110。
    assert poc[-1] == pytest.approx(99.3)
    # 今日收盤 110 的 ±3%（106.7~113.3）只含那 5 天的量
    assert near[-1] == pytest.approx(5_000.0 / (55 * 50_000.0 + 5_000.0))
    assert np.isnan(poc[:59]).all() and np.isfinite(poc[59])


def test_ac7_a_locked_day_keeps_its_volume() -> None:
    """一價到底（高＝低）的量整筆記在那個價位，不能因為區間為零而消失。"""
    low = np.array([100.0] * 59 + [120.0])
    high = np.array([102.0] * 59 + [120.0])
    vol = np.array([1_000.0] * 59 + [10_000_000.0])
    close = np.array([101.0] * 59 + [120.0])
    poc, near = volume_profile(low, high, vol, close, window=60, bins=20, band=0.03)
    assert poc[-1] == pytest.approx(119.5)  # 最高那一格（119~120）的中價
    assert near[-1] == pytest.approx(10_000_000.0 / (59 * 1_000.0 + 10_000_000.0))


def test_ac8_support_hold_counts_tests_of_the_volume_node(tmp_path: Path) -> None:
    """大量區在 100：最後 20 天每天最低都碰到 100.5、收在 102 → 20 天全算守住；
    若收在 99（跌破），一天都不算。"""
    base: list[Bar] = [(100.0, 101.0, 99.0, 100.0, 100_000.0)] * 120
    held: list[Bar] = base + [(102.0, 103.0, 100.5, 102.0, 1_000.0)] * 20
    broke: list[Bar] = base + [(99.0, 99.5, 98.0, 99.0, 1_000.0)] * 20
    wh = _wh(tmp_path, {"2330": held, "2454": broke})
    df = fc.compute(["price.vp_support_hold_20"], wh.view(_d(139)), dates=[_d(139)])
    got = {r["stock_id"]: r["value"] for r in df.to_dicts()}
    assert got["2330"] == 20.0
    assert got["2454"] == 0.0
