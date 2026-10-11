"""T-343／D-173（A 級，先凍結）：技術分析排列組合的純函數——指標、濾網、出場與挑選規則就是登記寫的那樣。"""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl
import pytest
from scripts.d173_ta_grid import (
    divergence_trigger,
    exit_day,
    exit_triggers,
    filter_masks,
    month_block_t,
    rsi_expr,
    select,
    sidak_t,
    subsets,
    summarize,
    trade_outcomes,
)

nan = np.nan


def test_subsets_cover_all_16_with_empty_first() -> None:
    s = subsets(("T", "B", "V", "M"))
    assert len(s) == 16 and s[0] == () and s[-1] == ("T", "B", "V", "M")


def test_rsi_is_100_when_only_gains_and_null_before_warmup() -> None:
    df = pl.DataFrame({"ac": [float(x) for x in range(1, 20)]})
    r = df.select(rsi_expr("ac", 14)).to_series().to_list()
    assert r[0] is None and r[13] is None and r[14] == pytest.approx(100.0)


def test_rsi_matches_wilder_hand_calc() -> None:
    # 交替 +1／−1：上漲與下跌的 Wilder 平均相等 → RSI 趨近 50
    px = [100.0 + (i % 2) for i in range(60)]
    r = pl.DataFrame({"ac": px}).select(rsi_expr("ac", 14)).to_series().to_list()
    assert 40.0 < r[-1] < 60.0


def test_filter_masks_definitions_and_nulls_are_false() -> None:
    df = pl.DataFrame(
        {
            "ac": [110.0, 100.0, 103.0, 100.0],
            "ma200": [100.0, 101.0, 100.0, None],
            "ma200_lag": [99.0, 99.0, 101.0, 90.0],
            "taiex_ok": [True, True, True, True],
            "hi60": [106.0, 100.0, 100.0, 100.0],
            "volume": [150.0, 149.0, 300.0, 300.0],
            "vbar": [100.0, 100.0, 100.0, None],
            "macd": [1.0, 1.0, -1.0, 1.0],
            "macd_sig": [0.5, 0.5, 0.0, 0.0],
            "rsi": [60.0, 85.0, 60.0, 50.0],
        }
    )
    m = filter_masks(df)
    assert m["T"].tolist() == [
        True,
        False,
        False,
        False,
    ]  # 第 2 列跌破、第 3 列均線下彎、第 4 列 null
    assert m["B"].tolist() == [True, False, True, False]  # 110 ≥ 106×1.03＝109.18；103 ≥ 103
    assert m["V"].tolist() == [True, False, True, False]
    assert m["M"].tolist() == [True, False, False, True]  # RSI 85 超過 80；MACD 在訊號線下


def test_exit_day_is_next_open_after_first_trigger() -> None:
    t = np.array([[False, True, True], [False, False, False]])
    assert exit_day(t, 21).tolist() == [2, 21]


def test_divergence_needs_new_high_with_lower_rsi_then_break() -> None:
    close = np.array([[10.0, 11.0, 12.0, 11.5, 11.0, 10.9]])
    rsi = np.array([[50.0, 70.0, 65.0, 60.0, 55.0, 50.0]])
    # 第 2 天收盤 12 創新高、RSI 65 < 70 → 確認線＝第 0～2 天最低 10；之後沒跌破 10 → 不觸發
    assert not divergence_trigger(close, rsi, 5).any()
    close2 = np.array([[10.0, 11.0, 12.0, 11.5, 9.9, 9.0]])
    t = divergence_trigger(close2, rsi, 5)
    assert t[0].tolist() == [False, False, False, False, True, True]
    # 沒有背離（RSI 跟著創高）→ 永不觸發
    assert not divergence_trigger(close2, np.array([[50.0, 60, 70, 80, 90, 95]]), 5).any()


def test_failed_breakout_only_applies_to_breakout_entries() -> None:
    n, d = 2, 3
    p = {
        "ac": np.array([[105.0, 99.0, 98.0], [95.0, 90.0, 89.0]]),
        "ma50": np.full((n, d), 50.0),
        "ma50_lag": np.full((n, d), 40.0),
        "rsi": np.full((n, d), 50.0),
        "entry": np.array([104.0, 96.0]),
        "level": np.array([100.0, 100.0]),
        "brk": np.array([True, False]),
    }
    t = exit_triggers(p)
    assert t["X1"][0].tolist() == [False, True, True]
    assert not t["X1"][1].any()  # 不是突破進場：失敗突破不適用
    assert not t["X2"].any()  # 收盤都在 50 日線之上
    assert t["X4"][1].tolist() == [False, False, False]  # 89 > 96×0.85＝81.6


def test_stop_and_trend_break() -> None:
    p = {
        "ac": np.array([[100.0, 84.9, 80.0]]),
        "ma50": np.array([[90.0, 90.0, 89.0]]),
        "ma50_lag": np.array([[91.0, 91.0, 91.0]]),
        "rsi": np.full((1, 3), 50.0),
        "entry": np.array([100.0]),
        "level": np.array([nan]),
        "brk": np.array([False]),
    }
    t = exit_triggers(p)
    assert t["X4"][0].tolist() == [False, True, True]  # 84.9 ≤ 85
    assert t["X2"][0].tolist() == [False, True, True]  # 跌破 50 日線且均線下彎


def test_trade_outcomes_net_days_and_hit() -> None:
    ao = np.array([[100.0, 101, 102, 130], [100.0, 90, 80, 70]])
    ac = np.array([[100.0, 125, 102, 130], [100.0, 90, 80, 70]])
    o = trade_outcomes(ao, ac, np.array([2, 3]))
    assert o["net"][0] == pytest.approx(0.02 - 0.004)
    assert o["net"][1] == pytest.approx(-0.30 - 0.004)
    assert o["days"].tolist() == [2.0, 3.0]
    assert o["hit"].tolist() == [True, False]  # 第 1 天收盤 125 ≥ +20%（持有中，出場日不算）


def test_summarize_per_day_is_total_net_over_total_days() -> None:
    s = summarize(
        np.array([0.10, -0.02]),
        np.array([5.0, 15.0]),
        np.array([True, False]),
        np.array(["2020-01", "2020-02"]),
    )
    assert s["per_day"] == pytest.approx(0.08 / 20.0)
    assert s["loss15"] == 0.0 and s["hit"] == 0.5 and s["n"] == 2


def test_month_block_t_and_sidak() -> None:
    t, m = month_block_t(np.array(["a", "a", "b", "c"]), np.array([0.1, 0.3, 0.1, 0.4]))
    assert m == 3 and t > 0
    assert sidak_t(512, 0.05) == pytest.approx(3.89, abs=0.02)
    assert sidak_t(1, 0.05) == pytest.approx(1.96, abs=0.01)


def test_select_applies_registered_rule() -> None:
    rows = [
        {"combo": "a", "n": 500, "t": 5.0, "per_day": 0.002},
        {"combo": "b", "n": 100, "t": 9.0, "per_day": 0.009},  # 筆數不足
        {"combo": "c", "n": 300, "t": 3.0, "per_day": 0.008},  # t 不到門檻
        {"combo": "d", "n": 300, "t": 4.0, "per_day": 0.003},
        {"combo": "e", "n": 300, "t": 4.5, "per_day": 0.001},
        {"combo": "f", "n": 300, "t": 4.5, "per_day": 0.0005},
        {"combo": "g", "n": 0},
    ]
    assert select(rows, 3.89) == ["d", "a", "e"]
    assert select([{"combo": "x", "n": 400, "t": 1.0, "per_day": 0.01}], 3.89) == []


def test_dates_constant_guard() -> None:
    from scripts.d173_ta_grid import ENTRY_FROM, PATH_END

    assert dt.date(2017, 10, 2) == ENTRY_FROM and dt.date(2026, 4, 30) == PATH_END
