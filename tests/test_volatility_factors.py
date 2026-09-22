"""T-284：波動類八支因子（公式由 D-143 §十 事前寫死）。

每支都要過 `assert_no_lookahead`（CLAUDE.md §4 的強制護欄），另釘住四件容易寫錯的事：
窗內不足回 null 而非用短窗充數、分母為零回 null 而非填中性值、上下行拆解的方向正確、
以及跳躍門檻用前一日波動（否則跳躍那天會自己撐大門檻把自己藏起來）。
"""

from __future__ import annotations

import datetime as dt
import math
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
    "price.atr_14",
    "price.parkinson_20",
    "price.garman_klass_20",
    "price.upside_vol_20",
    "price.downside_vol_20",
    "price.vol_asym_20",
    "price.ret_skew_60",
    "price.jump_count_60",
)


def _d(i: int) -> dt.date:
    return _BASE + dt.timedelta(days=i)


def _wh(tmp_path: Path, series: dict[str, list[float]]) -> Warehouse:
    rows = []
    for sid, closes in series.items():
        for i, c in enumerate(closes):
            d = _d(i)
            # 高低由相鄰收盤推出：完全不動的序列才會真的得到 high == low，
            # 那正是 Parkinson 分母為零、上下行半變異數同時為零的情形
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


_N = 120
_UP = [100.0 * 1.01**i for i in range(_N)]  # 穩定上漲：只有上行波動
_DOWN = [100.0 * 0.99**i for i in range(_N)]  # 穩定下跌：只有下行波動
_FLAT = [100.0] * _N  # 完全不動：多個估計量的分母為零
_ZIGZAG = [100.0 * (1.01 if i % 2 else 0.99) ** 1 for i in range(_N)]


def _value(wh: Warehouse, name: str, sid: str, i: int) -> float | None:
    df = fc.compute([name], wh.view(_d(_N - 1)), dates=[_d(i)])
    row = df.filter(pl.col("stock_id") == sid)
    return None if row.is_empty() else row["value"][0]


@pytest.mark.parametrize("name", NAMES)
def test_ac1_no_lookahead(name: str, tmp_path: Path) -> None:
    wh = _wh(tmp_path, {"2330": _UP, "2454": _DOWN})
    assert_no_lookahead(name, wh, [_d(90), _d(105), _d(_N - 1)])


@pytest.mark.parametrize("name", NAMES)
def test_ac2_insufficient_window_returns_null(name: str, tmp_path: Path) -> None:
    """窗內不足一律 null——不得用較短的窗充數（黃金規則 5）。"""
    wh = _wh(tmp_path, {"2330": _UP})
    assert _value(wh, name, "2330", 0) is None, f"{name} 在第一天就給了值"


@pytest.mark.parametrize("name", NAMES)
def test_ac3_registered_lookback_covers_the_window(name: str) -> None:
    """`lookback_days` 必須大於等於計算窗，否則物化時會拿不到足夠歷史。"""
    fdef = get(name)
    window = fdef.params.get("window", 0)
    assert isinstance(window, int)
    assert fdef.lookback_days >= window, f"{name}: lookback {fdef.lookback_days} < window {window}"


def test_ac4_zero_denominator_returns_null_not_a_filled_value(tmp_path: Path) -> None:
    """完全不動的股票：Parkinson 的 ln(H/L)、上下行波動都是零 → 必須是 null 或 0，不得填中性值。

    `vol_asym_20` 是重點：上下行都為零時比值未定義，填 0（＝「對稱」）會讓
    「沒有資訊」在橫斷面排序裡變成真實排名，正是禁止靜默處理要防的事。
    """
    # 對照組要有漲有跌，否則它自己也會因為單邊為零而回 null（見 ac5）
    wh = _wh(tmp_path, {"2330": _FLAT, "2454": _ZIGZAG})
    assert _value(wh, "price.vol_asym_20", "2330", _N - 1) is None
    assert _value(wh, "price.parkinson_20", "2330", _N - 1) == 0.0  # 真的沒波動，不是缺資料
    assert _value(wh, "price.vol_asym_20", "2454", _N - 1) is not None


def test_ac5_semivariance_splits_direction(tmp_path: Path) -> None:
    """穩定上漲只有上行波動、穩定下跌只有下行波動——這是修「會動不會漲」的關鍵。"""
    wh = _wh(tmp_path, {"2330": _UP, "2454": _DOWN})
    up_of_riser = _value(wh, "price.upside_vol_20", "2330", _N - 1)
    dn_of_riser = _value(wh, "price.downside_vol_20", "2330", _N - 1)
    up_of_faller = _value(wh, "price.upside_vol_20", "2454", _N - 1)
    dn_of_faller = _value(wh, "price.downside_vol_20", "2454", _N - 1)
    assert up_of_riser is not None and dn_of_riser == 0.0
    assert dn_of_faller is not None and up_of_faller == 0.0
    assert up_of_riser > 0.0 and dn_of_faller > 0.0
    # 兩檔的 vol_20d 幾乎相同（都是 1% 的日波動），vol_asym 卻必須把它們分開
    assert _value(wh, "price.vol_asym_20", "2330", _N - 1) is None  # 下行為零 → 未定義
    assert _value(wh, "price.vol_asym_20", "2454", _N - 1) is None


def test_ac6_vol_asym_sign_follows_the_dominant_side(tmp_path: Path) -> None:
    """上漲日振幅大於下跌日 → ln 比值為正；反之為負。"""
    big_up = [100.0]
    for i in range(1, _N):
        big_up.append(big_up[-1] * (1.03 if i % 2 else 0.99))
    big_down = [100.0]
    for i in range(1, _N):
        big_down.append(big_down[-1] * (1.01 if i % 2 else 0.97))
    wh = _wh(tmp_path, {"2330": big_up, "2454": big_down})
    hi = _value(wh, "price.vol_asym_20", "2330", _N - 1)
    lo = _value(wh, "price.vol_asym_20", "2454", _N - 1)
    assert hi is not None and lo is not None
    assert hi > 0.0 > lo, f"上行主導 {hi}、下行主導 {lo}"


def test_ac7_parkinson_matches_the_closed_form(tmp_path: Path) -> None:
    """固定 1% 高低比的序列，Parkinson 應等於 ln(1.01)/√(4·ln2)。"""
    wh = _wh(tmp_path, {"2330": _ZIGZAG})
    got = _value(wh, "price.parkinson_20", "2330", _N - 1)
    expected = math.log(1.01 / 0.99) / math.sqrt(4.0 * math.log(2.0))
    assert got is not None
    assert got == pytest.approx(expected, rel=1e-9)


def test_ac8_jump_threshold_uses_previous_day_volatility(tmp_path: Path) -> None:
    """平靜期後單日暴漲必須被記成跳躍——門檻若用含當日的波動，它會把自己藏起來。"""
    calm = [100.0 + 0.05 * (i % 2) for i in range(_N - 1)]
    wh = _wh(tmp_path, {"2330": [*calm, calm[-1] * 1.10]})
    assert (v := _value(wh, "price.jump_count_60", "2330", _N - 1)) is not None
    assert v > 0.0, "平靜期後的 +10% 沒有被認定為跳躍"
    assert _value(wh, "price.jump_count_60", "2330", _N - 2) == 0.0


def test_ac9_ret_skew_is_positive_for_right_tailed_series(tmp_path: Path) -> None:
    """多數小跌、偶爾大漲 → 右偏為正。這正是「飆」的形狀。"""
    closes = [100.0]
    for i in range(1, _N):
        closes.append(closes[-1] * (1.09 if i % 25 == 0 else 0.997))
    wh = _wh(tmp_path, {"2330": closes})
    assert (v := _value(wh, "price.ret_skew_60", "2330", _N - 1)) is not None
    assert v > 0.5, f"右偏序列的偏度只有 {v}"


def test_ac10_atr_is_scale_free(tmp_path: Path) -> None:
    """同一條走勢、價格差 10 倍，ATR/close 必須相同——沒除以收盤價就會差一個量級。"""
    wh = _wh(tmp_path, {"2330": _UP, "2454": [c * 10.0 for c in _UP]})
    cheap = _value(wh, "price.atr_14", "2330", _N - 1)
    pricey = _value(wh, "price.atr_14", "2454", _N - 1)
    assert cheap is not None and pricey is not None
    assert cheap == pytest.approx(pricey, rel=1e-9)
