"""T-099：產業層因子——產業動能（Moskowitz–Grinblatt 1999）與產業相對強弱。

產業別取 `stock_meta.industry`（緩變維度、現值套用全歷史，與 residual_reversal_5d 同一近似）。
兩個容易錯的地方：
1. 產業動能要**跳過最近一週**（t−5 到 t−126），否則把短期反轉混進動能。
2. 產業相對強弱的基準是加權指數（`index_daily` 的 TAIEX），不是全市場等權。
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
from twquant.factors.registry import get, list_factors
from twquant.factors.testing import assert_no_lookahead

# 三檔兩產業：A = {1101, 1102}、B = {2330}；9999 不在 stock_meta（無產業）
_GROWTH = {"1101": 0.002, "1102": 0.004, "2330": 0.001, "9999": 0.003}
_INDUSTRY = {"1101": "A", "1102": "A", "2330": "B"}
_TAIEX_G = 0.0015


def _days(n: int, start: dt.date = dt.date(2025, 1, 2)) -> list[dt.date]:
    out: list[dt.date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


_DAYS = _days(150)


def _at(d: dt.date, hour: int = 18) -> dt.datetime:
    return dt.datetime(d.year, d.month, d.day, hour, tzinfo=TZ)


def _price(sid: str, t: int) -> float:
    return 100.0 * (1.0 + _GROWTH[sid]) ** t


def _prices(days: list[dt.date]) -> pl.DataFrame:
    rows = []
    for t, d in enumerate(days):
        for s in _GROWTH:
            p = _price(s, t)
            rows.append(
                {
                    "date": d,
                    "stock_id": s,
                    "open": p,
                    "high": p,
                    "low": p,
                    "close": p,
                    "volume": 1000,
                    "amount": p * 1000,
                    "limit_up_price": None,
                    "limit_down_price": None,
                    "available_at": _at(d),
                }
            )
    return pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(schemas.TW_DT),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )


def _index(days: list[dt.date]) -> pl.DataFrame:
    rows = [
        {
            "date": d,
            "index_id": "TAIEX",
            "open": None,
            "high": None,
            "low": None,
            "close": 20000.0 * (1.0 + _TAIEX_G) ** t,
            "available_at": _at(d),
        }
        for t, d in enumerate(days)
    ]
    return pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(schemas.TW_DT),
        pl.col("open").cast(pl.Float64),
        pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64),
    )


def _meta() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "stock_id": list(_INDUSTRY),
            "name": ["a", "b", "c"],
            "market": ["TWSE"] * 3,
            "industry": list(_INDUSTRY.values()),
            "list_date": [dt.date(2000, 1, 1)] * 3,
            "delist_date": [None] * 3,
        }
    ).with_columns(pl.col("delist_date").cast(pl.Date))


def _wh(tmp_path: Path, days: list[dt.date] = _DAYS) -> Warehouse:
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", _prices(days), source="twse")
    wh.write("clean", "index_daily", _index(days), source="twse")
    wh.write("clean", "stock_meta", _meta(), source="twse")
    return wh


def _vals(wh: Warehouse, name: str, day: dt.date) -> dict[str, float]:
    out = fc.compute([name], wh.view(day), dates=[day])
    return {r["stock_id"]: r["value"] for r in out.to_dicts()}


def _ret(sid: str, t_from: int, t_to: int) -> float:
    return _price(sid, t_to) / _price(sid, t_from) - 1.0


def test_registered_with_directions() -> None:
    names = list_factors()
    assert "price.industry_mom_6m" in names and "price.industry_rs_20d" in names
    assert get("price.industry_mom_6m").direction == 1
    assert get("price.industry_rs_20d").direction == 1
    assert "stock_meta" in get("price.industry_mom_6m").tables
    assert "index_daily" in get("price.industry_rs_20d").tables


def test_industry_mom_is_equal_weight_mean_with_one_week_skip(tmp_path: Path) -> None:
    """t=149：窗 = close(t−5)/close(t−126) − 1，產業 A 取 1101、1102 等權平均。"""
    t = len(_DAYS) - 1
    got = _vals(_wh(tmp_path), "price.industry_mom_6m", _DAYS[t])
    exp_a = (_ret("1101", t - 126, t - 5) + _ret("1102", t - 126, t - 5)) / 2
    assert got["1101"] == pytest.approx(exp_a) and got["1102"] == pytest.approx(exp_a)
    # 若沒跳過最近一週（用 t 而非 t−5），值會不同——鎖死 skip
    assert got["1101"] != pytest.approx((_ret("1101", t - 126, t) + _ret("1102", t - 126, t)) / 2)


def test_industry_mom_single_member_industry_equals_own_return(tmp_path: Path) -> None:
    t = len(_DAYS) - 1
    got = _vals(_wh(tmp_path), "price.industry_mom_6m", _DAYS[t])
    assert got["2330"] == pytest.approx(_ret("2330", t - 126, t - 5))


def test_stock_without_industry_gets_no_value(tmp_path: Path) -> None:
    t = len(_DAYS) - 1
    for name in ("price.industry_mom_6m", "price.industry_rs_20d"):
        assert "9999" not in _vals(_wh(tmp_path), name, _DAYS[t]), name


def test_industry_rs_is_industry_return_minus_taiex(tmp_path: Path) -> None:
    t = len(_DAYS) - 1
    got = _vals(_wh(tmp_path), "price.industry_rs_20d", _DAYS[t])
    ind_a = (_ret("1101", t - 20, t) + _ret("1102", t - 20, t)) / 2
    taiex = (1.0 + _TAIEX_G) ** 20 - 1.0
    assert got["1101"] == pytest.approx(ind_a - taiex)
    assert got["2330"] == pytest.approx(_ret("2330", t - 20, t) - taiex)


def test_industry_mom_needs_full_window_else_no_value(tmp_path: Path) -> None:
    """只有 100 個交易日 → 126 日窗不足 → 無值（不得用較短窗充數）。"""
    days = _days(100)
    assert _vals(_wh(tmp_path, days), "price.industry_mom_6m", days[-1]) == {}


@pytest.mark.parametrize("name", ["price.industry_mom_6m", "price.industry_rs_20d"])
def test_no_lookahead(tmp_path: Path, name: str) -> None:
    assert_no_lookahead(name, _wh(tmp_path), _DAYS[-4:])
