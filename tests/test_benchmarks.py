"""T-126／AC-11：基準統計。

這裡釘的是三個「安靜地算錯」的失效模式：
1. 基準沒對齊到策略的交易日 → CAGR 的年數與 MDD 的取樣點都不同，比較無效
2. 缺值前值填補 → MDD 被系統性低估（缺的那天往往正是大跌那天）
3. total_return 缺 `adj` 時回退成未還原價 → 0050 少掉約 3~4pp 的股利
"""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from twquant.errors import DataError
from twquant.reporting.benchmarks import (
    align_to,
    annual_returns,
    bench_stats,
    benchmark_series,
    index_series,
)

_D = [dt.date(2024, 1, d) for d in (2, 3, 4, 5, 8)]


def _prices(rows: list[tuple[dt.date, str, float, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [r[0] for r in rows],
            "stock_id": [r[1] for r in rows],
            "close": [r[2] for r in rows],
            "adj": [r[3] for r in rows],
        }
    )


def test_ac11_total_return_uses_adj_not_close() -> None:
    """含息序列必須是 close/adj。用 close 會少掉股利——那正是「有沒有輸給 0050」的量級。"""
    px = _prices([(_D[0], "0050", 100.0, 1.0), (_D[1], "0050", 100.0, 0.9)])
    got = benchmark_series(px, "0050", total_return=True)["value"].to_list()
    assert got == [100.0, pytest.approx(111.111111, rel=1e-6)]
    # 未還原時同一組價格看起來是「零報酬」——除息被誤讀成沒漲
    plain = benchmark_series(px, "0050", total_return=False)["value"].to_list()
    assert plain == [100.0, 100.0]


def test_ac11_total_return_without_adj_raises_instead_of_falling_back() -> None:
    """缺 adj 卻要求含息 → raise。靜默回退會產生一個看起來合理的錯數字。"""
    px = pl.DataFrame({"date": [_D[0]], "stock_id": ["0050"], "close": [100.0]})
    with pytest.raises(DataError, match="adj"):
        benchmark_series(px, "0050", total_return=True)


def test_ac11_missing_benchmark_day_raises_instead_of_forward_filling() -> None:
    """基準缺策略走過的某一天 → raise，不得前值填補。

    前值填補會讓那天的報酬變成 0，而缺的那天往往正是大跌那天，MDD 因此被低估。
    """
    series = pl.DataFrame({"date": [_D[0], _D[1], _D[3]], "value": [100.0, 99.0, 90.0]})
    with pytest.raises(DataError, match="不得前值填補"):
        align_to(series, _D[:4], name="測試基準")


def test_ac11_aligned_values_follow_the_strategy_dates_exactly() -> None:
    """對齊後的順序與長度必須與策略日期一致（多出來的基準日要被丟掉）。"""
    series = pl.DataFrame(
        {"date": [*_D, dt.date(2024, 1, 9)], "value": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]}
    )
    assert align_to(series, [_D[1], _D[3]], name="b") == [2.0, 4.0]


def test_ac11_annual_returns_chain_from_the_prior_year_close() -> None:
    """次年的年報酬基準是**前一年最後一個交易日**，不是次年第一天。

    用次年第一天當基準會把跨年那天的報酬憑空丟掉。
    """
    dates = [dt.date(2024, 12, 30), dt.date(2024, 12, 31), dt.date(2025, 1, 2)]
    vals = [100.0, 110.0, 121.0]
    got = annual_returns(dates, vals)
    assert got[2024] == pytest.approx(0.10)  # 100 → 110（首年由序列第一點起算）
    assert got[2025] == pytest.approx(0.10)  # 110 → 121，而不是 121/121-1 = 0


def test_ac11_stats_are_computed_on_the_strategy_trading_days() -> None:
    """指標由對齊後的序列算出，`n_days` 必須等於策略的交易日數。"""
    series = pl.DataFrame({"date": _D, "value": [100.0, 101.0, 102.0, 101.0, 105.0]})
    st = bench_stats(series, _D, key="k", label="測試", role="這是角色說明", total_return=True)
    assert st.n_days == len(_D)
    assert st.metrics.n_days == len(_D)
    assert st.metrics.mdd < 0.0  # 中間有一天下跌，MDD 不得為 0
    assert st.role  # 角色說明不得為空——沒有它讀者會混淆不同基準的意義


def test_ac11_index_series_is_labelled_and_found() -> None:
    idx = pl.DataFrame(
        {"date": _D[:2], "index_id": ["TAIEX", "TAIEX"], "close": [17000.0, 17100.0]}
    )
    assert index_series(idx, "TAIEX")["value"].to_list() == [17000.0, 17100.0]
    with pytest.raises(DataError, match="TPEX"):
        index_series(idx, "TPEX")
