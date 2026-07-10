"""T-033 / SPEC-009 §4：個股 API v2（只讀 PG，無 DuckDB 降級）。

衍生欄（累計/券資比/MoM/top 券商）以純函數測試（不需 PG，CI 可跑）；PG 計分卡形狀與
source=="pg" 需真 PG，無則 skip。
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from twquant.config import ServingConfig
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.errors import DataError
from twquant.monitoring import stock_api

_TZ = schemas.TW_DT


# ── 純衍生函數（AC-6），不碰 PG ──────────────────────────────────


def test_derive_institutional_cumsum() -> None:
    d = [dt.date(2026, 6, i) for i in (1, 2, 3)]
    rows: list[dict[str, Any]] = [
        {"date": d[0], "foreign_net": 100, "trust_net": 10},
        {"date": d[1], "foreign_net": 200, "trust_net": 20},
        {"date": d[2], "foreign_net": -50, "trust_net": 5},
    ]
    out = stock_api.derive_institutional(rows)
    assert out[-1]["cum_foreign"] == 250 and out[-1]["cum_trust"] == 35


def test_derive_margin_ratio_and_chg() -> None:
    rows: list[dict[str, Any]] = [
        {"margin_balance": 1000, "short_balance": 50},
        {"margin_balance": 1100, "short_balance": 60},
        {"margin_balance": 900, "short_balance": 45},
    ]
    out = stock_api.derive_margin(rows)
    assert out[0]["margin_chg"] is None
    assert out[-1]["margin_chg"] == -200 and out[-1]["short_chg"] == -15
    assert out[-1]["short_margin_ratio"] == pytest.approx(45 / 900)


def test_derive_revenue_mom() -> None:
    raw: list[dict[str, Any]] = [
        {"year_month": "2026-04", "revenue": 1000.0, "yoy": 0.1},
        {"year_month": "2026-05", "revenue": 1200.0, "yoy": 0.2},
        {"year_month": "2026-06", "revenue": 1500.0, "yoy": 0.25},
    ]
    out = stock_api.derive_revenue(raw, 36)
    assert out[0]["mom"] is None
    assert out[-1]["mom"] == pytest.approx(1500 / 1200 - 1) and out[-1]["month"] == "2026-06"


def test_derive_brokers_topbuy_topsell() -> None:
    d = dt.date(2026, 6, 1)
    rows: list[dict[str, Any]] = [
        {"date": d, "broker_id": "甲券", "buy_shares": 5000, "sell_shares": 0},
        {"date": d, "broker_id": "乙券", "buy_shares": 0, "sell_shares": 3000},
    ]
    out = stock_api.derive_brokers(rows, 5)
    assert out["top_buy"][0]["broker"] == "甲券" and out["top_buy"][0]["net_shares"] == 5000
    assert out["top_sell"][0]["broker"] == "乙券" and out["top_sell"][0]["net_shares"] == -3000
    assert out["summary"][0]["buy_brokers"] == 1 and out["summary"][0]["sell_brokers"] == 1


def test_bad_sid_raises() -> None:
    with pytest.raises(DataError, match="非法股票代號"):
        stock_api.ohlc(None, ServingConfig(pg_schema="serving"), "bad!")


def test_factor_history_no_factors_empty() -> None:
    out = stock_api.factor_history(None, ServingConfig(pg_schema="serving"), "2330", [])
    assert out["source"] == "pg" and out["rows"] == []


# ── 需真 PG 的整合測試 ──────────────────────────────────────────


def _prices(days: int, stocks: dict[str, float]) -> pl.DataFrame:
    from twquant.conventions import TZ

    rows = []
    for i in range(days):
        d = dt.date(2026, 6, 1) + dt.timedelta(days=i)
        at = dt.datetime(d.year, d.month, d.day, 18, 0, tzinfo=TZ)
        for sid, p0 in stocks.items():
            c = float(p0 + i)
            rows.append(
                {"date": d, "stock_id": sid, "open": c, "high": c, "low": c, "close": c,
                 "volume": 1000, "amount": c * 1000.0, "limit_up_price": None,
                 "limit_down_price": None, "available_at": at}
            )
    return pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(_TZ),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )


def _pg_cfg_or_skip() -> ServingConfig:
    pytest.importorskip("psycopg")
    if not os.environ.get("TWQUANT_PG_DSN"):
        pytest.skip("TWQUANT_PG_DSN 未設，跳過 PG 整合測試")
    from twquant.data.serving import store

    cfg = ServingConfig(enabled=True, pg_schema="serving_test_api")
    if not store.ping(cfg):
        pytest.skip("PG 不可達")
    return cfg


def _drop(cfg: ServingConfig) -> None:
    from twquant.data.serving import store

    with store.connect(cfg) as conn, conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{cfg.pg_schema}" CASCADE')
        conn.commit()


def test_scorecard_pg_shape(tmp_path: Path) -> None:
    from twquant.data.serving import materialize as smat

    cfg = _pg_cfg_or_skip()
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", _prices(25, {"1111": 100.0, "2222": 50.0, "3333": 200.0}))
    try:
        _drop(cfg)
        smat.materialize_day(wh, cfg, dt.date(2026, 6, 25))
        sc = stock_api.scorecard(None, cfg, "1111")
        assert set(sc.keys()) == {"sid", "name", "industry", "market", "as_of", "factors", "composite"}
        assert sc["factors"] and sc["composite"] is not None
        assert set(sc["factors"][0].keys()) == {
            "factor", "zh", "category", "value", "z", "pct", "direction", "signed_z"
        }
    finally:
        _drop(cfg)


def test_source_pg_and_empty_when_missing(tmp_path: Path) -> None:
    from twquant.data.serving import sync as ssync

    cfg = _pg_cfg_or_skip()
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", _prices(3, {"1111": 100.0}))
    try:
        _drop(cfg)
        ssync.sync_all(wh, cfg)  # 鏡射 prices 進 PG
        out = stock_api.ohlc(None, cfg, "1111")
        assert out["source"] == "pg" and len(out["rows"]) == 3
        # PG 沒有的股票 → 回空（不降級資料湖）
        empty = stock_api.ohlc(None, cfg, "9999")
        assert empty["source"] == "pg" and empty["rows"] == []
    finally:
        _drop(cfg)
