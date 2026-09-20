"""T-248／D-120 §三：買進模型的標籤（先 +10% 於 −10% 之前）與大盤狀態特徵。"""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from twquant.ml.context import CONTEXT_FEATURES, market_state_features
from twquant.ml.dataset import triple_barrier_labels

D0 = dt.date(2024, 1, 1)


def _px(closes: list[float], opens: list[float] | None = None, sid: str = "2330") -> pl.DataFrame:
    n = len(closes)
    return pl.DataFrame(
        {
            "date": [D0 + dt.timedelta(days=i) for i in range(n)],
            "stock_id": [sid] * n,
            "open": opens or closes,
            "close": closes,
        }
    )


def _label(df: pl.DataFrame, day: int, **kw: float | int) -> float | None:
    out = triple_barrier_labels(df, horizon=kw.pop("horizon", 5), up=0.10, down=0.10, **kw)  # type: ignore[arg-type]
    row = out.filter(pl.col("date") == D0 + dt.timedelta(days=day))
    return None if row.is_empty() else row["label"][0]


def test_ac1_up_first_is_one() -> None:
    # t=0 訊號、t=1 開盤 100 進場；t=3 收盤 111 → 先碰上緣
    assert _label(_px([100, 100, 105, 111, 90, 90, 90, 90]), 0) == 1.0


def test_ac2_down_first_is_zero_even_if_it_recovers_later() -> None:
    assert _label(_px([100, 100, 89, 120, 120, 120, 120, 120]), 0) == 0.0


def test_ac3_neither_barrier_within_horizon_is_zero() -> None:
    assert _label(_px([100, 100, 104, 96, 105, 95, 100, 130]), 0) == 0.0  # 第 7 天才漲，已過期限


def test_ac4_entry_is_next_open_not_signal_close() -> None:
    """訊號日收盤 100，但隔日開盤跳空到 110：+10% 要從 110 起算（121），118 不算碰到。"""
    closes = [100.0, 110.0, 118.0, 118.0, 118.0, 118.0, 118.0, 118.0]
    opens = [100.0, 110.0, 118.0, 118.0, 118.0, 118.0, 118.0, 118.0]
    assert _label(_px(closes, opens), 0) == 0.0


def test_ac5_no_lookahead_label_at_t_ignores_data_up_to_t() -> None:
    """標籤只由 t+1 之後的價格決定：改掉 t 以前的收盤，標籤不變。"""
    a = _px([100, 100, 105, 111, 90, 90, 90, 90, 90, 90])
    b = _px([50, 100, 105, 111, 90, 90, 90, 90, 90, 90])
    la = triple_barrier_labels(a, horizon=5, up=0.10, down=0.10)
    lb = triple_barrier_labels(b, horizon=5, up=0.10, down=0.10)
    assert la.filter(pl.col("date") >= D0 + dt.timedelta(days=1)).equals(
        lb.filter(pl.col("date") >= D0 + dt.timedelta(days=1))
    )


def test_ac6_incomplete_horizon_is_null_not_zero() -> None:
    """期末不足 horizon 天的列沒有答案——null，不得當成「沒漲」的 0（黃金規則 5）。"""
    df = _px([100, 100, 101, 102, 103, 104])
    out = triple_barrier_labels(df, horizon=5, up=0.10, down=0.10)
    assert out.filter(pl.col("date") == D0)["label"][0] == 0.0  # 剛好 5 天，可判
    assert out.filter(pl.col("date") == D0 + dt.timedelta(days=1))["label"][0] is None


def test_ac7_label_is_binary_and_stock_id_stays_string() -> None:
    out = triple_barrier_labels(
        pl.concat([_px([100, 100, 111, 111, 111, 111, 111], sid="0050"), _px([100] * 7)]),
        horizon=5,
        up=0.10,
        down=0.10,
    )
    assert set(out["label"].drop_nulls().to_list()) <= {0.0, 1.0}
    assert "0050" in out["stock_id"].to_list()


def test_ac8_bad_parameters_raise() -> None:
    with pytest.raises(ValueError):
        triple_barrier_labels(_px([100] * 8), horizon=0, up=0.1, down=0.1)
    with pytest.raises(ValueError):
        triple_barrier_labels(_px([100] * 8), horizon=5, up=0.0, down=0.1)


def _market(n: int = 40) -> tuple[pl.DataFrame, pl.DataFrame]:
    days = [D0 + dt.timedelta(days=i) for i in range(n)]
    index = pl.DataFrame({"date": days, "close": [100.0 + i for i in range(n)]})
    rows = []
    for i, d in enumerate(days):
        rows.append({"date": d, "stock_id": "1101", "close": 100.0 + i})  # 永遠在均線上
        rows.append({"date": d, "stock_id": "2330", "close": 100.0 - i})  # 永遠在均線下
    return index, pl.DataFrame(rows)


def test_ac9_market_state_has_the_three_registered_features() -> None:
    index, prices = _market()
    out = market_state_features(index, prices, window=20)
    assert tuple(CONTEXT_FEATURES) == ("ctx.taiex_ma20_dev", "ctx.taiex_vol20", "ctx.breadth_ma20")
    last = out.sort("date").tail(1)
    assert last["ctx.breadth_ma20"][0] == pytest.approx(0.5)  # 兩檔一上一下
    ma = sum(100.0 + i for i in range(20, 40)) / 20
    assert last["ctx.taiex_ma20_dev"][0] == pytest.approx(139.0 / ma - 1.0)


def test_ac10_market_state_has_no_lookahead() -> None:
    index, prices = _market()
    full = market_state_features(index, prices, window=20)
    cut = D0 + dt.timedelta(days=30)
    part = market_state_features(
        index.filter(pl.col("date") <= cut), prices.filter(pl.col("date") <= cut), window=20
    )
    assert full.filter(pl.col("date") <= cut).equals(part)


def test_ac11_warmup_rows_are_null_not_zero() -> None:
    index, prices = _market()
    first = market_state_features(index, prices, window=20).sort("date").head(1)
    assert first["ctx.taiex_ma20_dev"][0] is None
