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


# ── T-260／D-127：波動度標準化的邊界 ──────────────────────────────────────────


def _vol_px(n: int, daily: float, sid: str = "2330") -> pl.DataFrame:
    """前半段以 ±daily 鋸齒走（製造已知的日波動），最後一天起直線上漲。"""
    closes = [100.0 * (1 + daily * (1 if i % 2 else -1)) for i in range(n)]
    return _px(closes, sid=sid)


def test_ac12_barrier_scales_with_each_stocks_own_volatility() -> None:
    """同樣漲 3%：低波動股碰到上緣（1σ），高波動股沒碰到。"""
    quiet = _vol_px(30, 0.002, "1101")  # 日波動 0.2% → 20 日 σ ≈ 0.9%
    wild = _vol_px(30, 0.03, "2330")  # 日波動 3% → 20 日 σ ≈ 13%
    both = pl.concat([quiet, wild])
    # 第 25 天起兩檔都漲 3%，持續到期末
    both = both.with_columns(
        pl.when(pl.col("date") >= D0 + dt.timedelta(days=25))
        .then(pl.col("close") * 1.03)
        .otherwise(pl.col("close"))
        .alias("close")
    ).with_columns(pl.col("close").alias("open"))
    out = triple_barrier_labels(both, horizon=4, up=1.0, down=1.0, vol_window=20, vol_scaled=True)
    # 訊號日 index 23 → t+1（index 24）開盤進場，仍是跳空前的價格；跳空發生在 index 25 起
    day = D0 + dt.timedelta(days=23)
    got = {r["stock_id"]: r["label"] for r in out.filter(pl.col("date") == day).to_dicts()}
    assert got["1101"] == 1.0  # 低波動：3% 遠超過它的 1σ
    assert got["2330"] == 0.0  # 高波動：3% 還不到它的 1σ


def test_ac13_vol_scaled_needs_enough_history_and_nonzero_vol() -> None:
    out = triple_barrier_labels(
        _vol_px(30, 0.01), horizon=4, up=1.0, down=1.0, vol_window=20, vol_scaled=True
    )
    assert out.filter(pl.col("date") < D0 + dt.timedelta(days=20))["label"].null_count() > 0
    flat = triple_barrier_labels(  # 價格完全不動 → σ=0 → 無標籤，不得當成 0
        _px([100.0] * 30), horizon=4, up=1.0, down=1.0, vol_window=20, vol_scaled=True
    )
    assert flat["label"].null_count() == flat.height


def test_ac14_vol_scaled_has_no_lookahead() -> None:
    px = _vol_px(40, 0.01)
    a = triple_barrier_labels(px, horizon=4, up=1.0, down=1.0, vol_window=20, vol_scaled=True)
    cut = D0 + dt.timedelta(days=30)
    poked = px.with_columns(
        pl.when(pl.col("date") > cut)
        .then(pl.col("close") * 5)
        .otherwise(pl.col("close"))
        .alias("close")
    )
    b = triple_barrier_labels(poked, horizon=4, up=1.0, down=1.0, vol_window=20, vol_scaled=True)
    early = pl.col("date") <= cut - dt.timedelta(days=5)
    assert a.filter(early).equals(b.filter(early))


def test_ac15_vol_window_required_when_scaled() -> None:
    with pytest.raises(ValueError, match="vol_window"):
        triple_barrier_labels(_px([100.0] * 10), horizon=4, up=1.0, down=1.0, vol_scaled=True)


def test_ac16_vol_scaled_config_round_trips_and_is_validated() -> None:
    """D-127：`vol_scaled` 必須是配置欄位，且與 transform 相符（寫了卻不生效是 no-op 缺陷）。"""
    from pydantic import ValidationError

    from twquant.config import MlLabelConfig

    ok = MlLabelConfig.model_validate(
        {
            "transform": "triple_barrier",
            "barrier_up": 1.0,
            "barrier_down": 1.0,
            "vol_scaled": True,
            "vol_window": 20,
            "total_return": True,
        }
    )
    assert ok.vol_scaled and ok.vol_window == 20
    with pytest.raises(ValidationError, match="vol_window"):  # 開了卻沒給視窗
        MlLabelConfig.model_validate(
            {
                "transform": "triple_barrier",
                "barrier_up": 1.0,
                "barrier_down": 1.0,
                "vol_scaled": True,
                "total_return": True,
            }
        )
    with pytest.raises(ValidationError, match="triple_barrier"):  # 用在別的標籤上
        MlLabelConfig.model_validate(
            {
                "transform": "forward_drawdown",
                "vol_scaled": True,
                "vol_window": 20,
                "total_return": True,
            }
        )


def test_ac17_shipped_third_round_config_loads() -> None:
    from pathlib import Path

    from twquant.config import MlConfig, load_config

    cfg = load_config(Path(__file__).resolve().parents[1] / "configs" / "ml_sb3_vol.yaml", MlConfig)
    assert cfg.label.vol_scaled and cfg.label.vol_window == 20
    assert cfg.label.barrier_up == 1.0 and cfg.label.barrier_down == 1.0
    assert len(cfg.features) == 19  # 12 個股價量／基本面 ＋ 7 籌碼
    assert "chip.foreign_streak" in cfg.features


def test_ac18_label_zero_lumps_down_first_with_no_touch() -> None:
    """標籤是二元的：0 同時代表「先觸下緣」與「兩邊都沒碰」。

    D-129 的診斷 B 登記的統計量是「上緣比例 − **下緣**比例」，
    用 `1 - label.mean()` 當下緣比例會把「沒碰到」算進去——
    2026-09-21 實測：前 15 名真實是 37.8% 上／27.8% 下／34.4% 沒碰，
    錯誤算法得到 38.8% vs 61.2%（−21.5pp，判未通過），正確算法是 +10.0pp（t=+3.78，通過）。
    """
    flat = _px([100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0])
    out = triple_barrier_labels(flat, horizon=5, up=0.10, down=0.10)
    assert out.filter(pl.col("date") == D0)["label"][0] == 0.0  # 沒碰到任何邊界也是 0
    down = _px([100.0, 100.0, 85.0, 85.0, 85.0, 85.0, 85.0, 85.0])
    assert triple_barrier_labels(down, horizon=5, up=0.10, down=0.10).filter(
        pl.col("date") == D0
    )["label"][0] == 0.0  # 先跌也是 0——兩者在標籤裡無法區分
