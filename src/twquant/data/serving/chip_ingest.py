"""富邦DJ 分點 PG 直寫（D-025）：逐檔 upsert broker_trades ＋ fubon_fetch_log 抓取帳本。

多程序併跑的協調核心：worker 抓完一檔，在**同一交易**內 upsert 資料列＋寫入 log——
log 列存在 ⇔ 該 (date, stock_id) 的資料已 commit，故「查 log 即跳過」永不漏抓；
競態最壞情況是兩程序重抓同一檔（upsert 冪等、內容相同，無害）。SENTINEL 列標記
整日非交易（假日探測 D-024 的持久化）。日終 `read_day` 讀回該日完整資料（含他程序
貢獻）供補寫資料湖分區——湖仍是因子層（PitView）與 `serving rebuild` 的真相源。

比照 schema.py：本模組不 import psycopg，只在呼叫端傳入的 cursor 上操作。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import polars as pl

from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.serving import schema as sch

SENTINEL = "_day_empty"  # fubon_fetch_log.stock_id 的整日非交易標記列

_SPEC = sch.spec_of("broker_trades")
_LOG = sch.spec_of("fubon_fetch_log")


def logged(cur: Any, schema: str, day: dt.date) -> dict[str, int]:
    """該日已記錄的 {stock_id: n_rows}（含 SENTINEL 列，呼叫端自行判讀）。"""
    cur.execute(f'SELECT stock_id, n_rows FROM "{schema}"."{_LOG.name}" WHERE date = %s', (day,))
    return {r[0]: int(r[1]) for r in cur.fetchall()}


def is_logged(cur: Any, schema: str, day: dt.date, stock_id: str) -> bool:
    """該 (日, 股) 是否已有抓取紀錄（供抓取當下的逐檔跳過檢查）。"""
    cur.execute(
        f'SELECT 1 FROM "{schema}"."{_LOG.name}" WHERE date = %s AND stock_id = %s',
        (day, stock_id),
    )
    return cur.fetchone() is not None


def mark(cur: Any, schema: str, day: dt.date, stock_id: str, n_rows: int) -> None:
    """記錄一筆抓取（含空結果 n_rows=0）。已存在保留首錄（DO NOTHING，競態安全）。"""
    cur.execute(
        f'INSERT INTO "{schema}"."{_LOG.name}" (date, stock_id, n_rows, fetched_at) '
        f"VALUES (%s, %s, %s, now()) ON CONFLICT (date, stock_id) DO NOTHING",
        (day, stock_id, n_rows),
    )


def clear_sentinel(cur: Any, schema: str, day: dt.date) -> None:
    """移除該日的非交易標記（該日事後發現有資料時呼叫，避免永久誤跳）。"""
    cur.execute(
        f'DELETE FROM "{schema}"."{_LOG.name}" WHERE date = %s AND stock_id = %s',
        (day, SENTINEL),
    )


def upsert_broker_rows(cur: Any, schema: str, df: pl.DataFrame) -> None:
    """單檔 canonical broker_trades 列 upsert（executemany；PK 撞列以新值覆蓋）。"""
    cols = _SPEC.column_names
    collist = ", ".join(f'"{c}"' for c in cols)
    ph = ", ".join(["%s"] * len(cols))
    pklist = ", ".join(f'"{c}"' for c in _SPEC.pk)
    setclause = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in cols if c not in _SPEC.pk)
    cur.executemany(
        f'INSERT INTO "{schema}"."{_SPEC.name}" ({collist}) VALUES ({ph}) '
        f"ON CONFLICT ({pklist}) DO UPDATE SET {setclause}",
        list(df.select(cols).iter_rows()),
    )


def read_day(cur: Any, schema: str, day: dt.date) -> pl.DataFrame:
    """自 PG 讀回某日完整 broker_trades（含他程序寫入）→ canonical df（供補寫資料湖）。

    無資料回空 df。available_at 依連線時區回傳 aware datetime → 統一轉台北再驗證
    （schemas.validate 把關 dtype，錯就 raise 不靜默）。
    """
    cols = _SPEC.column_names
    cur.execute(
        f'SELECT {", ".join(cols)} FROM "{schema}"."{_SPEC.name}" '
        f"WHERE date = %s ORDER BY stock_id, broker_id",
        (day,),
    )
    rows = cur.fetchall()
    if not rows:
        return pl.DataFrame()
    df = pl.DataFrame(
        {
            "date": pl.Series([r[0] for r in rows], dtype=pl.Date),
            "stock_id": pl.Series([r[1] for r in rows], dtype=pl.String),
            "broker_id": pl.Series([r[2] for r in rows], dtype=pl.String),
            "buy_shares": pl.Series([r[3] for r in rows], dtype=pl.Int64),
            "sell_shares": pl.Series([r[4] for r in rows], dtype=pl.Int64),
            "avg_price": pl.Series([r[5] for r in rows], dtype=pl.Float64),
            "available_at": pl.Series([r[6] for r in rows]),  # aware → 下行轉台北
        }
    ).with_columns(pl.col("available_at").dt.convert_time_zone(TZ.key).cast(schemas.TW_DT))
    return schemas.validate("broker_trades", df)


def day_stats(cur: Any, schema: str) -> list[tuple[dt.date, int, int, bool]]:
    """log 依日彙總：(date, 已抓檔數[不含 SENTINEL], 有資料檔數, 是否標記非交易日)。"""
    cur.execute(
        f"SELECT date, "
        f"count(*) FILTER (WHERE stock_id <> %s) AS fetched, "
        f"count(*) FILTER (WHERE n_rows > 0) AS with_data, "
        f"bool_or(stock_id = %s) AS holiday "
        f'FROM "{schema}"."{_LOG.name}" GROUP BY date ORDER BY date',
        (SENTINEL, SENTINEL),
    )
    return [(r[0], int(r[1]), int(r[2]), bool(r[3])) for r in cur.fetchall()]
