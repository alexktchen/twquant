"""D-092 P4／P6：兩條擇時規則的 overlay。

`index_trend_gate` ＝ Faber (2007) 的 N 日均線；
`absolute_momentum_gate` ＝ Antonacci (2014) 的 12 個月絕對動能。

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
from twquant.strategy.overlays import (
    absolute_momentum_gate,
    crash_risk_gate,
    get_overlay,
    index_trend_gate,
    inverse_variance_scaling,
    tsmom_vol_scaling,
    vol_target_scaling,
)


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
    with pytest.raises(DataError, match="不足 200 日"):
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


# ── Antonacci (2014) 絕對動能（D-092 P6）──────────────────────────────


def test_ac5_absolute_momentum_compares_endpoints_not_average(tmp_path: Path) -> None:
    """**這是與 Faber 的實質差別，不是參數差別。**

    構造一段「先跌後漲、但終點仍低於起點」的行情：
    端點報酬為負（Antonacci → 空手），而現價高於均線（Faber → 持有）。
    同一段資料兩條規則給出相反的訊號——這正是兩者要並排跑的理由。
    """
    start = dt.date(2026, 1, 1)
    closes = (
        [100.0] + [60.0] * 8 + [70.0, 80.0, 85.0, 88.0, 90.0, 95.0]
    )  # 15 天，終點 95 < 起點 100
    wh = _wh_with_index(tmp_path, closes, start)
    d = start + dt.timedelta(days=15)
    view = wh.view(d)

    # Antonacci：95 / 100 − 1 = −5% ≤ 0 → 空手
    out_abs = absolute_momentum_gate(d, _W, view, {"window": 15})
    assert out_abs["target_weight"][0] == pytest.approx(0.0)

    # Faber：均線 (100+60×8+70+80+85+88+90+95)/15 ≈ 77.9，而 95 > 77.9 → 持有
    out_sma = index_trend_gate(d, _W, view, {"window": 15})
    assert out_sma["target_weight"][0] == pytest.approx(0.5)


def test_ac5_absolute_momentum_positive_return_holds(tmp_path: Path) -> None:
    start = dt.date(2026, 1, 1)
    wh = _wh_with_index(tmp_path, [100.0 + i for i in range(15)], start)
    d = start + dt.timedelta(days=15)
    out = absolute_momentum_gate(d, _W, wh.view(d), {"window": 15})
    assert out["target_weight"][0] == pytest.approx(0.5)


def test_ac5_hurdle_raises_the_bar(tmp_path: Path) -> None:
    """hurdle 是「超額報酬」版：漲 14% 但門檻 20% → 仍空手。"""
    start = dt.date(2026, 1, 1)
    wh = _wh_with_index(tmp_path, [100.0 + i for i in range(15)], start)  # 100 → 114，+14%
    d = start + dt.timedelta(days=15)
    view = wh.view(d)
    assert absolute_momentum_gate(d, _W, view, {"window": 15, "hurdle": 0.20})["target_weight"][
        0
    ] == pytest.approx(0.0)
    assert absolute_momentum_gate(d, _W, view, {"window": 15, "hurdle": 0.10})["target_weight"][
        0
    ] == pytest.approx(0.5)


def test_ac5_absolute_momentum_never_uses_signal_day(tmp_path: Path) -> None:
    start = dt.date(2026, 1, 1)
    closes = [100.0] * 15 + [1000.0]  # 平盤（報酬 0，不 > 0 → 空手）；當日暴漲只有偷看才會翻多
    wh = _wh_with_index(tmp_path, closes, start)
    d = start + dt.timedelta(days=15)
    view = wh.view(dt.datetime.combine(d, dt.time(23, 59), tzinfo=TZ))
    assert absolute_momentum_gate(d, _W, view, {"window": 15})["target_weight"][0] == pytest.approx(
        0.0
    )


def test_ac5_absolute_momentum_insufficient_history_raises(tmp_path: Path) -> None:
    start = dt.date(2026, 1, 1)
    wh = _wh_with_index(tmp_path, [100.0] * 10, start)
    d = start + dt.timedelta(days=10)
    with pytest.raises(DataError, match="絕對動能回看"):
        absolute_momentum_gate(d, _W, wh.view(d), {})  # 預設 window=244


def test_absolute_momentum_registered() -> None:
    assert get_overlay("absolute_momentum_gate") is absolute_momentum_gate


# ── 另外三條文獻規則（D-092 P7）────────────────────────────────────────
#
# 這一組測試的重點**不是**「函數會跑」，而是**六條規則彼此不是參數變體**。
# 若它們在同一段資料上永遠給出相同的倍數，那這批實驗就只是同一個假設試六次，
# DSR 的試驗計數會嚴重低估。每個測試都構造一個「只有這條規則會這樣判」的情境。


def _wh_px(tmp_path: Path, closes: list[float], start: dt.date) -> Warehouse:
    """兩檔同步的股價（等權組合報酬 = 個股報酬），供波動類 overlay 使用。"""
    rows = []
    for i, c in enumerate(closes):
        d = start + dt.timedelta(days=i)
        for sid in ("1101", "2330"):
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": c,
                    "high": c,
                    "low": c,
                    "close": c,
                    "volume": 1000,
                    "amount": 1e8,
                    "limit_up_price": None,
                    "limit_down_price": None,
                    "available_at": dt.datetime.combine(d, dt.time(18), tzinfo=TZ),
                }
            )
    df = pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(schemas.TW_DT),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", df)
    return wh


def _both(tmp_path: Path, idx: list[float], px: list[float], start: dt.date) -> Warehouse:
    wh = _wh_with_index(tmp_path, idx, start)
    _wh_px(tmp_path, px, start)  # 同一個 tmp_path＝同一個資料湖
    return wh


_W2 = pl.DataFrame(
    {"date": [dt.date(2026, 1, 1)] * 2, "stock_id": ["1101", "2330"], "target_weight": [0.5, 0.5]}
)


def test_ac6_tsmom_is_antonacci_times_volscaling(tmp_path: Path) -> None:
    """MOP = 方向閘門 × 波動縮放。方向為負時空手（同 Antonacci）；為正時**還要**縮放。"""
    start = dt.date(2026, 1, 1)
    # 指數下跌 → 方向為負 → 空手，且**不必**看波動
    wh = _both(tmp_path / "dn", [100.0 - i for i in range(20)], [100.0] * 20, start)
    d = start + dt.timedelta(days=20)
    p: dict[str, object] = {"mom_window": 20, "vol_window": 10}
    assert tsmom_vol_scaling(d, _W2, wh.view(d), p)["target_weight"][0] == pytest.approx(0.0)

    # 指數上漲、股價劇烈震盪 → 在場但被縮放：倍數 < 1，而 Antonacci 會給滿倉
    idx = [100.0 + i for i in range(20)]
    px = [100.0 * (1.15 if i % 2 else 0.85) for i in range(20)]  # 每日 ±15%，年化波動遠超 20%
    wh2 = _both(tmp_path / "up", idx, px, start)
    v = wh2.view(d)
    m_ts = tsmom_vol_scaling(d, _W2, v, p)["target_weight"][0]
    m_abs = absolute_momentum_gate(d, _W2, v, {"window": 20})["target_weight"][0]
    assert m_abs == pytest.approx(0.5)  # Antonacci：滿倉
    assert 0.0 < m_ts < 0.5  # MOP：在場但縮了——這就是兩者的差


def test_ac6_inverse_variance_cuts_harder_than_linear(tmp_path: Path) -> None:
    """Moreira–Muir 用 1/σ²、Barroso 用 1/σ。同一段高波動資料，前者砍得更兇。

    這不是參數差異：兩者的參考波動刻意設成同一個值（0.20），差別只剩指數。
    """
    start = dt.date(2026, 1, 1)
    px = [100.0 * (1.08 if i % 2 else 0.92) for i in range(20)]  # 高波動
    wh = _both(tmp_path, [100.0 + i for i in range(20)], px, start)
    d = start + dt.timedelta(days=20)
    v = wh.view(d)
    lin = vol_target_scaling(d, _W2, v, {"window": 10, "target_vol": 0.20, "min_mult": 0.0})
    sq = inverse_variance_scaling(d, _W2, v, {"vol_window": 10, "ref_vol": 0.20, "min_mult": 0.0})
    assert 0.0 < sq["target_weight"][0] < lin["target_weight"][0]


def test_ac6_crash_gate_needs_both_bear_and_high_vol(tmp_path: Path) -> None:
    """DM 是**條件式**規則：空頭且高波動才砍。單獨成立任一條都不砍——
    這正是它與 Antonacci（只看空頭）和波動縮放（只看波動）的分野。"""
    start = dt.date(2026, 1, 1)
    d = start + dt.timedelta(days=30)
    p: dict[str, object] = {"bear_window": 30, "vol_window": 10, "vol_threshold": 0.25}

    # (1) 空頭 ＋ 高波動 → 砍
    bear_wild = [100.0 * (1 - 0.02 * i) * (1.05 if i % 2 else 0.95) for i in range(30)]
    wh1 = _wh_with_index(tmp_path / "a", bear_wild, start)
    assert crash_risk_gate(d, _W2, wh1.view(d), p)["target_weight"][0] == pytest.approx(0.0)

    # (2) 空頭但低波動（慢跌）→ 不砍。Antonacci 在這裡會空手，DM 不會。
    bear_calm = [100.0 - 0.5 * i for i in range(30)]
    wh2 = _wh_with_index(tmp_path / "b", bear_calm, start)
    assert crash_risk_gate(d, _W2, wh2.view(d), p)["target_weight"][0] == pytest.approx(0.5)
    assert absolute_momentum_gate(d, _W2, wh2.view(d), {"window": 30})["target_weight"][
        0
    ] == pytest.approx(0.0)

    # (3) 多頭但高波動（急漲）→ 不砍。波動縮放在這裡會砍，DM 不會。
    bull_wild = [100.0 * (1 + 0.02 * i) * (1.05 if i % 2 else 0.95) for i in range(30)]
    wh3 = _wh_with_index(tmp_path / "c", bull_wild, start)
    assert crash_risk_gate(d, _W2, wh3.view(d), p)["target_weight"][0] == pytest.approx(0.5)


def test_ac6_all_six_are_registered() -> None:
    for name, fn in (
        ("index_trend_gate", index_trend_gate),
        ("absolute_momentum_gate", absolute_momentum_gate),
        ("tsmom_vol_scaling", tsmom_vol_scaling),
        ("inverse_variance_scaling", inverse_variance_scaling),
        ("crash_risk_gate", crash_risk_gate),
        ("vol_target_scaling", vol_target_scaling),
    ):
        assert get_overlay(name) is fn
