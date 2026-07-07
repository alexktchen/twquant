"""SPEC-007：監控快照建構函數（monitoring.snapshot）的純查詢測試。

因子 IC / 回測 / 容量需完整策略配置與較長歷史，屬整合層（由 smoke test 與對真實
資料湖的手動驗證覆蓋）；此處聚焦可用小 fixture 精確斷言的顯示查詢與安全性。
"""

from __future__ import annotations

import pytest

from twquant.data.warehouse import Warehouse
from twquant.errors import DataError
from twquant.monitoring import snapshot as snap


def test_market_index_shape(snapshot_wh: Warehouse) -> None:
    mkt = snap.market_index(snapshot_wh)
    assert len(mkt) == 2  # 兩個交易日
    assert {"d", "o", "h", "l", "c", "amt"} <= set(mkt[0])


def test_liquid_stocks_ordered_by_turnover(snapshot_wh: Warehouse) -> None:
    liq = snap.liquid_stocks(snapshot_wh)
    assert set(liq) == {"2330", "2317"}
    assert liq[0] == "2330"  # 平均成交金額較高者在前


def test_stock_ohlc_valid(snapshot_wh: Warehouse) -> None:
    k = snap.stock_ohlc(snapshot_wh, "2330")
    assert [row["d"] for row in k] == ["2026-06-29", "2026-06-30"]
    assert k[-1]["c"] == 1010.0


def test_stock_ohlc_rejects_sql_injection(snapshot_wh: Warehouse) -> None:
    # 白名單驗證：帶分號/空白的注入字串必須被擋（黃金規則 5：raise 而非靜默）。
    with pytest.raises(DataError):
        snap.stock_ohlc(snapshot_wh, "2330'; DROP TABLE prices_daily;--")


def test_institutional_top_trust_first(snapshot_wh: Warehouse) -> None:
    rows = snap.institutional(snapshot_wh)
    assert rows[0]["sid"] == "2330"  # trust_net 50000 > 30000，投信買超前排
    assert "px" in rows[0]


def test_revenue_month_and_order(snapshot_wh: Warehouse) -> None:
    ym, rows = snap.revenue(snapshot_wh)
    assert ym == "2026-05"
    assert [r["sid"] for r in rows] == ["2330", "2317"]  # 皆 >5 億、依 YoY 由高到低


def test_coverage_uses_source_partitions(snapshot_wh: Warehouse) -> None:
    cov = {c["label"]: c for c in snap.coverage(snapshot_wh)}
    assert cov["上市"]["days"] == 2  # 由 part-twse.parquet 的來源日期算
    assert cov["上櫃"]["days"] == 0  # 無 tpex 來源


def test_factor_dropped_when_ten_day_t_nonfinite() -> None:
    # 回歸：NaN/Inf 的 10 日 t（如 52 週高在連續期樣本不足）必須被判無效並剔除——
    # 篩選在 _sanitize（NaN→None）之前，故須用 isfinite 而非 `is not None`。
    def mk(t: float) -> dict[str, object]:
        return {"name": "price.x", "horizons": [{"h": 10, "t": t}]}

    assert snap._has_valid_ten_day_t(mk(1.5)) is True
    assert snap._has_valid_ten_day_t(mk(float("nan"))) is False
    assert snap._has_valid_ten_day_t(mk(float("inf"))) is False
    # 無 10 日視窗 → 無效
    assert snap._has_valid_ten_day_t({"name": "price.x", "horizons": [{"h": 5, "t": 1.0}]}) is False


def test_ingest_status_no_gaps_no_failures(snapshot_wh: Warehouse) -> None:
    st = snap.ingest_status(snapshot_wh)
    assert st["failed"] == 0
    assert st["recent_failures"] == []
    src = st["sources"]
    assert isinstance(src, list)
    by_label = {s["label"]: s for s in src}
    assert by_label["上市行情"]["days"] == 2
    assert by_label["上市行情"]["gaps"] == 0  # 交易日曆由 prices 推導，區間內無缺口
