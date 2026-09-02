"""T-016：主力分點因子（富邦 broker_trades：每檔每日 top-15 買超／賣超，母公司與分點混層）。

三個因子 + 一張隔日沖分點屬性表（清潔劑，非因子）：
- chip.broker_concentration_20d：Σ20 日（主力淨買超）÷ Σ20 日成交量，+
- chip.broker_count_diff_5d：Σ5 日（賣方分點數 − 買方分點數）÷ Σ5 日總分點數，+
- chip.broker_persistence_10d：近 10 日主力淨買超為正的天數比例，+

**PIT 的關鍵**：隔日沖比率用 expanding（只看過去事件），且「今買明賣」事件記在**賣出日**——
那天的賣出資料進來才知道昨天那筆是隔日沖。用全歷史算好的分點清單套回過去是前視。
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
from twquant.factors.broker import daytrade_ratio
from twquant.factors.registry import get, list_factors
from twquant.factors.testing import assert_no_lookahead

_NAMES = [
    "chip.broker_concentration_20d",
    "chip.broker_count_diff_5d",
    "chip.broker_persistence_10d",
]
_STOCKS = ["1101", "2330"]
_VOL = 10_000  # 每檔每日成交量（股）


def _days(n: int, start: dt.date = dt.date(2025, 3, 3)) -> list[dt.date]:
    out: list[dt.date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


_DAYS = _days(30)


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
            "volume": _VOL,
            "amount": 100.0 * _VOL,
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


def _bt(rows: list[tuple[dt.date, str, str, int, int]]) -> pl.DataFrame:
    """(date, stock_id, broker_id, buy_shares, sell_shares)"""
    return pl.DataFrame(
        {
            "date": [r[0] for r in rows],
            "stock_id": [r[1] for r in rows],
            "broker_id": [r[2] for r in rows],
            "buy_shares": [r[3] for r in rows],
            "sell_shares": [r[4] for r in rows],
            "avg_price": pl.Series([None] * len(rows), dtype=pl.Float64),
            "available_at": pl.Series([_at(r[0]) for r in rows], dtype=schemas.TW_DT),
        }
    )


def _steady(days: list[dt.date]) -> list[tuple[dt.date, str, str, int, int]]:
    """穩定型態：1101 每日 A 買 1000、B 賣 400（淨 +600，買方 1 家、賣方 1 家）；
    2330 每日 C 賣 500、D 賣 300、E 買 200（淨 −600，買方 1、賣方 2）。"""
    rows = []
    for d in days:
        rows += [(d, "1101", "A", 1000, 0), (d, "1101", "B", 0, 400)]
        rows += [(d, "2330", "C", 0, 500), (d, "2330", "D", 0, 300), (d, "2330", "E", 200, 0)]
    return rows


def _wh(tmp_path: Path, bt: pl.DataFrame, days: list[dt.date] = _DAYS) -> Warehouse:
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", _prices(days), source="twse")
    wh.write("clean", "broker_trades", bt, source="fubon")
    return wh


def _vals(wh: Warehouse, name: str, day: dt.date, **params: object) -> dict[str, float]:
    p = {name: params} if params else None
    out = fc.compute([name], wh.view(day), dates=[day], params=p)
    return {r["stock_id"]: r["value"] for r in out.to_dicts()}


def test_registered_with_directions_and_tables() -> None:
    names = list_factors()
    for n in _NAMES:
        assert n in names, n
        assert get(n).direction == 1
        assert "broker_trades" in get(n).tables
    assert "prices_daily" in get("chip.broker_concentration_20d").tables


def test_concentration_is_net_main_force_over_volume(tmp_path: Path) -> None:
    """1101：每日淨 +600、量 10,000 → 20 日 = 12,000 / 200,000 = 0.06；2330：−0.06。"""
    got = _vals(_wh(tmp_path, _bt(_steady(_DAYS))), "chip.broker_concentration_20d", _DAYS[-1])
    assert got["1101"] == pytest.approx(0.06) and got["2330"] == pytest.approx(-0.06)


def test_count_diff_is_sellers_minus_buyers_share(tmp_path: Path) -> None:
    """1101：賣 1 買 1 → 0；2330：賣 2 買 1 → (2−1)/3 = 1/3（5 日加總後比例相同）。"""
    got = _vals(_wh(tmp_path, _bt(_steady(_DAYS))), "chip.broker_count_diff_5d", _DAYS[-1])
    assert got["1101"] == pytest.approx(0.0) and got["2330"] == pytest.approx(1 / 3)


def test_count_diff_negative_when_buyers_outnumber_sellers(tmp_path: Path) -> None:
    """買方分點數 > 賣方 → 值為負。分點數是布林 sum（無號整數），相減不得下溢。
    真實資料跑 IC 時整個因子崩掉才發現：夾具兩檔都剛好賣 ≥ 買。"""
    rows = []
    for d in _DAYS:
        rows += [
            (d, "1101", "A", 500, 0),
            (d, "1101", "B", 300, 0),
            (d, "1101", "C", 0, 100),
        ]  # 買 2 賣 1
        rows += [(d, "2330", "D", 0, 500), (d, "2330", "E", 200, 0)]
    got = _vals(_wh(tmp_path, _bt(rows)), "chip.broker_count_diff_5d", _DAYS[-1])
    assert got["1101"] == pytest.approx((1 - 2) / 3)


def test_persistence_is_share_of_positive_net_days(tmp_path: Path) -> None:
    """1101 每日淨買 → 1.0；2330 每日淨賣 → 0.0；把 1101 最近 3 天改成淨賣 → 0.7。"""
    rows = _steady(_DAYS)
    for d in _DAYS[-3:]:
        rows = [r for r in rows if not (r[0] == d and r[1] == "1101")] + [(d, "1101", "A", 0, 900)]
    got = _vals(_wh(tmp_path, _bt(rows)), "chip.broker_persistence_10d", _DAYS[-1])
    assert got["1101"] == pytest.approx(0.7) and got["2330"] == pytest.approx(0.0)


def test_window_not_full_gives_no_value(tmp_path: Path) -> None:
    days = _days(12)
    wh = _wh(tmp_path, _bt(_steady(days)), days)
    assert _vals(wh, "chip.broker_concentration_20d", days[-1]) == {}
    assert set(_vals(wh, "chip.broker_persistence_10d", days[-1])) == set(_STOCKS)


# ── 隔日沖分點：屬性表與清潔 ─────────────────────────────────────────────


def _with_daytrader(days: list[dt.date]) -> list[tuple[dt.date, str, str, int, int]]:
    """在穩定型態上加分點 X：對 2330 每逢偶數索引日買 800、次日賣 600（典型隔日沖；每對淨 +200，
    讓「排除／不排除」在 20 日窗內的值不同）。"""
    rows = _steady(days)
    for i in range(0, len(days) - 1, 2):
        rows.append((days[i], "2330", "X", 800, 0))
        rows.append((days[i + 1], "2330", "X", 0, 600))
    return rows


def test_daytrade_ratio_is_expanding_and_attributed_to_sell_day(tmp_path: Path) -> None:
    """X 每次買後次日必賣 → 比率 1.0；A 從不隔日賣 → 0.0；事件數只算到 as_of 為止。"""
    wh = _wh(tmp_path, _bt(_with_daytrader(_DAYS)))
    tbl = daytrade_ratio(
        wh.view(_DAYS[9])
    )  # 到第 10 天（索引 9）：X 在索引 0/2/4/6/8 買、1/3/5/7/9 賣，五次都已結算
    x = tbl.filter(pl.col("broker_id") == "X").to_dicts()[0]
    assert x["n_buys"] == 5 and x["n_flip"] == 5 and x["ratio"] == pytest.approx(1.0), x
    a = tbl.filter(pl.col("broker_id") == "A").to_dicts()[0]
    assert a["n_flip"] == 0 and a["ratio"] == pytest.approx(0.0)


def test_concentration_excludes_daytrade_brokers_once_threshold_met(tmp_path: Path) -> None:
    """排除 X 後 2330 回到穩定型態 −12,000/200,000 = −0.06；不排除時 20 日窗含 10 對 (+800, −600)
    → −12,000 + 2,000 = −10,000 → −0.05。門檻由參數注入（測試用 min_events=3）。"""
    wh = _wh(tmp_path, _bt(_with_daytrader(_DAYS)))
    clean = _vals(
        wh,
        "chip.broker_concentration_20d",
        _DAYS[-1],
        daytrade_min_events=3,
        daytrade_min_ratio=0.5,
    )
    raw = _vals(wh, "chip.broker_concentration_20d", _DAYS[-1], daytrade_min_events=10_000)
    assert clean["2330"] == pytest.approx(-0.06)
    assert raw["2330"] == pytest.approx(-0.05)
    assert clean["1101"] == pytest.approx(raw["1101"])  # 1101 沒有隔日沖分點，不受影響


def test_daytrade_exclusion_is_pit_safe_before_threshold(tmp_path: Path) -> None:
    """事件數未達門檻前 X 不被排除——早期日子的值與「不排除」相同。"""
    wh = _wh(tmp_path, _bt(_with_daytrader(_DAYS)))
    day = _DAYS[21]  # X 至此累積 ~10 次翻轉；門檻設 50 → 尚未排除
    a = _vals(wh, "chip.broker_concentration_20d", day, daytrade_min_events=50)
    b = _vals(wh, "chip.broker_concentration_20d", day, daytrade_min_events=10_000)
    assert a["2330"] == pytest.approx(b["2330"])


@pytest.mark.parametrize("name", _NAMES)
def test_no_lookahead(tmp_path: Path, name: str) -> None:
    wh = _wh(tmp_path, _bt(_with_daytrader(_DAYS)))
    assert_no_lookahead(name, wh, _DAYS[-5:])
