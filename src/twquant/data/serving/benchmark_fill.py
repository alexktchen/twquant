"""名單績效的對照（0050）在雲端缺行情、缺除權息時，從 FinMind 補齊（D-176 修訂三）。

雲端的行情表原本只留四碼普通股，0050 的歷史在 2026-10-13 之前都被修剪掉了（之後 `keep_stocks` 才留下），
名單績效因此一直寫「行情中找不到基準 0050」；除權息表在雲端也沒有每天更新（T-333），0050 之後的配息會漏掉，
對照就變成不含息、把帳戶說得比實際好。這裡只補「缺的列」：已經有的列拿來比對，對不上就 raise、不覆蓋
（黃金規則 5——兩個來源說法不一，要先查清楚誰錯）。

只補到「全市場已經有行情的最新一天」：FinMind 比證交所的每日擷取早貼出 0050 時，若先寫進去，
`prices_daily` 的最新日期會跑到全市場還沒有的那天——每日擷取以為那天做過了、名單績效也會挑那天來算。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import polars as pl

from twquant.data import schemas
from twquant.data.serving import schema as sch
from twquant.data.serving.sync import upsert
from twquant.data.sources.finmind import dividend_rows
from twquant.data.sources.finmind_history import clean_finmind_prices
from twquant.errors import DataError

_TABLE = "benchmark_fill"
_TOL = 1e-6  # 相對誤差；兩個來源同一天的價格與還原因子應完全相同
_PRICE_COLS = ("open", "high", "low", "close")


@dataclass(frozen=True)
class FillPlan:
    """要補的列，與比對過、一致的既有列數。"""

    prices: pl.DataFrame
    events: pl.DataFrame
    prices_checked: int
    events_checked: int


def _mismatch(a: pl.Expr, b: pl.Expr) -> pl.Expr:
    return (a - b).abs() > _TOL * pl.max_horizontal(a.abs(), pl.lit(1.0))


def plan_fill(
    code: str,
    upto: dt.date,
    fm_prices: list[dict[str, Any]],
    fm_dividends: list[dict[str, Any]],
    pg_prices: pl.DataFrame,
    pg_events: pl.DataFrame,
) -> FillPlan:
    """FinMind 的行情與除權息（原始列）對照資料庫已有的 → 要補哪些列。

    pg_prices：date、open、high、low、close；pg_events：date、event_type、adj_factor（都只有這檔、這段期間）。
    `upto` 之後的列不補（全市場那天還沒有行情）。
    同一天兩邊都有、數字對不上 → raise。FinMind 沒有任何行情 → raise（來源壞了，不是真的沒交易）。
    """
    if not fm_prices:
        raise DataError(_TABLE, None, f"FinMind 沒有 {code} 的行情——來源異常，不補")
    raw = pl.DataFrame(fm_prices)
    prices = clean_finmind_prices(raw).frame.filter(
        (pl.col("stock_id") == code) & (pl.col("date") <= upto)
    )
    ov = prices.join(pg_prices, on="date", how="inner", suffix="_pg")
    bad = ov.filter(
        pl.any_horizontal(*(_mismatch(pl.col(c), pl.col(f"{c}_pg")) for c in _PRICE_COLS))
    )
    if bad.height:
        raise DataError(
            "prices_daily",
            bad.row(0, named=True)["date"],
            f"{code} 有 {bad.height} 天的價格與 FinMind 不同，例：{bad.head(1).to_dicts()}——不覆蓋，先查清楚",
        )
    have = set(pg_prices.get_column("date").to_list())
    add_prices = prices.filter(~pl.col("date").is_in(list(have)))

    ev_rows = [
        r for r in dividend_rows(fm_dividends) if r["stock_id"] == code and r["date"] <= upto
    ]
    events = pl.DataFrame(
        ev_rows,
        schema={
            "date": pl.Date,
            "stock_id": pl.String,
            "event_type": pl.String,
            "adj_factor": pl.Float64,
            "ref_price": pl.Float64,
            "available_at": schemas.TW_DT,
        },
    )
    ev_ov = events.join(pg_events, on="date", how="inner", suffix="_pg")
    ev_bad = ev_ov.filter(
        (pl.col("event_type") != pl.col("event_type_pg"))
        | _mismatch(pl.col("adj_factor"), pl.col("adj_factor_pg"))
    )
    if ev_bad.height:
        raise DataError(
            "corporate_events",
            ev_bad.row(0, named=True)["date"],
            f"{code} 的除權息與 FinMind 不同，例：{ev_bad.head(1).to_dicts()}——不覆蓋，先查清楚",
        )
    have_ev = set(pg_events.get_column("date").to_list())
    add_events = events.filter(~pl.col("date").is_in(list(have_ev)))
    return FillPlan(add_prices, add_events, ov.height, ev_ov.height)


def market_latest(conn: Any, schema: str, code: str) -> dt.date | None:
    """全市場（這檔以外）在資料庫最新的行情日；沒有任何行情回 None。"""
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT MAX(date) FROM "{schema}"."prices_daily" WHERE stock_id <> %s', (code,)
        )
        row = cur.fetchone()
    return row[0] if row and row[0] is not None else None


def read_existing(
    conn: Any, schema: str, code: str, lo: dt.date, hi: dt.date
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """資料庫裡這檔在 [lo, hi] 已有的行情與除權息。"""
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT date, open, high, low, close FROM "{schema}"."prices_daily" '
            "WHERE stock_id = %s AND date BETWEEN %s AND %s",
            (code, lo, hi),
        )
        prices = pl.DataFrame(
            cur.fetchall(),
            schema={c: (pl.Date if c == "date" else pl.Float64) for c in ("date", *_PRICE_COLS)},
            orient="row",
        )
        cur.execute(
            f'SELECT date, event_type, adj_factor FROM "{schema}"."corporate_events" '
            "WHERE stock_id = %s AND date BETWEEN %s AND %s",
            (code, lo, hi),
        )
        events = pl.DataFrame(
            cur.fetchall(),
            schema={"date": pl.Date, "event_type": pl.String, "adj_factor": pl.Float64},
            orient="row",
        )
    return prices, events


def write_fill(conn: Any, schema: str, plan: FillPlan) -> None:
    """只寫要補的列（同一個交易）。"""
    with conn.cursor() as cur:
        if plan.prices.height:
            spec = sch.spec_of("prices_daily")
            out = schemas.validate("prices_daily", plan.prices.select(list(spec.column_names)))
            upsert(cur, schema, spec, out)
        if plan.events.height:
            spec = sch.spec_of("corporate_events")
            upsert(cur, schema, spec, plan.events.select(list(spec.column_names)))
    conn.commit()
