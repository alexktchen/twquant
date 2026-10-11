"""`etf_meta`／`etf_units_daily` 寫進 PG（SPEC-016，T-335）。直寫表：資料湖沒有它們。

比照 direct_ingest.py：本模組不 import psycopg，只在呼叫端傳入的連線上操作；抓取函數也由呼叫端
注入（每日用 TWSE MI_QFIIS、回補用 FinMind），測試因此不必連網。

寫入規則：
- `etf_meta`：snapshot——整份刪掉重灌（同一交易）。
- `etf_units_daily`：只補 PG 還沒有的交易日（冪等）；差分需要的前一交易日從 PG 讀。
  每日模式整批先過完整性閘門（AC-6），任一天缺任一檔就一列都不寫。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from twquant.config import EtfMetaConfig, FundFlowConfig
from twquant.data import fund_flow_units as ffu
from twquant.data.serving import schema as sch
from twquant.data.serving.sync import upsert

UnitsFetch = Callable[[dt.date], list[dict[str, Any]]]
_RAW: dict[str, pl.DataType | type[pl.DataType]] = {
    "date": pl.Date,
    "etf_id": pl.String,
    "units": pl.Int64,
}


def _q(schema: str, table: str) -> str:
    return f'"{schema}"."{table}"'


def sessions_between(cur: Any, schema: str, start: dt.date, end: dt.date) -> list[dt.date]:
    """[start, end] 的交易日（PG prices_daily 有列的日子），舊到新。"""
    cur.execute(
        f"SELECT DISTINCT date FROM {_q(schema, 'prices_daily')} "
        "WHERE date BETWEEN %s AND %s ORDER BY date",
        (start, end),
    )
    return [r[0] for r in cur.fetchall()]


def recent_sessions(cur: Any, schema: str, day: dt.date, n: int) -> list[dt.date]:
    """day 之前（不含）最近 n 個交易日，舊到新。"""
    cur.execute(
        f"SELECT DISTINCT date FROM {_q(schema, 'prices_daily')} "
        "WHERE date < %s ORDER BY date DESC LIMIT %s",
        (day, n),
    )
    return sorted(r[0] for r in cur.fetchall())


def split_events(cur: Any, schema: str, event_types: Sequence[str]) -> list[dict[str, Any]]:
    """corporate_events 裡指定型別（分割／反分割）的 (stock_id, date)。"""
    cur.execute(
        f"SELECT stock_id, date FROM {_q(schema, 'corporate_events')} "
        "WHERE event_type = ANY(%s) ORDER BY date, stock_id",
        (list(event_types),),
    )
    return [{"stock_id": r[0], "date": r[1]} for r in cur.fetchall()]


def units_on(cur: Any, schema: str, days: Sequence[dt.date]) -> pl.DataFrame:
    """PG 裡指定交易日的單位數（差分要用的前一交易日）。表還沒建 → 空表（唯讀檢查不建表）。"""
    cur.execute("SELECT to_regclass(%s)", (f"{schema}.etf_units_daily",))
    if cur.fetchone()[0] is None:
        return pl.DataFrame(schema=_RAW)
    cur.execute(
        f"SELECT date, etf_id, units FROM {_q(schema, 'etf_units_daily')} "
        "WHERE date = ANY(%s::date[])",
        (list(days),),
    )
    return pl.DataFrame(cur.fetchall(), schema=_RAW, orient="row")


def present_dates(cur: Any, schema: str, days: Sequence[dt.date]) -> set[dt.date]:
    """這些交易日裡 PG 已有任一列的日子。"""
    cur.execute(
        f"SELECT DISTINCT date FROM {_q(schema, 'etf_units_daily')} WHERE date = ANY(%s::date[])",
        (list(days),),
    )
    return {r[0] for r in cur.fetchall()}


def write_meta(conn: Any, schema: str, meta: EtfMetaConfig) -> int:
    """configs/etf_meta.yaml → PG `etf_meta`（整份重灌）。回傳列數。"""
    df = ffu.etf_meta_frame(meta)
    sch.ensure_tables(conn, sch.FUND_FLOW_SPECS, schema)
    try:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {_q(schema, 'etf_meta')}")
            upsert(cur, schema, sch.spec_of("etf_meta"), df)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return df.height


def write_units(conn: Any, schema: str, df: pl.DataFrame) -> None:
    """upsert `etf_units_daily`（一個交易；失敗 rollback 後照原樣 raise）。"""
    sch.ensure_tables(conn, sch.FUND_FLOW_SPECS, schema)
    try:
        with conn.cursor() as cur:
            upsert(cur, schema, sch.spec_of("etf_units_daily"), df)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


@dataclass
class UnitsResult:
    """一次擷取的結果（呼叫端印出）。"""

    targets: list[dt.date]
    fetched: list[dt.date]
    rows: int = 0
    jumps: list[tuple[Any, ...]] = field(default_factory=list)


def build_units(
    cur: Any,
    schema: str,
    cfg: FundFlowConfig,
    meta: EtfMetaConfig,
    fetched: pl.DataFrame,
    targets: Sequence[dt.date],
) -> pl.DataFrame:
    """fetched（targets 當天的 [date, etf_id, units]）＋ PG 前一交易日 → `etf_units_daily` 列。

    只留 configs/etf_meta.yaml 列到且 units_tracked 的 ETF（TWSE ETF 類含債券、槓桿、國外成分，不在研究範圍）。
    """
    ids = [e.etf_id for e in meta.etfs if e.units_tracked]
    first = min(targets)
    lookback = recent_sessions(cur, schema, first, 1)
    cal = sessions_between(cur, schema, lookback[0] if lookback else first, max(targets))
    prior_days = [d for d in cal if d < first]
    prior = units_on(cur, schema, prior_days) if prior_days else pl.DataFrame(schema=_RAW)
    raw = pl.concat([prior, fetched.select(list(_RAW)).cast(pl.Schema(_RAW))]).filter(
        pl.col("etf_id").is_in(ids)
    )
    events = split_events(cur, schema, cfg.units.split_event_types)
    null_days = ffu.split_null_days(events, cal, cfg.units.split_null_sessions_before)
    return ffu.units_frame(raw, cal, targets, null_days, cfg.units.available_hour)


def ingest_recent_units(
    conn: Any,
    schema: str,
    cfg: FundFlowConfig,
    meta: EtfMetaConfig,
    day: dt.date,
    fetch: UnitsFetch,
) -> UnitsResult:
    """每日模式：day 之前最近 `catchup_trading_days` 個交易日裡 PG 還沒有的那幾天 → 抓、過完整性閘門、寫。

    當天不抓：單位數同日 21:00 才公布（與外資持股統計同表），19:00 的排程抓不到。
    """
    sch.ensure_tables(conn, sch.FUND_FLOW_SPECS, schema)
    with conn.cursor() as cur:
        window = recent_sessions(cur, schema, day, cfg.units.catchup_trading_days)
        have = present_dates(cur, schema, window)
    targets = [d for d in window if d not in have]
    result = UnitsResult(targets=window, fetched=targets)
    if not targets:
        return result
    fetched = pl.DataFrame([r for d in targets for r in fetch(d)], schema=_RAW, orient="row")
    with conn.cursor() as cur:
        df = build_units(cur, schema, cfg, meta, fetched, targets)
    ffu.require_complete(df, meta, targets)
    write_units(conn, schema, df)
    result.rows = df.height
    result.jumps = ffu.units_jumps(df, cfg.units.jump_report_ratio).head(10).rows()
    return result
