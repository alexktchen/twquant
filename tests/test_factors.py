"""T-012 / SPEC-002：因子框架（註冊表、compute、標準化、反前視護欄）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import PitView, Warehouse
from twquant.errors import TwquantError
from twquant.factors import compute as fc
from twquant.factors import price as _price  # noqa: F401  # 觸發 mom_20d 註冊
from twquant.factors import registry
from twquant.factors.registry import factor, get, list_factors
from twquant.factors.standardize import standardize
from twquant.factors.testing import assert_no_lookahead


def _synthetic_prices(days: int, stocks: dict[str, float]) -> pl.DataFrame:
    """days 個交易日、每檔股價從基準每日 +1 遞增。日期用 2026-06 起連續平日近似。"""
    base = dt.date(2026, 6, 1)
    rows = []
    for i in range(days):
        d = base + dt.timedelta(days=i)
        at = dt.datetime(d.year, d.month, d.day, 18, 0, tzinfo=TZ)
        for sid, p0 in stocks.items():
            c = p0 + i
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": c,
                    "high": c,
                    "low": c,
                    "close": c,
                    "volume": 1000,
                    "amount": c * 1000.0,
                    "limit_up_price": None,
                    "limit_down_price": None,
                    "available_at": at,
                }
            )
    return pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(schemas.TW_DT),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )


def _warehouse(tmp_path: Path, days: int = 25) -> Warehouse:
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", _synthetic_prices(days, {"0001": 100.0, "0002": 50.0}))
    return wh


# ── 註冊表 ──────────────────────────────────────────────────────


def test_ac1_mom20d_registered() -> None:
    assert "price.mom_20d" in list_factors()
    assert "price.mom_20d" in list_factors(category="price")
    fdef = get("price.mom_20d")
    assert fdef.direction == 1 and fdef.anchor_invariant is True


def test_ac1_duplicate_register_raises() -> None:
    with pytest.raises(TwquantError, match="已註冊"):

        @factor(name="price.mom_20d", category="price", tables=["prices_daily"], lookback_days=25)
        def _dup(view: PitView, params: dict[str, object]) -> pl.DataFrame:
            return pl.DataFrame()


def test_bad_name_format_raises() -> None:
    with pytest.raises(TwquantError, match="格式不符"):
        registry.register(registry.FactorDef("price.Bad-Name", "price", 1, ("prices_daily",), 5))


def test_category_mismatch_raises() -> None:
    with pytest.raises(TwquantError, match="前綴"):
        registry.register(registry.FactorDef("chip.x", "price", 1, ("prices_daily",), 5))


# ── compute ─────────────────────────────────────────────────────


def test_compute_mom20d_values(tmp_path: Path) -> None:
    wh = _warehouse(tmp_path, days=25)
    out = fc.compute(["price.mom_20d"], wh.view(dt.date(2026, 6, 25)))
    assert out.columns == ["date", "stock_id", "factor", "value"]
    # 第 21 天(index 20, 2026-06-21) 0001：close=120, 20 日前 close=100 → 0.2
    row = out.filter(
        (pl.col("stock_id") == "0001") & (pl.col("date") == dt.date(2026, 6, 21))
    ).to_dicts()[0]
    assert row["value"] == pytest.approx(120 / 100 - 1)
    assert row["factor"] == "price.mom_20d"


def test_compute_dates_filter(tmp_path: Path) -> None:
    wh = _warehouse(tmp_path, days=25)
    d = dt.date(2026, 6, 25)
    out = fc.compute(["price.mom_20d"], wh.view(d), dates=[d])
    assert set(out["date"].to_list()) == {d}


# ── 標準化（AC-4）─────────────────────────────────────────────────


def test_ac4_standardize_zero_mean_unit_std() -> None:
    df = pl.DataFrame(
        {
            "date": [dt.date(2026, 6, 30)] * 5,
            "stock_id": ["a", "b", "c", "d", "e"],
            "factor": ["price.x"] * 5,
            "value": [1.0, 2.0, 3.0, 4.0, 100.0],  # 含極端值
        }
    )
    std = standardize(df)
    m = std.select(pl.col("value").mean()).item()
    s = std.select(pl.col("value").std()).item()
    mx = std.select(pl.col("value").max()).item()
    assert abs(m) < 1e-9
    assert abs(s - 1.0) < 1e-6
    # 極端值被 winsorize 拉回，不再是離群的巨值
    assert mx < 3.0


def test_ac4_standardize_nan_one_stock_does_not_nuke_group() -> None:
    """D-020 回歸防線：某 (date,factor) 群內單一 NaN 不得汙染同群其他股票的 z，
    也不得影響另一群。退化股自身維持非有限，供下游以 is_finite 過濾。"""
    d = dt.date(2026, 6, 30)
    df = pl.DataFrame(
        {
            "date": [d] * 8,
            "stock_id": ["a", "b", "c", "bad", "a", "b", "c", "d"],
            "factor": ["price.x"] * 4 + ["price.y"] * 4,
            "value": [1.0, 2.0, 3.0, float("nan"), 10.0, 20.0, 30.0, 40.0],
        }
    )
    std = standardize(df)

    # 含 NaN 的 price.x 群：其他三檔仍得有限 z，且橫斷面 mean≈0 / std≈1（NaN 不參與估計）
    x_good = std.filter((pl.col("factor") == "price.x") & (pl.col("stock_id") != "bad"))
    assert x_good.select(pl.col("value").is_finite().all()).item()
    assert abs(x_good.select(pl.col("value").mean()).item()) < 1e-9
    assert abs(x_good.select(pl.col("value").std()).item() - 1.0) < 1e-6

    # 退化股自身維持 NaN（≠ 自己），下游 filter(is_finite) 才能剔除它而非整因子消失
    bad_z = std.filter(pl.col("stock_id") == "bad").select("value").item()
    assert bad_z != bad_z

    # 另一群 price.y 完全不受鄰群 NaN 影響
    y = std.filter(pl.col("factor") == "price.y")
    assert y.select(pl.col("value").is_finite().all()).item()
    assert abs(y.select(pl.col("value").mean()).item()) < 1e-9
    assert abs(y.select(pl.col("value").std()).item() - 1.0) < 1e-6


# ── 反前視護欄（AC-3）────────────────────────────────────────────


def test_ac3_mom20d_no_lookahead(tmp_path: Path) -> None:
    wh = _warehouse(tmp_path, days=25)
    assert_no_lookahead(
        "price.mom_20d",
        wh,
        [dt.date(2026, 6, 22), dt.date(2026, 6, 24), dt.date(2026, 6, 25)],
    )


@factor(
    name="price.bad_lookahead",
    category="price",
    tables=["prices_daily"],
    lookback_days=2,
    params={},
)
def _bad_peek(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """故意偷看未來（shift(-1) = 次日收盤）的壞因子，供護欄反向測試。"""
    prices = (
        view.table("prices_daily").select("date", "stock_id", "close").sort(["stock_id", "date"])
    )
    nxt = pl.col("close").shift(-1).over("stock_id", order_by="date")
    return (
        prices.with_columns((nxt / pl.col("close") - 1.0).alias("value"))
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .collect()
    )


def test_ac3_guard_catches_lookahead(tmp_path: Path) -> None:
    wh = _warehouse(tmp_path, days=10)
    with pytest.raises(AssertionError, match="前視"):
        assert_no_lookahead("price.bad_lookahead", wh, [dt.date(2026, 6, 5), dt.date(2026, 6, 8)])
