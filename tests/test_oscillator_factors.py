"""D-138：震盪指標因子（KD／RSI／布林／MACD）。

每支都要過 `assert_no_lookahead`（CLAUDE.md §4 的強制護欄），另釘住三件容易寫錯的事：
分母為零時回 null 而非填補、MACD 有除以收盤價（否則高價股恆大、橫斷面比較無意義）、
以及方向宣告與實測一致（超買為正，見模組 docstring）。
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
NAMES = (
    "price.kd_k_9",
    "price.rsi_14",
    "price.bollinger_pctb_20",
    "price.bollinger_width_20",
    "price.macd_hist_norm",
)


def _d(i: int) -> dt.date:
    return _BASE + dt.timedelta(days=i)


def _wh(tmp_path: Path, series: dict[str, list[float]]) -> Warehouse:
    rows = []
    for sid, closes in series.items():
        for i, c in enumerate(closes):
            d = _d(i)
            # 高低由相鄰收盤推出，而不是固定 ±1%：完全不動的序列才會真的得到
            # high == low（KD 分母為零），ac4 要驗的就是那個情形
            prev = closes[i - 1] if i else c
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": prev,
                    "high": max(prev, c),
                    "low": min(prev, c),
                    "close": c,
                    "volume": 1000,
                    "amount": c * 1000,
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


_UP = [100.0 * 1.01**i for i in range(80)]  # 穩定上漲
_DOWN = [100.0 * 0.99**i for i in range(80)]  # 穩定下跌
_FLAT = [100.0] * 80  # 完全不動：多個指標的分母為零


def _value(wh: Warehouse, name: str, sid: str, i: int) -> float | None:
    df = fc.compute([name], wh.view(_d(79)), dates=[_d(i)])
    row = df.filter(pl.col("stock_id") == sid)
    return None if row.is_empty() else row["value"][0]


@pytest.mark.parametrize("name", NAMES)
def test_ac1_no_lookahead(name: str, tmp_path: Path) -> None:
    wh = _wh(tmp_path, {"2330": _UP, "2454": _DOWN})
    assert_no_lookahead(name, wh, [_d(60), _d(70), _d(79)])


@pytest.mark.parametrize("name", NAMES)
def test_ac2_registered_direction_is_positive(name: str) -> None:
    """五支一律 direction=+1：實測超買優於超賣，與教科書相反（模組 docstring 有表）。"""
    assert get(name).direction == 1


def test_ac3_kd_and_rsi_are_bounded_and_ordered(tmp_path: Path) -> None:
    """穩定上漲的 K／RSI 應接近上界且明顯高於穩定下跌者。"""
    wh = _wh(tmp_path, {"2330": _UP, "2454": _DOWN})
    for name in ("price.kd_k_9", "price.rsi_14"):
        up, down = _value(wh, name, "2330", 79), _value(wh, name, "2454", 79)
        assert up is not None and down is not None
        assert 0.0 <= down < up <= 100.0, f"{name}: 上漲 {up}、下跌 {down}"
        assert up > 80.0, f"{name}: 穩定上漲卻只有 {up}"


def test_ac4_zero_denominator_returns_null_not_a_filled_value(tmp_path: Path) -> None:
    """完全不動的股票：KD 的高低區間、布林的標準差都是零 → 必須是 null。

    填成 50（KD 常見做法）或 0 會讓「沒有資訊」看起來像「中性訊號」，
    在橫斷面排序裡會被當成真實排名，違反 CLAUDE.md 的禁止靜默處理。
    """
    wh = _wh(tmp_path, {"2330": _FLAT, "2454": _UP})
    for name in ("price.kd_k_9", "price.bollinger_pctb_20"):
        assert _value(wh, name, "2330", 79) is None, f"{name} 對不動的股票給了值"
        assert _value(wh, name, "2454", 79) is not None, f"{name} 對正常股票沒給值"
    # 帶寬是例外：分母是均價（不會為零），帶寬 0 是「完全沒波動」這個真實量測，
    # 不是缺資料。把它也當成 null 會丟掉資訊。
    assert _value(wh, "price.bollinger_width_20", "2330", 79) == 0.0


def test_ac5_macd_is_scale_free(tmp_path: Path) -> None:
    """同一條走勢、價格差 10 倍，MACD 柱狀必須相同——沒除以收盤價就會差一個量級。"""
    wh = _wh(tmp_path, {"2330": _UP, "2454": [c * 10.0 for c in _UP]})
    cheap, pricey = (
        _value(wh, "price.macd_hist_norm", "2330", 79),
        _value(wh, "price.macd_hist_norm", "2454", 79),
    )
    assert cheap is not None and pricey is not None
    assert cheap == pytest.approx(pricey, rel=1e-9)


def test_ac6_bollinger_pctb_exceeds_one_above_the_upper_band(tmp_path: Path) -> None:
    """%B 不夾在 [0,1]：站上上軌就該 > 1，那正是實測有效的那一段（+2.07pp）。"""
    # 77 天不動後連三根漲停（台股單日上限 10%）：布林帶來不及跟上，收盤穿出上軌
    wh = _wh(tmp_path, {"2330": [*_FLAT[:77], 110.0, 121.0, 133.1]})
    assert (v := _value(wh, "price.bollinger_pctb_20", "2330", 79)) is not None
    assert v > 1.0, f"突破上軌後 %B 只有 {v}"
