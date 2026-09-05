"""共用測試 fixture：建構供監控快照/API 測試用的小型 clean 資料湖。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse

_D1 = dt.date(2026, 6, 29)
_D2 = dt.date(2026, 6, 30)
_REV_END = dt.date(2026, 5, 31)


def _tw(days: list[dt.date], hour: int) -> pl.Series:
    return pl.Series(
        [dt.datetime(d.year, d.month, d.day, hour, 0, tzinfo=TZ) for d in days],
        dtype=schemas.TW_DT,
    )


def _prices(rows: list[tuple[dt.date, str, float, float]]) -> pl.DataFrame:
    """(date, stock_id, close, amount) → prices_daily 一批（OHLC 全設為 close）。"""
    dates = [r[0] for r in rows]
    close = [r[2] for r in rows]
    return pl.DataFrame(
        {
            "date": dates,
            "stock_id": [r[1] for r in rows],
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": [1000 for _ in rows],
            "amount": [r[3] for r in rows],
            "limit_up_price": pl.Series([None] * len(rows), dtype=pl.Float64),
            "limit_down_price": pl.Series([None] * len(rows), dtype=pl.Float64),
            "available_at": _tw(dates, 18),
        }
    )


def _institutional(date: dt.date, rows: list[tuple[str, int, int, int]]) -> pl.DataFrame:
    """(stock_id, foreign_net, trust_net, dealer_net) → institutional_daily 一批。"""
    return pl.DataFrame(
        {
            "date": [date] * len(rows),
            "stock_id": [r[0] for r in rows],
            "foreign_net": [r[1] for r in rows],
            "trust_net": [r[2] for r in rows],
            "dealer_net": [r[3] for r in rows],
            "available_at": _tw([date] * len(rows), 18),
        }
    )


def _revenue(ym: str, rows: list[tuple[str, float, float]]) -> pl.DataFrame:
    """(stock_id, revenue, yoy) → monthly_revenue 一批。"""
    return pl.DataFrame(
        {
            "date": [_REV_END] * len(rows),
            "year_month": [ym] * len(rows),
            "stock_id": [r[0] for r in rows],
            "period_end": [_REV_END] * len(rows),
            "revenue": [r[1] for r in rows],
            "yoy": [r[2] for r in rows],
            "available_at": _tw([_REV_END] * len(rows), 22),
            "available_at_estimated": [False] * len(rows),
        }
    )


def build_snapshot_warehouse(root: Path) -> Warehouse:
    """兩檔個股 × 兩日行情 + 法人 + 月營收的小資料湖（依來源分檔，供缺口/涵蓋測試）。"""
    wh = Warehouse(root)
    wh.write(
        "clean",
        "prices_daily",
        _prices(
            [
                (_D1, "2330", 1000.0, 5e8),
                (_D2, "2330", 1010.0, 6e8),
                (_D1, "2317", 100.0, 3e8),
                (_D2, "2317", 99.0, 2e8),
            ]
        ),
        source="twse",
    )
    wh.write(
        "clean",
        "institutional_daily",
        _institutional(_D2, [("2330", 100000, 50000, 1000), ("2317", -20000, 30000, -500)]),
        source="twse",
    )
    wh.write(
        "clean",
        "monthly_revenue",
        _revenue("2026-05", [("2330", 2e9, 0.35), ("2317", 8e8, 0.12)]),
        source="mops_sii",
    )
    return wh


@pytest.fixture
def snapshot_wh(tmp_path: Path) -> Warehouse:
    """建好的小資料湖（給 monitoring.snapshot 與 monitoring.api 測試共用）。"""
    return build_snapshot_warehouse(tmp_path)


def write_stock_meta(wh: Warehouse, prices: pl.DataFrame, market: str = "TWSE") -> None:
    """依行情裡出現的股票補一張 `stock_meta`（T-143 的 `markets` 濾網需要它）。

    濾網上線後宇宙需要市場別，缺表時 `universe_ids` 會 raise（黃金規則 5：不得靜默放行，
    否則濾網會在缺表時「成功地什麼都沒過濾」）。測試 fixture 的股票都是正常上市櫃標的，
    故一律標為可交易市場——這不是放寬測試，是把 fixture 補成合法的資料湖。
    """
    ids = prices["stock_id"].unique().sort().to_list()
    n = len(ids)
    wh.write(
        "clean",
        "stock_meta",
        pl.DataFrame(
            {
                "stock_id": ids,
                "name": pl.Series(ids, dtype=pl.String),
                "market": pl.Series([market] * n, dtype=pl.String),
                "industry": pl.Series([None] * n, dtype=pl.String),
                "list_date": pl.Series([None] * n, dtype=pl.Date),
                "delist_date": pl.Series([None] * n, dtype=pl.Date),
            }
        ),
        source=market.lower(),
    )
