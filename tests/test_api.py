"""SPEC-007：監控 API（monitoring.api）端點測試。

直接呼叫路由處理函數（傳入 fixture 資料湖），驗證回傳結構、安全性（SQL 注入白名單）
與參數驗證的 HTTPException。實際 HTTP 傳輸/序列化由 `twquant serve` 對真實資料湖手動
驗證覆蓋。重運算端點（/api/snapshot、成功回測）需完整配置與歷史，屬整合層，不在此覆蓋。
"""

from __future__ import annotations

from typing import cast

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


def test_strategies_listing() -> None:
    # T-049：configs/strategies/*.yaml 全部列出（含 Sleeve C 原型）
    ids = {s["id"] for s in api.get_strategies()}
    assert "reversal_revenue" in ids
    assert "sample_revenue_trust" in ids


def test_backtest_unknown_strategy_404(snapshot_wh: Warehouse) -> None:
    with pytest.raises(HTTPException) as ei:
        api.get_backtest(snapshot_wh, start="2025-08-01", end="2025-08-31", strategy="no_such")
    assert ei.value.status_code == 404


def test_backtest_strategy_path_escape_404(snapshot_wh: Warehouse) -> None:
    with pytest.raises(HTTPException) as ei:
        api.get_backtest(snapshot_wh, start="2025-08-01", end="2025-08-31", strategy="../serving")
    assert ei.value.status_code == 404


def test_backtest_bad_date_format(snapshot_wh: Warehouse) -> None:
    with pytest.raises(HTTPException) as ei:
        api.get_backtest(snapshot_wh, start="nope", end="2025-12-31")
    assert ei.value.status_code == 400


def test_backtest_start_not_before_end(snapshot_wh: Warehouse) -> None:
    with pytest.raises(HTTPException) as ei:
        api.get_backtest(snapshot_wh, start="2025-12-31", end="2025-08-01")
    assert ei.value.status_code == 400


def test_ac_d116_exit_check_validates_ids_and_passes_them_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """依本 repo 慣例直接呼叫端點函數，不走 TestClient。"""
    from twquant.monitoring import api, exit_check

    seen: list[list[str]] = []

    def fake(_cfg: object, _check: object, _dir: object, ids: list[str]) -> dict[str, object]:
        seen.append(list(ids))
        return {"mode": "research", "stocks": [{"stock_id": i} for i in ids]}

    monkeypatch.setattr(exit_check, "exit_check", fake)
    out = api.get_platform_exit_check("0050, 2330")
    stocks = cast("list[dict[str, object]]", out["stocks"])
    assert [s["stock_id"] for s in stocks] == ["0050", "2330"]
    assert seen == [["0050", "2330"]]  # 前導零保留、空白去除
    for bad in ("23;30", ",", ",".join(str(1000 + i) for i in range(61))):
        with pytest.raises(HTTPException) as err:
            api.get_platform_exit_check(bad)
        assert err.value.status_code == 422
