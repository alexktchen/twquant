"""T-011 / SPEC-001 §4 §7：倉儲寫入、掃描、PIT 截點、SQL。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.errors import DataError


def _prices(date: dt.date, hour: int, stock: str = "2330", close: float = 2410.0) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [date],
            "stock_id": [stock],
            "open": [close],
            "high": [close],
            "low": [close],
            "close": [close],
            "volume": [1000],
            "amount": [close * 1000],
            "limit_up_price": pl.Series([None], dtype=pl.Float64),
            "limit_down_price": pl.Series([None], dtype=pl.Float64),
            "available_at": pl.Series(
                [dt.datetime(date.year, date.month, date.day, hour, 0, tzinfo=TZ)],
                dtype=schemas.TW_DT,
            ),
        }
    )


def test_write_scan_roundtrip(tmp_path: Path) -> None:
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", _prices(dt.date(2026, 6, 30), 18))
    got = wh.scan("prices_daily").collect()
    assert got.height == 1
    assert got.filter(pl.col("stock_id") == "2330")["close"][0] == 2410.0


def test_partition_replace_idempotent(tmp_path: Path) -> None:
    wh = Warehouse(tmp_path)
    d = dt.date(2026, 6, 30)
    wh.write("clean", "prices_daily", _prices(d, 18, close=2410.0))
    wh.write("clean", "prices_daily", _prices(d, 18, close=2500.0))  # 同分區重寫
    got = wh.scan("prices_daily").collect()
    assert got.height == 1  # 不累加
    assert got["close"][0] == 2500.0  # 等於最後一次


def test_pit_cutoff_d008(tmp_path: Path) -> None:
    # D-008：date 型 as_of coerce 到 20:00。18:00 的價格看得到，21:00 的融資看不到。
    wh = Warehouse(tmp_path)
    d = dt.date(2026, 6, 30)
    wh.write("clean", "prices_daily", _prices(d, 18))
    margin = pl.DataFrame(
        {
            "date": [d],
            "stock_id": ["2330"],
            "margin_balance": [1000],
            "short_balance": [0],
            "available_at": pl.Series(
                [dt.datetime(2026, 6, 30, 21, 0, tzinfo=TZ)], dtype=schemas.TW_DT
            ),
        }
    )
    wh.write("clean", "margin_daily", margin)

    view = wh.view(d)  # cutoff = 20:00
    assert view.table("prices_daily").collect().height == 1  # 18:00 <= 20:00
    assert view.table("margin_daily").collect().height == 0  # 21:00 > 20:00

    view_next = wh.view(dt.date(2026, 7, 1))  # cutoff 次日 20:00
    assert view_next.table("margin_daily").collect().height == 1


def test_multi_date_scan_and_latest(tmp_path: Path) -> None:
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", _prices(dt.date(2026, 6, 29), 18))
    wh.write("clean", "prices_daily", _prices(dt.date(2026, 6, 30), 18))
    assert wh.scan("prices_daily").collect().height == 2
    assert wh.latest_date("prices_daily") == dt.date(2026, 6, 30)


def test_sql_analysis(tmp_path: Path) -> None:
    wh = Warehouse(tmp_path)
    df = pl.concat(
        [
            _prices(dt.date(2026, 6, 30), 18, stock="2330", close=2410.0),
            _prices(dt.date(2026, 6, 30), 18, stock="2317", close=250.0),
        ]
    )
    wh.write("clean", "prices_daily", df)
    out = wh.sql("SELECT count(*) AS n, max(close) AS mx FROM prices_daily")
    assert out["n"][0] == 2
    assert out["mx"][0] == 2410.0


def test_multi_source_coexist(tmp_path: Path) -> None:
    # 上市(twse) 與上櫃(tpex) 寫進同表同分區，各自 part 檔不互相覆蓋（T-008）
    wh = Warehouse(tmp_path)
    d = dt.date(2026, 6, 30)
    wh.write("clean", "prices_daily", _prices(d, 18, stock="2330"), source="twse")
    wh.write("clean", "prices_daily", _prices(d, 18, stock="5483", close=180.0), source="tpex")
    got = wh.scan("prices_daily").collect()
    assert got.height == 2
    assert set(got["stock_id"]) == {"2330", "5483"}
    # 同來源重寫仍為替換（不累加）
    wh.write("clean", "prices_daily", _prices(d, 18, stock="2330", close=2500.0), source="twse")
    got2 = wh.scan("prices_daily").collect()
    assert got2.height == 2
    assert got2.filter(pl.col("stock_id") == "2330")["close"][0] == 2500.0


def test_partition_exists(tmp_path: Path) -> None:
    wh = Warehouse(tmp_path)
    d = dt.date(2026, 6, 30)
    assert not wh.partition_exists("clean", "prices_daily", d, "twse")
    wh.write("clean", "prices_daily", _prices(d, 18), source="twse")
    assert wh.partition_exists("clean", "prices_daily", d, "twse")
    assert not wh.partition_exists("clean", "prices_daily", d, "tpex")


def test_scan_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(DataError, match="無資料"):
        Warehouse(tmp_path).scan("prices_daily").collect()
