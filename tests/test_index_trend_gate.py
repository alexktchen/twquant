"""D-092 P4：`index_trend_gate` overlay（Faber 2007 的 N 日均線擇時）。

契約（由本測試凍結）：
- AC-1 大盤 **t−1** 收盤 > N 日 SMA → 倍數 1.0；否則 → `floor`（預設 0＝空手）。
- AC-2 **只用 signal_date 之前的指數**：signal_date 當日的收盤即使在表裡也不得影響結果。
  `index_daily` 的 available_at 是當日 18:00，PitView 以 as_of 截點過濾時會擋住當日列；
  但 overlay 不得依賴這件事——若表沒有 available_at（或截點設在收盤後），自己也要擋。
- AC-3 歷史不足 N 日 → raise DataError，不得靜默放行。
- AC-4 參數驗證：window < 2、floor 不在 [0,1] → ConfigError。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.errors import ConfigError, DataError
from twquant.strategy.overlays import get_overlay, index_trend_gate


def _wh_with_index(tmp_path: Path, closes: list[float], start: dt.date) -> Warehouse:
    """把一串 TAIEX 收盤寫進資料湖；available_at 一律當日 18:00（與正式資料相同）。"""
    rows = [
        {
            "date": start + dt.timedelta(days=i),
            "index_id": "TAIEX",
            "open": None,
            "high": None,
            "low": None,
            "close": c,
            "available_at": dt.datetime.combine(
                start + dt.timedelta(days=i), dt.time(18), tzinfo=TZ
            ),
        }
        for i, c in enumerate(closes)
    ]
    df = pl.DataFrame(rows).with_columns(
        pl.col("open").cast(pl.Float64),
        pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64),
        pl.col("available_at").cast(schemas.TW_DT),
    )
    wh = Warehouse(tmp_path)
    wh.write("clean", "index_daily", df)
    return wh


_W = pl.DataFrame({"date": [dt.date(2026, 1, 1)], "stock_id": ["2330"], "target_weight": [0.5]})


def test_ac1_full_exposure_above_sma_and_floor_below(tmp_path: Path) -> None:
    start = dt.date(2026, 1, 1)
    # 前 10 天 100，第 11~15 天一路漲到 150：t−1 收盤 150 > 15 日 SMA → 全額
    up = [100.0] * 10 + [110.0, 120.0, 130.0, 140.0, 150.0]
    wh = _wh_with_index(tmp_path / "up", up, start)
    d = start + dt.timedelta(days=15)
    out = index_trend_gate(d, _W, wh.view(d), {"window": 15})
    assert out["target_weight"][0] == pytest.approx(0.5)

    # 一路跌：t−1 收盤 50 < SMA → floor（預設 0）
    down = [100.0] * 10 + [90.0, 80.0, 70.0, 60.0, 50.0]
    wh2 = _wh_with_index(tmp_path / "down", down, start)
    out2 = index_trend_gate(d, _W, wh2.view(d), {"window": 15})
    assert out2["target_weight"][0] == pytest.approx(0.0)
    out3 = index_trend_gate(d, _W, wh2.view(d), {"window": 15, "floor": 0.3})
    assert out3["target_weight"][0] == pytest.approx(0.15)  # 0.5 × 0.3


def test_ac2_signal_day_close_is_never_used(tmp_path: Path) -> None:
    """構造一個只有把 signal_date 當日收盤算進去才會翻多的序列。

    截點刻意設在當日 **收盤後**（23:59），讓 PitView 放行當日列——這樣若 overlay
    偷看了當日收盤，測試就會抓到；正式回測的截點不會這樣，但契約不能靠那個巧合。
    """
    start = dt.date(2026, 1, 1)
    # 15 天平盤 100，t−1 = 100（不 > SMA 100）→ 應為 floor；
    # 第 16 天（signal_date 當日）暴漲到 1000——只有偷看它才會變成全額。
    closes = [100.0] * 15 + [1000.0]
    wh = _wh_with_index(tmp_path, closes, start)
    d = start + dt.timedelta(days=15)
    view = wh.view(dt.datetime.combine(d, dt.time(23, 59), tzinfo=TZ))
    assert view.table("index_daily").filter(pl.col("date") == d).collect().height == 1  # 當日列可見
    out = index_trend_gate(d, _W, view, {"window": 15})
    assert out["target_weight"][0] == pytest.approx(0.0)


def test_ac3_insufficient_history_raises(tmp_path: Path) -> None:
    start = dt.date(2026, 1, 1)
    wh = _wh_with_index(tmp_path, [100.0] * 10, start)
    d = start + dt.timedelta(days=10)
    with pytest.raises(DataError, match="不足 200 日均線"):
        index_trend_gate(d, _W, wh.view(d), {})  # 預設 window=200


def test_ac4_params_are_validated(tmp_path: Path) -> None:
    start = dt.date(2026, 1, 1)
    wh = _wh_with_index(tmp_path, [100.0] * 5, start)
    d = start + dt.timedelta(days=5)
    with pytest.raises(ConfigError, match="window"):
        index_trend_gate(d, _W, wh.view(d), {"window": 1})
    with pytest.raises(ConfigError, match="floor"):
        index_trend_gate(d, _W, wh.view(d), {"window": 3, "floor": 1.5})


def test_registered_under_its_config_name() -> None:
    assert get_overlay("index_trend_gate") is index_trend_gate
