"""主力買賣超（T-424／D-205）：每檔每天「前 15 大券商、排除隔日沖分點」的淨買超股數。

數學同 `factors/broker.py`（`daytrade_events` → `cumulate_events` → `clean_daily`），但改成**逐日增量**：
雲端只留幾天 `broker_trades`，隔日沖判定要的全期累計放在小表 `broker_daytrade_events`——
地端用全歷史寫一列「截至種子日的累計」當底，之後每天只加當天那一列（`seed` → `refresh_day`）。
純函數（`day_events`、`day_net`）與批次版逐列相同，`tests/test_main_force.py` 守著。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import polars as pl

from twquant.data.serving.schema import (
    BROKER_DAYTRADE_EVENTS_SPEC,
    MAIN_FORCE_DAILY_SPEC,
    ensure_tables,
)
from twquant.data.serving.sync import upsert
from twquant.errors import DataError

_NET = pl.col("buy_shares") - pl.col("sell_shares")


def day_events(prev_trades: pl.DataFrame, day_trades: pl.DataFrame, day: dt.date) -> pl.DataFrame:
    """結算日＝day 的事件：前一交易日每筆買超列 × day 同檔同分點是否賣超 → (broker_id, resolve_date, n_buys, n_flip)。"""
    buys = prev_trades.filter(_NET > 0).select("stock_id", "broker_id")
    sells = day_trades.filter(_NET < 0).select("stock_id", "broker_id", _flip=pl.lit(1))
    return (
        buys.join(sells, on=["stock_id", "broker_id"], how="left")
        .group_by("broker_id")
        .agg(n_buys=pl.len().cast(pl.Int64), n_flip=pl.col("_flip").count().cast(pl.Int64))
        .with_columns(resolve_date=pl.lit(day, dtype=pl.Date))
        .select("broker_id", "resolve_date", "n_buys", "n_flip")
        .sort("broker_id")
    )


def flagged(state: pl.DataFrame, min_ratio: float, min_events: float) -> pl.Series:
    """累計統計 (broker_id, n_buys, n_flip) → 已達門檻的隔日沖分點。"""
    return state.filter(
        (pl.col("n_buys") >= min_events) & (pl.col("n_flip") / pl.col("n_buys") >= min_ratio)
    )["broker_id"]


def day_net(day_trades: pl.DataFrame, daytraders: pl.Series) -> pl.DataFrame:
    """當天排除隔日沖分點後的主力淨買超 → (date, stock_id, net_shares, available_at)。"""
    return (
        day_trades.filter(~pl.col("broker_id").is_in(daytraders.implode()))
        .group_by("date", "stock_id")
        .agg(net_shares=_NET.sum().cast(pl.Int64), available_at=pl.col("available_at").max())
        .sort("date", "stock_id")
    )


def _params(params: dict[str, object]) -> tuple[float, float]:
    ratio, events = params["daytrade_min_ratio"], params["daytrade_min_events"]
    if not isinstance(ratio, int | float) or not isinstance(events, int | float):
        raise DataError("broker_daytrade_events", None, f"隔日沖門檻不是數字：{params}")
    return float(ratio), float(events)


def _trades(conn: Any, schema: str, day: dt.date) -> pl.DataFrame:
    return pl.read_database(
        f'SELECT date, stock_id, broker_id, buy_shares, sell_shares, available_at FROM "{schema}".broker_trades WHERE date = %(d)s',
        conn,
        execute_options={"params": {"d": day}},
        schema_overrides={"buy_shares": pl.Int64, "sell_shares": pl.Int64},
    )


def _state(conn: Any, schema: str, day: dt.date) -> pl.DataFrame:
    return pl.read_database(
        f'SELECT broker_id, sum(n_buys)::bigint AS n_buys, sum(n_flip)::bigint AS n_flip FROM "{schema}".broker_daytrade_events '
        "WHERE resolve_date <= %(d)s GROUP BY broker_id",
        conn,
        execute_options={"params": {"d": day}},
        schema_overrides={"n_buys": pl.Int64, "n_flip": pl.Int64},
    )


def _replace(conn: Any, schema: str, spec: Any, key: str, day: dt.date, df: pl.DataFrame) -> None:
    """先刪 key＝day 再寫（重跑時某分點／某檔消失也不殘留）。"""
    with conn.cursor() as cur:
        cur.execute(f'DELETE FROM "{schema}"."{spec.name}" WHERE {key} = %s', (day,))
        if not df.is_empty():
            upsert(cur, schema, spec, df.select(list(spec.column_names)))
    conn.commit()


def refresh_day(
    conn: Any, schema: str, day: dt.date, params: dict[str, object], now: dt.datetime
) -> str:
    """day 的隔日沖事件與主力淨買超 → PG（冪等）。沒有當天分點＝略過；沒有種子＝raise。"""
    if now.tzinfo is None:
        raise ValueError("now 必須帶時區")
    ensure_tables(conn, (BROKER_DAYTRADE_EVENTS_SPEC, MAIN_FORCE_DAILY_SPEC), schema)
    trades = _trades(conn, schema, day)
    if trades.is_empty():
        return f"{day} 沒有分點資料，略過"
    with conn.cursor() as cur:
        cur.execute(f'SELECT max(date) FROM "{schema}".prices_daily WHERE date < %s', (day,))
        prev = cur.fetchone()[0]
        cur.execute(
            f'SELECT count(*) FROM "{schema}".broker_daytrade_events WHERE resolve_date < %s',
            (day,),
        )
        seeded = cur.fetchone()[0] > 0
    if not seeded:
        raise DataError(
            "broker_daytrade_events",
            day,
            "沒有種子（隔日沖累計）：先在地端跑 `twquant serving main-force-seed`，否則每個分點都會被當成正常",
        )
    note = ""
    prev_trades = _trades(conn, schema, prev) if prev is not None else trades.clear()
    if prev_trades.is_empty():
        note = f"；缺前一交易日 {prev} 的分點，那天的隔日沖事件少算"
    else:
        ev = day_events(prev_trades, trades, day).with_columns(created_at=pl.lit(now))
        _replace(conn, schema, BROKER_DAYTRADE_EVENTS_SPEC, "resolve_date", day, ev)
    ratio, events = _params(params)
    dts = flagged(_state(conn, schema, day), ratio, events)
    net = day_net(trades, dts)
    _replace(conn, schema, MAIN_FORCE_DAILY_SPEC, "date", day, net)
    return f"主力淨買超 {net.height} 檔（排除隔日沖分點 {dts.len()} 家）{note}"


def seed(
    src: Any,
    dst: Any,
    schema: str,
    as_of: dt.date,
    sessions: int,
    params: dict[str, object],
    now: dt.datetime,
    *,
    segment_months: int = 3,
    copy_trades: int = 0,
) -> str:
    """從 src（地端全歷史）寫種子到 dst：截至 as_of 的隔日沖累計一列一分點、最近 sessions 個交易日的主力淨買超；
    copy_trades＞0 時另把最近幾個交易日的分點明細抄過去（雲端第一天要前一天的明細）。"""
    from twquant.data.serving.pg_view import PgTableSource
    from twquant.factors.broker import daytrade_events

    if now.tzinfo is None:
        raise ValueError("now 必須帶時區")
    with src.cursor() as cur:
        cur.execute(f'SELECT min(date) FROM "{schema}".broker_trades')
        start = cur.fetchone()[0]
        cur.execute(
            f'SELECT DISTINCT date FROM "{schema}".broker_trades WHERE date <= %s ORDER BY date DESC LIMIT %s',
            (as_of, max(sessions, copy_trades)),
        )
        recent = sorted(r[0] for r in cur.fetchall())
    if start is None or not recent:
        raise DataError("broker_trades", as_of, "地端沒有分點資料，種子算不出來")
    # 每段多讀前後 pad 天、只留結算日落在本段的事件：段尾那天的買超要下一段第一天才結算，
    # 不重疊就會整筆漏掉（D-203 §七 的已知限制）；種子要跟全歷史一次算的一樣。
    pad = dt.timedelta(days=14)
    parts: list[pl.DataFrame] = []
    a = start
    while a <= as_of:
        b = min(_add_months(a, segment_months) - dt.timedelta(days=1), as_of)
        hi = min(b + pad, as_of)
        view = PgTableSource(src, schema, since=a - pad, until=hi).view(hi)
        ev = daytrade_events(view).collect()
        parts.append(ev.filter(pl.col("resolve_date").is_between(a, b)))
        a = b + dt.timedelta(days=1)
    events = pl.concat(parts).rename({"_b": "n_buys", "_f": "n_flip"})
    ratio, min_events = _params(params)
    base = (
        events.filter(pl.col("resolve_date") <= as_of)
        .group_by("broker_id")
        .agg(pl.col("n_buys").sum().cast(pl.Int64), pl.col("n_flip").sum().cast(pl.Int64))
        .with_columns(resolve_date=pl.lit(as_of, dtype=pl.Date), created_at=pl.lit(now))
    )
    nets = []
    for d in recent[-sessions:]:
        state = (
            events.filter(pl.col("resolve_date") <= d)
            .group_by("broker_id")
            .agg(pl.col("n_buys").sum(), pl.col("n_flip").sum())
        )
        nets.append(day_net(_trades(src, schema, d), flagged(state, ratio, min_events)))
    ensure_tables(dst, (BROKER_DAYTRADE_EVENTS_SPEC, MAIN_FORCE_DAILY_SPEC), schema)
    with dst.cursor() as cur:
        cur.execute(f'DELETE FROM "{schema}".broker_daytrade_events')
        upsert(
            cur,
            schema,
            BROKER_DAYTRADE_EVENTS_SPEC,
            base.select(list(BROKER_DAYTRADE_EVENTS_SPEC.column_names)),
        )
        net = pl.concat(nets)
        cur.execute(
            f'DELETE FROM "{schema}".main_force_daily WHERE date >= %s AND date <= %s',
            (recent[-sessions], as_of),
        )
        upsert(
            cur, schema, MAIN_FORCE_DAILY_SPEC, net.select(list(MAIN_FORCE_DAILY_SPEC.column_names))
        )
        copied = 0
        if copy_trades:
            from twquant.data.serving.schema import spec_of

            bt = spec_of("broker_trades")
            for d in recent[-copy_trades:]:
                rows = pl.read_database(
                    f'SELECT {", ".join(bt.column_names)} FROM "{schema}".broker_trades WHERE date = %(d)s',
                    src,
                    execute_options={"params": {"d": d}},
                )
                upsert(cur, schema, bt, rows)
                copied += rows.height
    dst.commit()
    return (
        f"種子：{base.height} 家分點的隔日沖累計（截至 {as_of}，其中達門檻 {flagged(base, ratio, min_events).len()} 家）、"
        f"主力淨買超 {net.height} 列（{len(recent[-sessions:])} 個交易日）、抄分點明細 {copied} 列"
    )


def _add_months(d: dt.date, months: int) -> dt.date:
    m = d.month - 1 + months
    return dt.date(d.year + m // 12, m % 12 + 1, 1)
