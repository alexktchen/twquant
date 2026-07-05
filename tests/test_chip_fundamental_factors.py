"""T-015：籌碼 + 基本面因子（投信買超、連買、散戶密集度代理、月營收動能 PEAD）。

重點在 PIT 正確性——月營收因子以公布日 available_at 映射到交易日，不得偷看未公布營收。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

import twquant.factors.builtins  # noqa: F401  # 註冊 chip/fundamental/price
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.factors import compute as fc
from twquant.factors.registry import get, list_factors
from twquant.factors.testing import assert_no_lookahead

_STOCKS = ["1101", "2330"]


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


def _inst(days: list[dt.date], trust: dict[str, list[int]]) -> pl.DataFrame:
    rows = [
        {
            "date": d,
            "stock_id": s,
            "foreign_net": 0,
            "trust_net": trust[s][i],
            "dealer_net": 0,
            "available_at": _at(d),
        }
        for i, d in enumerate(days)
        for s in _STOCKS
    ]
    return pl.DataFrame(rows).with_columns(pl.col("available_at").cast(schemas.TW_DT))


def _revenue(spec: list[tuple[str, dt.date, dt.date, float]]) -> pl.DataFrame:
    """spec: (year_month, period_end, available_at_date, yoy)；每列對兩檔股票各建一筆。"""
    rows = [
        {
            "date": pe,
            "year_month": ym,
            "stock_id": s,
            "period_end": pe,
            "revenue": 1.0e9,
            "yoy": yoy,
            "available_at": _at(av, 8),
            "available_at_estimated": False,
        }
        for (ym, pe, av, yoy) in spec
        for s in _STOCKS
    ]
    return pl.DataFrame(rows).with_columns(pl.col("available_at").cast(schemas.TW_DT))


def _warehouse(tmp_path: Path, days: list[dt.date]) -> Warehouse:
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", _prices(days), source="twse")
    return wh


def _range(start: dt.date, n: int) -> list[dt.date]:
    return [start + dt.timedelta(days=i) for i in range(n)]


# ── 註冊 ─────────────────────────────────────────────────────────


def test_factors_registered() -> None:
    names = list_factors()
    for n in (
        "chip.trust_net_10d",
        "chip.foreign_net_10d",
        "chip.trust_streak",
        "chip.margin_velocity_5d",
        "fundamental.revenue_yoy",
        "fundamental.revenue_accel",
    ):
        assert n in names, n
    assert get("chip.margin_velocity_5d").direction == -1
    assert get("chip.trust_net_10d").direction == 1
    assert "chip.trust_net_10d" in list_factors(category="chip")
    assert "fundamental.revenue_yoy" in list_factors(category="fundamental")


# ── chip.trust_net_10d：Σ10 淨買超 ÷ Σ10 量 ──────────────────────


def test_trust_net_10d_value(tmp_path: Path) -> None:
    days = _range(dt.date(2026, 6, 1), 12)
    wh = _warehouse(tmp_path, days)
    trust = {s: [100] * 12 for s in _STOCKS}  # 每日買超 100 股、量 1000 股
    wh.write("clean", "institutional_daily", _inst(days, trust), source="twse")
    out = fc.compute(["chip.trust_net_10d"], wh.view(days[-1]))
    row = out.filter((pl.col("stock_id") == "1101") & (pl.col("date") == days[-1])).to_dicts()[0]
    # Σ10 買超 = 1000，Σ10 量 = 10000 → 0.1
    assert row["value"] == pytest.approx(0.1)


# ── chip.trust_streak：連買天數 ──────────────────────────────────


def test_trust_streak_value(tmp_path: Path) -> None:
    days = _range(dt.date(2026, 6, 1), 8)
    wh = _warehouse(tmp_path, days)
    pattern = [10, 20, -5, 10, 10, 10, -1, 5]  # 買買賣買買買賣買
    trust = {s: pattern for s in _STOCKS}
    wh.write("clean", "institutional_daily", _inst(days, trust), source="twse")
    out = (
        fc.compute(["chip.trust_streak"], wh.view(days[-1]))
        .filter(pl.col("stock_id") == "1101")
        .sort("date")
    )
    assert out["value"].to_list() == [1.0, 2.0, 0.0, 1.0, 2.0, 3.0, 0.0, 1.0]


# ── fundamental.revenue_yoy：PIT as-of（不得偷看未公布營收）────────


def test_revenue_yoy_pit_asof(tmp_path: Path) -> None:
    days = _range(dt.date(2026, 6, 1), 20)  # 6/1 ~ 6/20
    wh = _warehouse(tmp_path, days)
    wh.write(
        "clean",
        "monthly_revenue",
        _revenue(
            [
                ("2026-04", dt.date(2026, 4, 30), dt.date(2026, 5, 10), 0.10),
                ("2026-05", dt.date(2026, 5, 31), dt.date(2026, 6, 10), 0.25),
            ]
        ),
    )
    out = (
        fc.compute(["fundamental.revenue_yoy"], wh.view(days[-1]))
        .filter(pl.col("stock_id") == "2330")
        .sort("date")
    )
    val = {r["date"]: r["value"] for r in out.to_dicts()}
    # 6/9：5 月營收（6/10 公布）尚未可見 → 仍是 4 月的 0.10
    assert val[dt.date(2026, 6, 9)] == pytest.approx(0.10)
    # 6/10 起：5 月營收已公布 → 0.25
    assert val[dt.date(2026, 6, 10)] == pytest.approx(0.25)
    assert val[dt.date(2026, 6, 15)] == pytest.approx(0.25)


# ── 反前視護欄（每個因子家族抽一支代表）──────────────────────────


def test_no_lookahead_chip(tmp_path: Path) -> None:
    days = _range(dt.date(2026, 6, 1), 15)
    wh = _warehouse(tmp_path, days)
    trust = {s: [100, -50, 100, 100, -20] * 3 for s in _STOCKS}
    wh.write("clean", "institutional_daily", _inst(days, trust), source="twse")
    cuts = [days[10], days[12], days[14]]
    assert_no_lookahead("chip.trust_net_10d", wh, cuts)
    assert_no_lookahead("chip.trust_streak", wh, cuts)


def test_no_lookahead_revenue(tmp_path: Path) -> None:
    days = _range(dt.date(2026, 6, 1), 20)
    wh = _warehouse(tmp_path, days)
    wh.write(
        "clean",
        "monthly_revenue",
        _revenue(
            [
                ("2026-04", dt.date(2026, 4, 30), dt.date(2026, 5, 10), 0.10),
                ("2026-05", dt.date(2026, 5, 31), dt.date(2026, 6, 10), 0.25),
            ]
        ),
    )
    assert_no_lookahead("fundamental.revenue_yoy", wh, [days[9], days[12], days[18]])
