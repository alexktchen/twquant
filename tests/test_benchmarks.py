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
    cumulative_factors,
    index_series,
)

_D = [dt.date(2024, 1, d) for d in (2, 3, 4, 5, 8)]


def _px(rows: list[tuple[dt.date, str, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [r[0] for r in rows],
            "stock_id": [r[1] for r in rows],
            "close": [r[2] for r in rows],
        }
    )


def _ev(rows: list[tuple[dt.date, str, str, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [r[0] for r in rows],
            "stock_id": [r[1] for r in rows],
            "event_type": [r[2] for r in rows],
            "adj_factor": [r[3] for r in rows],
        }
    )


def test_ac11_split_is_adjusted_even_for_price_return() -> None:
    """**分割根本不是報酬**，不含息路徑也必須還原。

    0050 在 2025-06 的 1:4 分割若只在含息時還原，價格序列會留著斷崖：
    實測那樣算出來是 CAGR −8.66%、MDD −77.18%（2025 −66%）。
    """
    px = _px([(_D[0], "0050", 100.0), (_D[1], "0050", 25.5)])
    ev = _ev([(_D[1], "0050", "split", 0.25)])
    got = benchmark_series(cumulative_factors(px, ev), "0050", total_return=False)
    vals = got["value"].to_list()
    assert vals == [100.0, pytest.approx(102.0)], "分割日的 25.5 應還原成 102，而不是 −74.5%"


def test_ac11_total_return_divides_by_the_dividend_factor_too() -> None:
    """含息時再除以股利因子；分割那一份已經在價格路徑扣掉，不得重複套用。"""
    px = _px([(_D[0], "0050", 100.0), (_D[1], "0050", 25.5)])
    ev = _ev([(_D[1], "0050", "split", 0.25), (_D[1], "0050", "cash_dividend", 0.98)])
    got = benchmark_series(cumulative_factors(px, ev), "0050", total_return=True)
    assert got["value"].to_list() == [100.0, pytest.approx(102.0 / 0.98)]


def test_ac11_total_return_without_any_dividend_event_raises() -> None:
    """含息卻查不到任何股利事件 → raise。

    `corporate_events` 對 380 檔 ETF 只涵蓋 16 檔，缺事件時因子被填成常數 1.0，
    於是「含息」靜默退化成不含息——實測 006208 因此少掉約 4.4pp／年，
    而頁面上照樣標著「含息」。
    """
    px = _px([(_D[0], "006208", 100.0), (_D[1], "006208", 101.0)])
    ev = _ev([(_D[0], "0050", "cash_dividend", 0.98)])  # 別檔的事件
    with pytest.raises(DataError, match="查不到任何股利事件"):
        benchmark_series(cumulative_factors(px, ev), "006208", total_return=True)


def test_ac11_missing_factor_columns_raise_instead_of_falling_back() -> None:
    """沒過 `cumulative_factors` 就用 → raise，不得回退成未還原價。"""
    with pytest.raises(DataError, match="cumulative_factors"):
        benchmark_series(_px([(_D[0], "0050", 100.0)]), "0050", total_return=False)


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


def test_ac11_suspension_ending_in_a_documented_event_may_carry_forward() -> None:
    """停牌可以結轉，但**只在有公司行動可解釋時**——0050 停 5 天是為了 1:4 分割。

    這不是「前值填補缺值」，是停牌期間本來就沒有可成交價格、部位也沒動。
    區別在於：復牌那天是不是一個**有記錄的**事件日。沒有記錄就代表我們不知道
    為什麼缺，那就該吵。
    """
    series = pl.DataFrame({"date": [_D[0], _D[1], _D[4]], "value": [100.0, 101.0, 25.0]})
    # _D[4] 是有記錄的事件日（分割復牌）→ 中間的 _D[2]、_D[3] 可結轉
    got = align_to(series, _D, name="0050", event_dates={_D[4]})
    assert got == [100.0, 101.0, 101.0, 101.0, 25.0]


def test_ac11_unexplained_gap_still_raises_even_when_events_are_supplied() -> None:
    """有事件清單不等於放行全部缺口——缺口後的第一天不是事件日就仍要 raise。

    否則這個參數會退化成「無條件前值填補」，而那正是本模組要防的。
    """
    series = pl.DataFrame({"date": [_D[0], _D[1], _D[4]], "value": [100.0, 101.0, 99.0]})
    with pytest.raises(DataError, match="不得前值填補"):
        align_to(series, _D, name="b", event_dates={_D[1]})  # 事件日不在缺口之後
