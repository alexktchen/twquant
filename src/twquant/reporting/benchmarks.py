"""基準統計（SPEC-013 §6.1，AC-11）：把基準序列換算成與策略**同口徑**的指標。

AC-11 要求儀表板的比較值一律取自這裡、不得寫死在字串裡——先前就是因為寫死，
頁面上出現過「TAIEX −34.37%」，而 −34.37% 其實是等權宇宙的回撤，不是 TAIEX 的。

本模組是純函數、無 IO：價格由呼叫端讀進來（黃金規則 10）。

**對齊是這裡最容易出錯的地方。** 基準必須在**策略實際走過的那些交易日**上評估，
否則 CAGR 的年數、MDD 的取樣點都不同，兩個數字放在同一列比較就是錯的。
故 `bench_stats` 要求基準涵蓋策略的每一個交易日，缺一天即 raise——內插或前值填補
會讓 MDD 被系統性低估（缺的那天往往正是大跌那天），而且不會有任何錯誤訊息。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import polars as pl

from twquant.backtest.metrics import Metrics, compute_metrics
from twquant.errors import DataError

_DATE = "date"
_VALUE = "value"


@dataclass(frozen=True)
class BenchStats:
    """一個基準在策略窗口內的統計。

    `role` 是**這個基準代表什麼**的白話說明——沒有它，讀者會把「輸給 0050」與
    「輸給等權宇宙」當成同一件事，而那是 SPEC-013 §0 診斷裡最關鍵的區分。
    """

    key: str  # 0050_tr / TAIEX ...
    label: str  # 顯示名稱
    role: str  # 這個基準回答什麼問題
    total_return: bool  # 是否含息（不含息的不得與含息的並列比較）
    metrics: Metrics
    annual: dict[int, float]  # 各曆年報酬（小數）
    n_days: int


def benchmark_series(prices: pl.DataFrame, stock_id: str, *, total_return: bool) -> pl.DataFrame:
    """由行情取單一標的的基準序列，回傳 (date, value)。

    `total_return=True` 時用還原價（`close / adj`），需含 `adj` 欄；缺欄即 raise
    ——靜默回退成未還原價會讓 0050 的年化少掉約 3~4pp 的股利，而那正是
    「策略有沒有輸給大盤」這個問題的量級。
    """
    need = {_DATE, "stock_id", "close"} | ({"adj"} if total_return else set())
    missing = sorted(need - set(prices.columns))
    if missing:
        raise DataError(
            "prices_daily",
            None,
            f"基準 {stock_id} 缺欄位 {missing}"
            + ("；total_return=True 需要 adj，不得回退成未還原價。" if "adj" in missing else ""),
        )
    sub = prices.filter(pl.col("stock_id") == stock_id).sort(_DATE)
    if sub.height == 0:
        raise DataError("prices_daily", None, f"行情中找不到基準 {stock_id}")
    value = (pl.col("close") / pl.col("adj")) if total_return else pl.col("close")
    return sub.select(_DATE, value.alias(_VALUE)).filter(pl.col(_VALUE) > 0.0)


def index_series(index_daily: pl.DataFrame, index_id: str) -> pl.DataFrame:
    """由 `index_daily` 取指數序列。指數是**價格指數、不含息**，呼叫端須據實標示。"""
    sub = index_daily.filter(pl.col("index_id") == index_id).sort(_DATE)
    if sub.height == 0:
        raise DataError("index_daily", None, f"index_daily 中找不到 {index_id}")
    return sub.select(_DATE, pl.col("close").alias(_VALUE)).filter(pl.col(_VALUE) > 0.0)


def align_to(series: pl.DataFrame, dates: list[dt.date], *, name: str) -> list[float]:
    """把基準對齊到策略實際走過的交易日；缺任何一天即 raise。

    前值填補會讓 MDD 被系統性低估——缺的那天往往正是停牌或大跌那天——而且不會有
    任何錯誤訊息。寧可吵，不要安靜地算出一個看起來合理的錯數字（黃金規則 5）。
    """
    have = dict(zip(series[_DATE].to_list(), series[_VALUE].to_list(), strict=True))
    missing = [d for d in dates if d not in have]
    if missing:
        raise DataError(
            "benchmark",
            missing[0],
            f"基準 {name} 缺 {len(missing)} 個策略交易日（最早 {missing[0]}、最晚 "
            f"{missing[-1]}）。不得前值填補：缺的那天往往正是大跌那天，"
            "填補會讓 MDD 被系統性低估。",
        )
    return [float(have[d]) for d in dates]


def annual_returns(dates: list[dt.date], values: list[float]) -> dict[int, float]:
    """各曆年報酬（小數）。年初基準取**前一年最後一個交易日**，故首年是部分年度。

    首年不年化、也不補齊——那是實際持有期間的報酬，標成「全年」會誤導。
    """
    if len(dates) != len(values):
        raise ValueError(f"日期與數值長度不同：{len(dates)} vs {len(values)}")
    out: dict[int, float] = {}
    last_of: dict[int, tuple[dt.date, float]] = {}
    for d, v in zip(dates, values, strict=True):
        last_of[d.year] = (d, v)
    years = sorted(last_of)
    for i, y in enumerate(years):
        base = last_of[years[i - 1]][1] if i > 0 else values[0]
        if base > 0:
            out[y] = last_of[y][1] / base - 1.0
    return out


def bench_stats(
    series: pl.DataFrame,
    dates: list[dt.date],
    *,
    key: str,
    label: str,
    role: str,
    total_return: bool,
) -> BenchStats:
    """把基準序列換算成與策略同口徑的指標（同交易日、同 `compute_metrics`）。

    `turnover` 對基準沒有意義，固定 0.0——買進持有沒有周轉。
    """
    values = align_to(series, dates, name=label)
    return BenchStats(
        key=key,
        label=label,
        role=role,
        total_return=total_return,
        metrics=compute_metrics(values, turnover_annualized=0.0),
        annual=annual_returns(dates, values),
        n_days=len(values),
    )
