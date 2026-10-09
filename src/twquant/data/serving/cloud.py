"""雲端供應層（T-055，D-030）：Supabase 滾動窗口的 seed / prune / broker_flow 聚合。

只在 CLI / GHA 邊界呼叫；連線由呼叫端提供（沿 sync.py 慣例，本模組不 import store）。
窗口單位＝交易日數（該表 distinct date 的第 N 新者為 cutoff，DELETE date < cutoff，冪等）。
Supabase transaction pooler（:6543）不支援 prepared statements——呼叫端建連線時須帶
`prepare_threshold=None`（psycopg），見 store.connect_cloud。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from twquant.data.serving.schema import ensure_schema, spec_of


@dataclass(frozen=True)
class PruneResult:
    """單表修剪結果。"""

    table: str
    deleted: int
    cutoff: dt.date | None  # None＝現有日數未超窗、未刪


@dataclass(frozen=True)
class SeedResult:
    """單表 seed 結果。"""

    table: str
    rows: int


def _nth_latest_date(cur: Any, schema: str, table: str, n: int) -> dt.date | None:
    """該表第 n 新的 distinct date；日數不足 n 回 None。"""
    cur.execute(
        f'SELECT DISTINCT date FROM "{schema}"."{table}" ORDER BY date DESC LIMIT 1 OFFSET %s',
        (n - 1,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def prune(
    conn: Any,
    schema: str,
    windows: dict[str, int],
    stock_pattern: str | None = None,
    pattern_tables: tuple[str, ...] = (),
    industry_member_tables: tuple[str, ...] = (),
) -> list[PruneResult]:
    """把 windows 各表修剪到最新 N 個交易日（＋pattern_tables 剔除非白名單股票）。冪等。

    industry_member_tables：這些表剔除非白名單股票時，**保留 stock_meta 有產業別的**。
    特別股（2887G 等）不是四碼，卻會進產業平均；刪掉它們，產業相對因子
    （residual_reversal_5d、industry_rs_20d、industry_mom_6m）就和地端不同（2026-10-09 實測各約 241 檔）。
    """
    results: list[PruneResult] = []
    with conn.cursor() as cur:
        for table, n in windows.items():
            spec_of(table)  # 未知表名 raise（黃金規則 5，不靜默跳過）
            if n < 1:
                raise ValueError(f"窗口必須 ≥ 1 交易日：{table}={n}")
            cutoff = _nth_latest_date(cur, schema, table, n)
            deleted = 0
            if cutoff is not None:
                cur.execute(f'DELETE FROM "{schema}"."{table}" WHERE date < %s', (cutoff,))
                deleted = cur.rowcount
            if stock_pattern and table in pattern_tables:
                keep = (
                    f' AND stock_id NOT IN (SELECT stock_id FROM "{schema}".stock_meta '
                    "WHERE coalesce(industry, '') <> '')"
                    if table in industry_member_tables
                    else ""
                )
                cur.execute(
                    f'DELETE FROM "{schema}"."{table}" WHERE stock_id !~ %s{keep}', (stock_pattern,)
                )
                deleted += cur.rowcount
            results.append(PruneResult(table, deleted, cutoff))
    conn.commit()
    return results


def refresh_broker_flow(conn: Any, schema: str, days: list[dt.date]) -> int:
    """從 broker_trades 聚合指定日的 (date, stock) 買賣超 → broker_flow_daily（upsert）。

    回傳寫入/更新的列數。須在 broker 明細修剪**之前**執行（雲端 pipeline 順序）。
    """
    if not days:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            f'INSERT INTO "{schema}".broker_flow_daily '
            "(date, stock_id, buy_shares, sell_shares, net_shares, n_brokers, available_at) "
            "SELECT date, stock_id, sum(buy_shares), sum(sell_shares), "
            "sum(buy_shares - sell_shares), count(*), max(available_at) "
            f'FROM "{schema}".broker_trades WHERE date = ANY(%s) GROUP BY date, stock_id '
            "ON CONFLICT (stock_id, date) DO UPDATE SET "
            "buy_shares = EXCLUDED.buy_shares, sell_shares = EXCLUDED.sell_shares, "
            "net_shares = EXCLUDED.net_shares, n_brokers = EXCLUDED.n_brokers, "
            "available_at = EXCLUDED.available_at",
            (days,),
        )
        n = int(cur.rowcount)
    conn.commit()
    return n


def _seed_where(
    cur: Any,
    src_schema: str,
    table: str,
    windows: dict[str, int],
    stock_pattern: str | None,
    pattern_tables: tuple[str, ...],
) -> str:
    """組單表 seed 的 WHERE 子句（窗口 cutoff 以**來源**表的交易日算）。"""
    conds: list[str] = []
    if (n := windows.get(table)) is not None:
        cutoff = _nth_latest_date(cur, src_schema, table, n)
        if cutoff is not None:
            conds.append(f"date >= '{cutoff.isoformat()}'")
    if stock_pattern and table in pattern_tables:
        # pattern 來自受信配置；仍以引號包夾避免語法意外
        conds.append(f"stock_id ~ '{stock_pattern}'")
    return f" WHERE {' AND '.join(conds)}" if conds else ""


def seed_tables(
    src_conn: Any,
    dst_conn: Any,
    *,
    src_schema: str,
    dst_schema: str,
    tables: list[str],
    windows: dict[str, int],
    stock_pattern: str | None = None,
    pattern_tables: tuple[str, ...] = (),
) -> list[SeedResult]:
    """來源 PG →目標 PG 整表串流搬運（COPY text），窗口與股票過濾在來源端套用。

    目標表先 TRUNCATE 再灌（重跑冪等＝重灌）；目標 schema 不存在則建。
    """
    ensure_schema(dst_conn, dst_schema)
    results: list[SeedResult] = []
    for table in tables:
        spec = spec_of(table)
        cols = ", ".join(spec.column_names)
        with src_conn.cursor() as scur, dst_conn.cursor() as dcur:
            where = (
                ""
                if spec.snapshot
                else _seed_where(scur, src_schema, table, windows, stock_pattern, pattern_tables)
            )
            dcur.execute(f'TRUNCATE "{dst_schema}"."{table}"')
            with (
                scur.copy(
                    f'COPY (SELECT {cols} FROM "{src_schema}"."{table}"{where}) TO STDOUT'
                ) as out,
                dcur.copy(f'COPY "{dst_schema}"."{table}" ({cols}) FROM STDIN') as into,
            ):
                for data in out:
                    into.write(bytes(data))
            dcur.execute(f'SELECT count(*) FROM "{dst_schema}"."{table}"')
            row = dcur.fetchone()
            results.append(SeedResult(table, int(row[0]) if row else 0))
        dst_conn.commit()
    return results
