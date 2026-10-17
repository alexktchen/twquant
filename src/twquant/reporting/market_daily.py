"""大盤體檢與籌碼的每日一列（T-374／D-188）：純運算，資料由呼叫端從供應層取好後傳入。

首頁戰情室要的「昨天的市場長什麼樣」：加權／櫃買漲跌、成交金額對前 N 日平均、漲跌家數、漲停跌停家數、
創 N 日新高／新低家數、三大法人合計買賣超（折成 TWD）、外資連買／連賣天數、融資餘額變化。
只算四碼普通股（同雲端的 stock_pattern），ETF／權證不算進家數與成交金額。

**估算值**：法人買賣超與融資餘額是「股數 × 當天收盤」加總，不是證交所公布的成交金額——2026-10-16 實測外資
上市部分 −421.8 億 vs 證交所 −461.5 億（證交所含 ETF 與實際成交價）。網站與晨報都標「估算」。
漲停／跌停：`limit_up_price` 空著時（供應層目前沒填）用前一天收盤 ±10% 對到升降單位推算（backtest/costs 同一套）。

不算任何訊號、不進模型：這是給人看的體檢表，數字要能一眼對回證交所的盤後統計。
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

import polars as pl

from twquant.backtest.costs import limit_down_proxy, limit_up_proxy
from twquant.errors import DataError

TABLE = "market_daily"


@dataclass(frozen=True)
class MarketDaily:
    """One trading day's market check-up; None means the input did not cover it."""

    date: dt.date
    taiex_close: float | None
    taiex_chg: float | None
    otc_close: float | None
    otc_chg: float | None
    amount: float  # TWD，四碼普通股合計
    amount_avg: float | None  # 前 avg_sessions 個交易日平均
    amount_ratio: float | None
    up: int
    down: int
    flat: int
    limit_up: int
    limit_down: int
    new_high: int | None  # 收盤 > 前 window 個交易日的最高收盤；視窗不足 → None（不知道，不是 0）
    new_low: int | None
    foreign_net: float  # TWD 估算（股數 × 收盤）
    trust_net: float
    dealer_net: float
    foreign_streak: int  # 正＝連續淨買超天數、負＝連續淨賣超天數（含當天）
    margin_balance: float | None  # 融資餘額 TWD 估算（股數 × 當天收盤）
    margin_delta: float | None  # 對前一個交易日（各用各天的收盤）
    short_balance: float | None  # 融券餘額 TWD 估算


def _chg(
    index_rows: pl.DataFrame, index_id: str, day: dt.date
) -> tuple[float | None, float | None]:
    xs = index_rows.filter(pl.col("index_id") == index_id).sort("date", descending=True)
    if xs.is_empty() or xs["date"][0] != day:
        return None, None
    close = float(xs["close"][0])
    if xs.height < 2 or not xs["close"][1] or xs["close"][1] <= 0:
        return close, None
    return close, close / float(xs["close"][1]) - 1


def _streak(values: list[float]) -> int:
    """最新一天往回數同號的天數（0 不算、遇到異號或 0 就停）；最新一天是 0 → 0。"""
    if not values or values[0] == 0:
        return 0
    sign = 1 if values[0] > 0 else -1
    n = 0
    for v in values:
        if (v > 0) - (v < 0) != sign:
            break
        n += 1
    return n * sign


def market_daily(
    day: dt.date,
    prices: pl.DataFrame,
    index_rows: pl.DataFrame,
    institutional: pl.DataFrame,
    margin: pl.DataFrame,
    *,
    stock_pattern: str,
    avg_sessions: int,
    high_low_window: int,
) -> MarketDaily:
    """從 `day`（含）往前的視窗算當天那一列。

    `prices`：date, stock_id, close, amount, limit_up_price, limit_down_price（含 `day` 與之前至少 high_low_window 個交易日）；
    `index_rows`：date, index_id, close；`institutional`：date, stock_id, foreign_net, trust_net, dealer_net（股）；
    `margin`：date, stock_id, margin_balance, short_balance。`day` 沒有行情 → DataError。
    """
    pat = re.compile(stock_pattern)
    px = prices.filter(
        pl.col("stock_id").map_elements(lambda s: bool(pat.match(s)), return_dtype=pl.Boolean)
    )
    sessions = sorted(px["date"].unique().to_list(), reverse=True)
    if not sessions or sessions[0] != day:
        raise DataError(TABLE, day, f"{day} 沒有行情（最新 {sessions[0] if sessions else '無'}）")
    today = px.filter(pl.col("date") == day)
    prior = sessions[1 : 1 + high_low_window]
    prev_day = sessions[1] if len(sessions) > 1 else None

    # 漲跌家數：對前一個交易日的收盤；漲停跌停價空著就用前收推算
    if prev_day is not None:
        prev = px.filter(pl.col("date") == prev_day).select(
            "stock_id", pl.col("close").alias("prev")
        )
        joined = today.join(prev, on="stock_id", how="inner").filter(pl.col("prev") > 0)
        up = int((joined["close"] > joined["prev"]).sum())
        down = int((joined["close"] < joined["prev"]).sum())
        flat = int((joined["close"] == joined["prev"]).sum())
        lim = joined.with_columns(
            pl.coalesce(
                pl.col("limit_up_price"),
                pl.col("prev").map_elements(limit_up_proxy, return_dtype=pl.Float64),
            ).alias("lu"),
            pl.coalesce(
                pl.col("limit_down_price"),
                pl.col("prev").map_elements(limit_down_proxy, return_dtype=pl.Float64),
            ).alias("ld"),
        )
        limit_up = int((lim["close"] >= lim["lu"]).sum())
        limit_down = int((lim["close"] <= lim["ld"]).sum())
    else:
        up = down = flat = limit_up = limit_down = 0

    # 新高新低：對前 window 個交易日的最高／最低收盤（不含當天）；視窗不足就說不知道
    if len(prior) == high_low_window:
        hist = (
            px.filter(pl.col("date").is_in(prior))
            .group_by("stock_id")
            .agg(
                pl.col("close").max().alias("hi"),
                pl.col("close").min().alias("lo"),
                pl.len().alias("n"),
            )
            .filter(pl.col("n") == len(prior))  # 視窗內少天的（新上市）不算
        )
        hl = today.join(hist, on="stock_id", how="inner")
        new_high: int | None = int((hl["close"] > hl["hi"]).sum())
        new_low: int | None = int((hl["close"] < hl["lo"]).sum())
    else:
        new_high = new_low = None

    amount = float(today["amount"].fill_null(0.0).sum())
    prior_amt = sessions[1 : 1 + avg_sessions]
    if len(prior_amt) == avg_sessions:
        daily = (
            px.filter(pl.col("date").is_in(prior_amt)).group_by("date").agg(pl.col("amount").sum())
        )
        amount_avg: float | None = float(daily.select(pl.col("amount").mean()).item())
        amount_ratio: float | None = amount / amount_avg if amount_avg and amount_avg > 0 else None
    else:
        amount_avg = amount_ratio = None

    # 三大法人：股數 × 當天收盤 → TWD；外資連續天數看每天的合計金額符號
    inst = institutional.join(
        px.select("date", "stock_id", "close"), on=["date", "stock_id"], how="inner"
    )
    by_day = (
        inst.group_by("date")
        .agg(
            (pl.col("foreign_net") * pl.col("close")).sum().alias("f"),
            (pl.col("trust_net") * pl.col("close")).sum().alias("t"),
            (pl.col("dealer_net") * pl.col("close")).sum().alias("d"),
        )
        .sort("date", descending=True)
    )
    today_inst = by_day.filter(pl.col("date") == day)
    if today_inst.is_empty():
        raise DataError(TABLE, day, f"{day} 沒有三大法人買賣超")
    foreign_net = float(today_inst["f"][0])
    trust_net = float(today_inst["t"][0])
    dealer_net = float(today_inst["d"][0])
    streak = _streak([float(v) for v in by_day["f"].to_list()])

    mg = margin.join(px.select("date", "stock_id", "close"), on=["date", "stock_id"], how="inner")
    mg_day = (
        mg.group_by("date")
        .agg(
            (pl.col("margin_balance") * pl.col("close")).sum().alias("m"),
            (pl.col("short_balance") * pl.col("close")).sum().alias("s"),
        )
        .sort("date", descending=True)
    )
    m_today = mg_day.filter(pl.col("date") == day)
    margin_balance: float | None
    margin_delta: float | None
    short_balance: float | None
    if m_today.is_empty():
        margin_balance = margin_delta = short_balance = None
    else:
        margin_balance = float(m_today["m"].item(0))
        short_balance = float(m_today["s"].item(0))
        m_prev = mg_day.filter(pl.col("date") < day)
        margin_delta = (
            margin_balance - float(m_prev["m"].item(0)) if not m_prev.is_empty() else None
        )

    taiex_close, taiex_chg = _chg(index_rows, "TAIEX", day)
    otc_close, otc_chg = _chg(index_rows, "TPEX", day)
    return MarketDaily(
        date=day,
        taiex_close=taiex_close,
        taiex_chg=taiex_chg,
        otc_close=otc_close,
        otc_chg=otc_chg,
        amount=amount,
        amount_avg=amount_avg,
        amount_ratio=amount_ratio,
        up=up,
        down=down,
        flat=flat,
        limit_up=limit_up,
        limit_down=limit_down,
        new_high=new_high,
        new_low=new_low,
        foreign_net=foreign_net,
        trust_net=trust_net,
        dealer_net=dealer_net,
        foreign_streak=streak,
        margin_balance=margin_balance,
        margin_delta=margin_delta,
        short_balance=short_balance,
    )
