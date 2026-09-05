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


_SPLIT_TYPES = ("split", "reverse_split")


def cumulative_factors(prices: pl.DataFrame, events: pl.DataFrame) -> pl.DataFrame:
    """加兩欄還原因子：`adj_split`（**一律**套用）與 `adj_div`（只在含息時套用）。

    **為什麼一定要拆開**：分割根本不是報酬，是計價單位改變，任何情況都必須還原；
    股利才是「含不含息」的選擇。把兩者混成單一因子、再讓不含息路徑整個跳過，
    會讓 0050 的價格序列留著 1:4 分割的斷崖——實測那樣算出來是 CAGR **−8.66%**、
    MDD **−77.18%**（2025 年 −66%），而正確值是 +34.5%／−27.5%。
    """
    need = {_DATE, "stock_id", "event_type", "adj_factor"}
    missing = sorted(need - set(events.columns))
    if missing:
        raise DataError("corporate_events", None, f"事件表缺欄位 {missing}")
    bad = events.filter(pl.col("adj_factor").is_null() | (pl.col("adj_factor") <= 0.0))
    if bad.height:
        raise DataError(
            "corporate_events",
            None,
            f"adj_factor 須 > 0 且非 null，違規 {bad.height} 列——0 或負值會經累乘污染該股全部歷史",
        )
    out = prices.sort(_DATE)
    for col, keep in (("adj_split", True), ("adj_div", False)):
        sel = pl.col("event_type").is_in(_SPLIT_TYPES)
        cum = (
            events.filter(sel if keep else ~sel)
            .select(_DATE, "stock_id", "adj_factor")
            .sort("stock_id", _DATE)
            .with_columns(**{col: pl.col("adj_factor").cum_prod().over("stock_id")})
            .select("stock_id", _DATE, col)
            .sort(_DATE)
        )
        out = out.join_asof(cum, on=_DATE, by="stock_id", strategy="backward").with_columns(
            pl.col(col).fill_null(1.0)
        )
    return out


def benchmark_series(prices: pl.DataFrame, stock_id: str, *, total_return: bool) -> pl.DataFrame:
    """由行情取單一標的的基準序列，回傳 (date, value)。需先過 `cumulative_factors`。

    **分割一律還原**（`close / adj_split`）——它不是報酬，是計價單位改變。
    `total_return=True` 時再除以 `adj_div`（股利）。

    含息卻查不到任何股利事件 → raise。`corporate_events` 對 380 檔 ETF 只涵蓋 16 檔，
    而缺事件時累積因子會被填成常數 1.0，於是「含息」**靜默退化成不含息**——實測
    006208 因此少掉約 4.4pp／年（+35.64% vs 應有的水準），而頁面上照樣標著「含息」。
    """
    need = {_DATE, "stock_id", "close", "adj_split"} | ({"adj_div"} if total_return else set())
    missing = sorted(need - set(prices.columns))
    if missing:
        raise DataError(
            "prices_daily",
            None,
            f"基準 {stock_id} 缺欄位 {missing}——請先過 `cumulative_factors`；不得回退成未還原價。",
        )
    sub = prices.filter(pl.col("stock_id") == stock_id).sort(_DATE)
    if sub.height == 0:
        raise DataError("prices_daily", None, f"行情中找不到基準 {stock_id}")
    if total_return and sub["adj_div"].n_unique() <= 1:
        raise DataError(
            "corporate_events",
            None,
            f"基準 {stock_id} 要求含息，但整段期間查不到任何股利事件"
            f"（adj_div 恆為 {sub['adj_div'][0]}）。這幾乎一定是事件表沒涵蓋它，"
            "而不是它真的不配息——靜默當成不含息會讓報表標著「含息」卻是價格報酬。",
        )
    value = pl.col("close") / pl.col("adj_split")
    if total_return:
        value = value / pl.col("adj_div")
    return sub.select(_DATE, value.alias(_VALUE)).filter(pl.col(_VALUE) > 0.0)


def index_series(index_daily: pl.DataFrame, index_id: str) -> pl.DataFrame:
    """由 `index_daily` 取指數序列。指數是**價格指數、不含息**，呼叫端須據實標示。"""
    sub = index_daily.filter(pl.col("index_id") == index_id).sort(_DATE)
    if sub.height == 0:
        raise DataError("index_daily", None, f"index_daily 中找不到 {index_id}")
    return sub.select(_DATE, pl.col("close").alias(_VALUE)).filter(pl.col(_VALUE) > 0.0)


def align_to(
    series: pl.DataFrame,
    dates: list[dt.date],
    *,
    name: str,
    event_dates: set[dt.date] | None = None,
) -> list[float]:
    """把基準對齊到策略實際走過的交易日；缺的日子**只在有公司行動可解釋時**才結轉。

    無條件前值填補會讓 MDD 被系統性低估——缺的那天往往正是停牌或大跌那天——而且
    不會有任何錯誤訊息。寧可吵，不要安靜地算出一個看起來合理的錯數字（黃金規則 5）。

    `event_dates`（該標的在 `corporate_events` 的事件日）放行**一種**缺口：
    停牌期間本來就沒有可成交價格、部位也沒動，復牌那天是有記錄的事件日。
    0050 為了 1:4 分割停牌 2025-06-11~06-17 就是這個形狀。

    判準刻意收得很緊：**一段連續缺口，只有當它之後的第一個有值日是事件日時才放行**。
    否則這個參數會退化成「無條件前值填補」，而那正是本函式要防的。
    """
    have = dict(zip(series[_DATE].to_list(), series[_VALUE].to_list(), strict=True))
    events = event_dates or set()
    out: list[float] = []
    pending: list[dt.date] = []
    last: float | None = None
    for d in dates:
        if d in have:
            if pending:
                if d not in events or last is None:
                    raise DataError(
                        "benchmark",
                        pending[0],
                        f"基準 {name} 缺 {len(pending)} 個策略交易日"
                        f"（{pending[0]} ~ {pending[-1]}），而復牌日 {d} "
                        f"{'不是有記錄的公司行動日' if last is not None else '之前沒有任何值'}。"
                        "不得前值填補：缺的那天往往正是大跌那天，填補會讓 MDD 被系統性低估。",
                    )
                out.extend([last] * len(pending))
                pending = []
            last = float(have[d])
            out.append(last)
        else:
            pending.append(d)
    if pending:
        raise DataError(
            "benchmark",
            pending[0],
            f"基準 {name} 末端缺 {len(pending)} 個策略交易日"
            f"（{pending[0]} ~ {pending[-1]}），其後沒有任何有值日可判斷缺口原因。",
        )
    return out


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
    event_dates: set[dt.date] | None = None,
) -> BenchStats:
    """把基準序列換算成與策略同口徑的指標（同交易日、同 `compute_metrics`）。

    `turnover` 對基準沒有意義，固定 0.0——買進持有沒有周轉。
    `event_dates` 見 `align_to`：只放行有公司行動可解釋的停牌。
    """
    values = align_to(series, dates, name=label, event_dates=event_dates)
    return BenchStats(
        key=key,
        label=label,
        role=role,
        total_return=total_return,
        metrics=compute_metrics(values, turnover_annualized=0.0),
        annual=annual_returns(dates, values),
        n_days=len(values),
    )
