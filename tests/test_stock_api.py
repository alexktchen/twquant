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


def test_broker_flow_bad_sid_raises() -> None:
    with pytest.raises(DataError, match="非法股票代號"):
        stock_api.broker_flow(None, ServingConfig(pg_schema="serving"), "bad!")


def test_factor_history_no_factors_empty() -> None:
    out = stock_api.factor_history(None, ServingConfig(pg_schema="serving"), "2330", [])
    assert out["source"] == "pg" and out["rows"] == []


def test_factor_top_unknown_factor_raises() -> None:
    with pytest.raises(DataError, match="未註冊"):
        stock_api.factor_top(None, ServingConfig(pg_schema="serving"), "price.not_exist")


def test_factor_top_bad_order_raises() -> None:
    with pytest.raises(DataError, match="order"):
        stock_api.factor_top(
            None, ServingConfig(pg_schema="serving"), "price.reversal_5d", order="sideways"
        )


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
    cfg = _pg_cfg_or_skip()
    from twquant.data.serving import materialize as smat

    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", _prices(25, {"1111": 100.0, "2222": 50.0, "3333": 200.0}))
    try:
        _drop(cfg)
        smat.materialize_day(wh, cfg, dt.date(2026, 6, 25))
        sc = stock_api.scorecard(None, cfg, "1111")
        assert set(sc.keys()) == {
            "sid",
            "name",
            "industry",
            "market",
            "as_of",
            "factors",
            "composite",
        }
        assert sc["factors"] and sc["composite"] is not None
        assert set(sc["factors"][0].keys()) == {
            "factor",
            "zh",
            "category",
            "value",
            "z",
            "pct",
            "direction",
            "signed_z",
        }
    finally:
        _drop(cfg)


def test_source_pg_and_empty_when_missing(tmp_path: Path) -> None:
    cfg = _pg_cfg_or_skip()
    from twquant.data.serving import sync as ssync

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


def test_composite_top_pg_rank_order(tmp_path: Path) -> None:
    cfg = _pg_cfg_or_skip()
    from twquant.data.serving import materialize as smat

    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", _prices(25, {"1111": 100.0, "2222": 50.0, "3333": 200.0}))
    try:
        _drop(cfg)
        smat.materialize_day(wh, cfg, dt.date(2026, 6, 25))
        out = stock_api.composite_top(None, cfg, 10)
        assert out["source"] == "pg" and out["as_of"] == dt.date(2026, 6, 25)
        rows = out["rows"]
        assert len(rows) == 3  # 宇宙 3 檔全回
        pcts = [r["pct"] for r in rows]
        assert pcts == sorted(pcts, reverse=True)  # pct 遞減
        assert {r["stock_id"] for r in rows} == {"1111", "2222", "3333"}
        # n 截斷
        assert len(stock_api.composite_top(None, cfg, 2)["rows"]) == 2
    finally:
        _drop(cfg)


def test_factor_top_pg_signed_order(tmp_path: Path) -> None:
    cfg = _pg_cfg_or_skip()
    from twquant.data.serving import materialize as smat

    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", _prices(25, {"1111": 100.0, "2222": 50.0, "3333": 200.0}))
    try:
        _drop(cfg)
        smat.materialize_day(wh, cfg, dt.date(2026, 6, 25))
        out = stock_api.factor_top(None, cfg, "price.reversal_5d", 10)
        assert out["source"] == "pg" and out["as_of"] == dt.date(2026, 6, 25)
        assert out["factor"] == "price.reversal_5d" and out["direction"] == -1
        assert out["order"] == "top"
        rows = out["rows"]
        assert len(rows) == 3
        szs = [r["signed_z"] for r in rows]
        assert szs == sorted(szs, reverse=True)  # 方向化 z 遞減（訊號最強在前）
        # 等差 +1/日 → 基期最高的 3333 近 5 日報酬最小 → 反轉（direction=-1）訊號最強
        assert rows[0]["stock_id"] == "3333"
        assert set(rows[0].keys()) == {"stock_id", "name", "value", "z", "pct", "signed_z"}
        # available 供前端下拉：至少含已註冊的價量因子
        avail = {a["factor"] for a in out["available"]}
        assert "price.reversal_5d" in avail and "price.mom_20d" in avail
        # n 截斷
        assert len(stock_api.factor_top(None, cfg, "price.reversal_5d", 2)["rows"]) == 2
        # order=bottom：訊號最弱在前（方向化 z 遞增），首檔為 top 的末檔
        bot = stock_api.factor_top(None, cfg, "price.reversal_5d", 10, order="bottom")
        assert bot["order"] == "bottom"
        bszs = [r["signed_z"] for r in bot["rows"]]
        assert bszs == sorted(bszs)  # 遞增
        assert bot["rows"][0]["stock_id"] == rows[-1]["stock_id"]
    finally:
        _drop(cfg)


def test_stock_meta_all_pg(tmp_path: Path) -> None:
    cfg = _pg_cfg_or_skip()
    from twquant.data.serving import sync as ssync

    wh = Warehouse(tmp_path)
    wh.write(
        "clean",
        "stock_meta",
        pl.DataFrame(
            {
                "stock_id": ["2330", "0050"],
                "name": ["台積電", "元大台灣50"],
                "market": ["TWSE", "TWSE"],
                "industry": ["半導體業", "ETF"],
                "list_date": [None, None],
                "delist_date": [None, None],
            }
        ).with_columns(pl.col("list_date").cast(pl.Date), pl.col("delist_date").cast(pl.Date)),
    )
    try:
        _drop(cfg)
        ssync.sync_all(wh, cfg)
        out = stock_api.stock_meta_all(None, cfg)
        meta = out["meta"]
        assert meta["2330"]["name"] == "台積電" and meta["2330"]["industry"] == "半導體業"
        assert meta["0050"]["name"] == "元大台灣50"  # 保前導零
    finally:
        _drop(cfg)


def _broker_trades(rows_by_day: dict[dt.date, list[tuple[str, int, int]]]) -> pl.DataFrame:
    from twquant.conventions import TZ

    rows = []
    for d, entries in rows_by_day.items():
        at = dt.datetime(d.year, d.month, d.day, 20, 0, tzinfo=TZ)  # 券商次日晚到
        for bid, buy, sell in entries:
            rows.append(
                {
                    "date": d,
                    "stock_id": "1111",
                    "broker_id": bid,
                    "buy_shares": buy,
                    "sell_shares": sell,
                    "avg_price": None,
                    "available_at": at,
                }
            )
    return pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(_TZ),
        pl.col("avg_price").cast(pl.Float64),
        pl.col("buy_shares").cast(pl.Int64),
        pl.col("sell_shares").cast(pl.Int64),
    )


def test_broker_flow_pg_daily_net(tmp_path: Path) -> None:
    cfg = _pg_cfg_or_skip()
    from twquant.data.serving import sync as ssync

    d1, d2 = dt.date(2026, 6, 1), dt.date(2026, 6, 2)
    wh = Warehouse(tmp_path)
    wh.write(
        "clean",
        "broker_trades",
        _broker_trades(
            {
                d1: [("甲券", 5000, 0), ("乙券", 0, 2000)],  # 淨 = +3000
                d2: [("甲券", 1000, 4000), ("乙券", 500, 0)],  # 淨 = -2500
            }
        ),
    )
    try:
        _drop(cfg)
        ssync.sync_all(wh, cfg)
        out = stock_api.broker_flow(None, cfg, "1111")
        assert out["source"] == "pg"
        # date 遞增、每日淨買超為當日各券商 (buy − sell) 加總
        assert [r["net"] for r in out["rows"]] == [3000, -2500]
        assert [str(r["date"]) for r in out["rows"]] == ["2026-06-01", "2026-06-02"]
        assert out["as_of"] == d2
        # 未知股 → 空列（不降級資料湖）
        assert stock_api.broker_flow(None, cfg, "9999")["rows"] == []
    finally:
        _drop(cfg)
