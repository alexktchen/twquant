"""T-010／D-044：評價因子（E/P、B/P、殖利率）。

為什麼用**倒數**而非 PER／PBR 本身：實測 PER 最大 24,950、最小 −1.0，PBR 最大 1,442.5。
以 P/E 直接當因子，橫斷面分布會被少數極端值主導；取倒數後 E/P 在 PER→∞ 時趨近 0、
在虧損時為負（有意義的方向），分布遠為良好。這是資產定價文獻的標準處理。

這三個是本專案**第一個評價因子家族**——先前 25 個特徵全是價量動能與月營收，
沒有任何評價維度。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

import twquant.factors.builtins  # noqa: F401  # 註冊全部因子
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.factors import compute as fc
from twquant.factors.registry import get, list_factors
from twquant.factors.testing import assert_no_lookahead

_STOCKS = ["1101", "2330"]
_DAYS = [dt.date(2024, 6, d) for d in (3, 4, 5, 6, 7)]


def _at(d: dt.date, hour: int = 18) -> dt.datetime:
    return dt.datetime(d.year, d.month, d.day, hour, tzinfo=TZ)


def _prices(days: list[dt.date]) -> pl.DataFrame:
    rows = [
        {
            "date": d,
            "stock_id": s,
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "volume": 1000,
            "amount": 100_000.0,
            "limit_up_price": None,
            "limit_down_price": None,
            "available_at": _at(d),
        }
        for d in days
        for s in _STOCKS
    ]
    return pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(schemas.TW_DT),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )


def _valuation(rows: list[tuple[dt.date, str, float, float, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "date": d,
                "stock_id": s,
                "per": per,
                "pbr": pbr,
                "dividend_yield": dy,
                "available_at": _at(d),
            }
            for d, s, per, pbr, dy in rows
        ]
    ).with_columns(pl.col("available_at").cast(schemas.TW_DT))


def _warehouse(tmp_path: Path, val: pl.DataFrame) -> Warehouse:
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", _prices(_DAYS), source="twse")
    wh.write("clean", "stock_valuation", val, source="finmind")
    return wh


def _run(wh: Warehouse, name: str, day: dt.date) -> dict[str, float | None]:
    out = fc.compute([name], wh.view(day), dates=[day])
    return {r["stock_id"]: r["value"] for r in out.to_dicts()}


def test_all_three_valuation_factors_are_registered() -> None:
    names = list_factors()
    for n in ("fundamental.earnings_yield", "fundamental.book_yield", "fundamental.dividend_yield"):
        assert n in names, f"{n} 未註冊"


def test_directions_encode_cheap_is_bullish() -> None:
    """E/P、B/P、殖利率皆為「越高越便宜／越好」→ direction 必須是 +1。

    若沿用 PER/PBR 本身則 direction 應為 -1；用倒數就是為了讓方向一致且分布良好。
    """
    for n in ("fundamental.earnings_yield", "fundamental.book_yield", "fundamental.dividend_yield"):
        assert get(n).direction == 1, f"{n} 方向錯"


def test_earnings_yield_is_reciprocal_and_keeps_negative_per(tmp_path: Path) -> None:
    """E/P = 1/PER。負 PER（虧損公司）→ 負收益率，是有意義的值，不得轉 null。"""
    val = _valuation(
        [(d, "1101", 20.0, 2.0, 0.03) for d in _DAYS]
        + [(d, "2330", -4.0, 5.0, 0.01) for d in _DAYS]
    )
    got = _run(_warehouse(tmp_path, val), "fundamental.earnings_yield", _DAYS[-1])
    assert got["1101"] == pytest.approx(0.05)
    assert got["2330"] == pytest.approx(-0.25), "虧損公司的 E/P 應為負，不是 null"


def test_book_yield_nulls_non_positive_pbr(tmp_path: Path) -> None:
    """PBR <= 0 → null，不得產生 inf。

    這正是 T-079 的教訓：`ml/dataset.py` 少了一模一樣的正值防護，
    open=0 產生 inf 後被當日均值擴散，毀掉整個模型。
    """
    val = _valuation(
        [(d, "1101", 20.0, 4.0, 0.03) for d in _DAYS]
        + [(d, "2330", 20.0, 0.0, 0.01) for d in _DAYS]
    )
    got = _run(_warehouse(tmp_path, val), "fundamental.book_yield", _DAYS[-1])
    assert got["1101"] == pytest.approx(0.25)
    assert "2330" not in got, "PBR=0 應產出 null 並被 drop_nulls 濾掉，而非 inf"


def test_dividend_yield_passes_through_as_decimal(tmp_path: Path) -> None:
    """殖利率已在來源映射層轉為小數（黃金規則 3），因子不再二次換算。"""
    val = _valuation([(d, s, 20.0, 2.0, 0.0289) for d in _DAYS for s in _STOCKS])
    got = _run(_warehouse(tmp_path, val), "fundamental.dividend_yield", _DAYS[-1])
    assert got["1101"] == pytest.approx(0.0289)


def test_pit_unpublished_valuation_is_invisible(tmp_path: Path) -> None:
    """available_at 晚於 as_of 截點的評價資料不得可見（PIT）。"""
    late = _valuation([(d, s, 20.0, 2.0, 0.03) for d in _DAYS for s in _STOCKS]).with_columns(
        pl.col("available_at").dt.offset_by("2d")  # 兩天後才公布
    )
    got = _run(_warehouse(tmp_path, late), "fundamental.earnings_yield", _DAYS[0])
    assert got == {}, "尚未公布的評價資料被看見了"


@pytest.mark.parametrize(
    "name",
    ["fundamental.earnings_yield", "fundamental.book_yield", "fundamental.dividend_yield"],
)
def test_no_lookahead(tmp_path: Path, name: str) -> None:
    """CLAUDE.md §4：每個新因子的測試檔必須呼叫 assert_no_lookahead。"""
    val = _valuation(
        [(d, "1101", 20.0 + i, 2.0, 0.03) for i, d in enumerate(_DAYS)]
        + [(d, "2330", 15.0 + i, 3.0, 0.02) for i, d in enumerate(_DAYS)]
    )
    assert_no_lookahead(name, _warehouse(tmp_path, val), _DAYS[1:])
