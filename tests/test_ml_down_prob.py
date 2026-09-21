"""T-267／D-131：個股下跌機率的標籤——未來 N 日內最大跌幅是否達門檻（絕對、二元）。

與既有 `forward_drawdown` 的差別：那個回傳橫斷面百分位（相對），
這個回傳 0/1（絕對）——「這檔會不會跌 10%」才答得出來。
"""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from twquant.ml.dataset import down_probability_labels

D0 = dt.date(2024, 1, 1)


def _px(closes: list[float], opens: list[float] | None = None, sid: str = "2330") -> pl.DataFrame:
    closes = [float(c) for c in closes]
    opens = [float(o) for o in opens] if opens else None
    n = len(closes)
    return pl.DataFrame(
        {
            "date": [D0 + dt.timedelta(days=i) for i in range(n)],
            "stock_id": [sid] * n,
            "open": opens or closes,
            "close": closes,
        }
    )


def _lab(df: pl.DataFrame, day: int, horizon: int = 5, drop: float = 0.10) -> float | None:
    out = down_probability_labels(df, horizon=horizon, drop=drop)
    row = out.filter(pl.col("date") == D0 + dt.timedelta(days=day))
    return None if row.is_empty() else row["label"][0]


def test_ac1_touching_the_drop_is_one() -> None:
    assert _lab(_px([100, 100, 95, 89, 95, 95, 95, 95]), 0) == 1.0


def test_ac2_not_touching_is_zero() -> None:
    assert _lab(_px([100, 100, 95, 92, 95, 98, 98, 98]), 0) == 0.0


def test_ac3_measured_from_next_open_not_signal_close() -> None:
    """訊號日收盤 100，隔日開盤跳空到 80：跌幅從 80 起算，72 才算跌 10%。"""
    closes = [100.0, 80.0, 75.0, 75.0, 75.0, 75.0, 75.0, 75.0]
    assert _lab(_px(closes, closes), 0) == 0.0  # 75/80−1 = −6.3%，沒到 −10%
    closes2 = [100.0, 80.0, 71.0, 71.0, 71.0, 71.0, 71.0, 71.0]
    assert _lab(_px(closes2, closes2), 0) == 1.0  # 71/80−1 = −11.3%


def test_ac4_a_rise_first_does_not_cancel_a_later_drop() -> None:
    """與買進標籤不同：這裡只問「有沒有跌到」，先漲不影響。"""
    assert _lab(_px([100, 100, 130, 130, 89, 89, 89, 89]), 0) == 1.0


def test_ac5_incomplete_horizon_is_null_not_zero() -> None:
    out = down_probability_labels(_px([100.0] * 6), horizon=5, drop=0.10)
    assert out.filter(pl.col("date") == D0)["label"][0] == 0.0
    assert out.filter(pl.col("date") == D0 + dt.timedelta(days=1))["label"][0] is None


def test_ac6_no_lookahead() -> None:
    a = _px([100, 100, 95, 89, 95, 95, 95, 95, 95, 95])
    b = _px([40, 100, 95, 89, 95, 95, 95, 95, 95, 95])  # 只改訊號日以前
    la = down_probability_labels(a, horizon=5, drop=0.10)
    lb = down_probability_labels(b, horizon=5, drop=0.10)
    late = pl.col("date") >= D0 + dt.timedelta(days=1)
    assert la.filter(late).equals(lb.filter(late))


def test_ac7_stock_id_keeps_leading_zero_and_label_is_binary() -> None:
    out = down_probability_labels(
        pl.concat([_px([100.0] * 8, sid="0050"), _px([100, 100, 80, 80, 80, 80, 80, 80])]),
        horizon=5,
        drop=0.10,
    )
    assert "0050" in out["stock_id"].to_list()
    assert set(out["label"].drop_nulls().to_list()) <= {0.0, 1.0}


def test_ac8_bad_parameters_raise() -> None:
    with pytest.raises(ValueError):
        down_probability_labels(_px([100.0] * 8), horizon=0, drop=0.1)
    with pytest.raises(ValueError):
        down_probability_labels(_px([100.0] * 8), horizon=5, drop=0.0)
