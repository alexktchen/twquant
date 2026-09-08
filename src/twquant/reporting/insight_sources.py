"""供應層讀取：把 PG `serving` 的列取成證據包建構器要的形狀（T-219）。

只讀不寫。每個查詢都帶 `available_at <= as_of` 的 PIT 條件——證據包建構器會再檢查一次，
兩層都擋是刻意的：這裡漏掉條件，那裡仍會把列排除並記成缺口。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from typing import Any

from twquant.conventions import TZ

# 各區段的回看窗（交易日／自然日）。放在這裡是查詢參數，不是研究參數：
# 它只決定「給模型看多久的資料」，不參與任何訊號或門檻計算。
_REVENUE_MONTHS = 6
_CHIP_DAYS = 10
_EVENT_DAYS = 90


def _taipei(value: Any) -> Any:
    """PG 的 timestamptz 回來帶 UTC 偏移；同一瞬間，但合約要求 Asia/Taipei 表示法。"""
    if isinstance(value, dt.datetime) and value.tzinfo is not None:
        return value.astimezone(TZ)
    return value


def _clean_valuation(row: dict[str, Any]) -> dict[str, Any] | None:
    """來源把缺值編成 0（FinMind 的已知行為）；「本益比 0 倍」不是事實，一律當缺值。"""
    cleaned = dict(row)
    for field in ("per", "pbr", "dividend_yield"):
        if cleaned.get(field) in (0, 0.0):
            cleaned[field] = None
    if all(cleaned.get(field) is None for field in ("per", "pbr", "dividend_yield")):
        return None
    return cleaned


def _rows(conn: Any, schema: str, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    """Run one read-only query and return dict rows with Taipei-normalised timestamps."""
    with conn.cursor() as cur:
        cur.execute(sql.format(schema=schema), params)
        columns = [description[0] for description in cur.description]
        return [
            {name: _taipei(value) for name, value in zip(columns, row, strict=True)}
            for row in cur.fetchall()
        ]


def fetch_sections(
    conn: Any,
    schema: str,
    stock_id: str,
    as_of: dt.datetime,
    industry_row: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read one stock's point-in-time rows for every evidence section."""
    day = as_of.date()
    revenue = _rows(
        conn,
        schema,
        "SELECT date, year_month, revenue, yoy, available_at, available_at_estimated "
        "FROM {schema}.monthly_revenue WHERE stock_id = %s AND available_at <= %s "
        "ORDER BY date DESC LIMIT %s",
        (stock_id, as_of, _REVENUE_MONTHS),
    )
    valuation = _rows(
        conn,
        schema,
        "SELECT date, per, pbr, dividend_yield, available_at FROM {schema}.stock_valuation "
        "WHERE stock_id = %s AND available_at <= %s ORDER BY date DESC LIMIT 1",
        (stock_id, as_of),
    )
    institutional = _rows(
        conn,
        schema,
        "SELECT date, foreign_net, trust_net, dealer_net, available_at "
        "FROM {schema}.institutional_daily WHERE stock_id = %s AND available_at <= %s "
        "ORDER BY date DESC LIMIT %s",
        (stock_id, as_of, _CHIP_DAYS),
    )
    margin = _rows(
        conn,
        schema,
        "SELECT date, margin_balance, short_balance, available_at FROM {schema}.margin_daily "
        "WHERE stock_id = %s AND available_at <= %s ORDER BY date DESC LIMIT 1",
        (stock_id, as_of),
    )
    events = _rows(
        conn,
        schema,
        "SELECT date, event_type, available_at FROM {schema}.corporate_events "
        "WHERE stock_id = %s AND available_at <= %s AND date >= %s ORDER BY date DESC LIMIT 5",
        (stock_id, as_of, day - dt.timedelta(days=_EVENT_DAYS)),
    )
    return {
        # ML 區段由呼叫端以「這一次推薦 run 自己的分數」填入：`ml_scores` 混有多個模型的
        # 回補列，取最新一列會拿到別的模型、甚至半年前的分數（2026-09-06 實跑時抓到）。
        "ml": None,
        "monthly_revenue": revenue,
        "stock_valuation": _clean_valuation(valuation[0]) if valuation else None,
        "institutional": institutional,
        "margin": margin,
        "corporate_events": events,
        # 處置股票目前只在資料湖，供應層尚無此表；缺席會由建構器記成事件區段的缺口。
        "disposition": [],
        "industry": industry_row,
    }


def fetch_industry_rows(
    conn: Any, schema: str, as_of: dt.datetime, window: int
) -> dict[str, dict[str, Any]]:
    """Aggregate institutional flow per industry once per run, keyed by industry name.

    This is the same shape the sector-flows endpoint reports, computed here from the serving
    tables so one query serves every candidate in the run.
    """
    rows = _rows(
        conn,
        schema,
        """
        WITH window_days AS (
            SELECT DISTINCT date FROM {schema}.prices_daily
            WHERE date <= %s ORDER BY date DESC LIMIT %s
        ),
        flows AS (
            SELECT m.industry,
                   SUM((i.foreign_net + i.trust_net + i.dealer_net) * p.close) AS net,
                   COUNT(DISTINCT i.stock_id) AS n
            FROM {schema}.institutional_daily i
            JOIN {schema}.prices_daily p ON p.stock_id = i.stock_id AND p.date = i.date
            JOIN {schema}.stock_meta m ON m.stock_id = i.stock_id
            WHERE i.date IN (SELECT date FROM window_days)
              AND i.available_at <= %s AND m.industry IS NOT NULL
            GROUP BY m.industry
        )
        SELECT industry, net, n FROM flows
        """,
        (as_of.date(), window, as_of),
    )
    return {
        str(row["industry"]): {
            "industry": row["industry"],
            "net_yi": float(row["net"] or 0.0) / 1e8,
            # 這個查詢不算族群報酬。留 None 讓證據不提它——填 0.0 會變成
            # 「報酬 0.00%」這句假話寫進證據裡（2026-09-06 實跑時抓到）。
            "ret_pct": None,
            "n": int(row["n"] or 0),
            "window": window,
            "as_of": as_of.date(),
        }
        for row in rows
    }


def fetch_industry_of(conn: Any, schema: str, stock_id: str) -> str | None:
    """The stock's current industry label; stock_meta keeps no history, so this is not PIT."""
    rows = _rows(
        conn, schema, "SELECT industry FROM {schema}.stock_meta WHERE stock_id = %s", (stock_id,)
    )
    return str(rows[0]["industry"]) if rows and rows[0]["industry"] else None
