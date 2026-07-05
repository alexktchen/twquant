"""T-014：價量因子（52週高接近度、5日反轉、量比、量能 z-score）。"""

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
from twquant.factors.registry import get, list_factors
from twquant.factors.testing import assert_no_lookahead


def _wh(tmp_path: Path, closes: list[float], vols: list[int]) -> Warehouse:
    base = dt.date(2026, 5, 1)
    rows = []
    for i, (c, v) in enumerate(zip(closes, vols, strict=True)):
        d = base + dt.timedelta(days=i)
        rows.append(
            {
                "date": d,
                "stock_id": "2330",
                "open": c,
                "high": c,
                "low": c,
                "close": c,
                "volume": v,
                "amount": c * v,
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


def test_registered_and_directions() -> None:
    names = list_factors()
    for n in (
        "price.high_52w",
        "price.reversal_5d",
        "volume.vol_ratio_5d",
        "volume.turnover_z_20d",
    ):
        assert n in names, n
    assert get("price.reversal_5d").direction == -1
    assert get("price.high_52w").direction == 1
    assert "volume.vol_ratio_5d" in list_factors(category="volume")


def test_high_52w_proximity(tmp_path: Path) -> None:
    # 直接呼叫因子函數並覆寫 window=5：上漲序列，最後一日貼齊高點 → 比值 = 1.0
    from twquant.factors.price import high_52w

    closes = [100.0 + i for i in range(11)]
    wh = _wh(tmp_path, closes, [1000] * 11)
    out = high_52w(wh.view(dt.date(2026, 5, 11)), {"window": 5}).sort("date")
    # close(t)/近5日最高：上漲序列最高即今日 → 全部 = 1.0（值域 ≤ 1）
    assert out["value"].to_list()[-1] == pytest.approx(1.0)
    assert all(v <= 1.0 + 1e-9 for v in out["value"].to_list())


def test_reversal_5d_value(tmp_path: Path) -> None:
    closes = [100.0] * 5 + [110.0]  # 第 6 天 close=110，5 日前=100 → 5 日報酬 0.1
    wh = _wh(tmp_path, closes, [1000] * 6)
    out = fc.compute(["price.reversal_5d"], wh.view(dt.date(2026, 5, 6)))
    row = out.filter(pl.col("date") == dt.date(2026, 5, 6)).to_dicts()[0]
    assert row["value"] == pytest.approx(0.1)


def test_vol_ratio_5d_value(tmp_path: Path) -> None:
    vols = [1000] * 5 + [3000]  # 第 6 天量 3000、前 5 日均量 1000 → 量比 3.0
    wh = _wh(tmp_path, [100.0] * 6, vols)
    out = fc.compute(["volume.vol_ratio_5d"], wh.view(dt.date(2026, 5, 6)))
    row = out.filter(pl.col("date") == dt.date(2026, 5, 6)).to_dicts()[0]
    assert row["value"] == pytest.approx(3.0)


def test_no_lookahead_price_volume(tmp_path: Path) -> None:
    closes = [100.0 + (i % 3) for i in range(15)]
    vols = [1000 + 100 * (i % 4) for i in range(15)]
    wh = _wh(tmp_path, closes, vols)
    cuts = [dt.date(2026, 5, 10), dt.date(2026, 5, 13), dt.date(2026, 5, 15)]
    assert_no_lookahead("price.reversal_5d", wh, cuts)
    assert_no_lookahead("volume.vol_ratio_5d", wh, cuts)
    assert_no_lookahead("volume.turnover_z_20d", wh, cuts)
