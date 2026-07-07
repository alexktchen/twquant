"""SPEC-007：監控 API（monitoring.api）端點測試。

直接呼叫路由處理函數（傳入 fixture 資料湖），驗證回傳結構、安全性（SQL 注入白名單）
與參數驗證的 HTTPException。實際 HTTP 傳輸/序列化由 `twquant serve` 對真實資料湖手動
驗證覆蓋。重運算端點（/api/snapshot、成功回測）需完整配置與歷史，屬整合層，不在此覆蓋。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from twquant.data.warehouse import Warehouse
from twquant.monitoring import api


def test_health(snapshot_wh: Warehouse) -> None:
    body = api.health(snapshot_wh)
    assert body["status"] == "ok"
    assert str(body["as_of"]) == "2026-06-30"


def test_stocks_list(snapshot_wh: Warehouse) -> None:
    stocks = api.get_stocks(snapshot_wh)["stocks"]
    assert isinstance(stocks, list)
    assert set(stocks) == {"2330", "2317"}


def test_stock_ohlc(snapshot_wh: Warehouse) -> None:
    ohlc = api.get_stock("2330", snapshot_wh)["ohlc"]
    assert isinstance(ohlc, list)
    assert len(ohlc) == 2


def test_stock_ohlc_rejects_injection(snapshot_wh: Warehouse) -> None:
    with pytest.raises(HTTPException) as ei:
        api.get_stock("2330'; DROP TABLE prices_daily;--", snapshot_wh)
    assert ei.value.status_code == 400


def test_stock_ohlc_unknown_returns_404(snapshot_wh: Warehouse) -> None:
    with pytest.raises(HTTPException) as ei:
        api.get_stock("9999", snapshot_wh)  # 格式合法但無資料
    assert ei.value.status_code == 404


def test_ingest_status(snapshot_wh: Warehouse) -> None:
    assert api.get_ingest_status(snapshot_wh)["failed"] == 0


def test_backtest_bad_date_format(snapshot_wh: Warehouse) -> None:
    with pytest.raises(HTTPException) as ei:
        api.get_backtest(snapshot_wh, start="nope", end="2025-12-31")
    assert ei.value.status_code == 400


def test_backtest_start_not_before_end(snapshot_wh: Warehouse) -> None:
    with pytest.raises(HTTPException) as ei:
        api.get_backtest(snapshot_wh, start="2025-12-31", end="2025-08-01")
    assert ei.value.status_code == 400
