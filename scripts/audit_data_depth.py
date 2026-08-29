"""T-065 資料深度審計：對還原後的 serving schema 盤點各表歷史深度與缺口。

用法（DSN 從環境變數讀，禁止命令列明文）：
    TWQUANT_PG_DSN=... python scripts/audit_data_depth.py \
        --out audit_report.md [--drive-inventory inventory.txt]

輸出 markdown 報告：各表 min/max 日期、distinct 交易日、對照交易日曆的缺日、
最長連續缺口、逐年列數/檔數、stock_meta 生存者偏誤檢查、Drive 歸檔庫存摘要。
結論（訓練可用期間）由人判讀後回填 SPEC-010 §2。
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
from pathlib import Path

import psycopg
from psycopg import sql

# 逐年細目只做給 ML 特徵會用到的表；其餘表只出總覽列（報告篇幅控制，非資料取捨）
DETAIL_TABLES = [
    "prices_daily",
    "institutional_daily",
    "margin_daily",
    "broker_trades",
    "shareholding_dispersion",
]


def _rows(conn: psycopg.Connection, query: sql.Composable, params: tuple = ()) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _date_tables(conn: psycopg.Connection, schema: str) -> list[str]:
    """serving schema 內含 date 欄位的表（動態發現，不寫死清單）。"""
    got = _rows(
        conn,
        sql.SQL(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = %s AND column_name = 'date' ORDER BY table_name"
        ),
        (schema,),
    )
    return [r[0] for r in got]


def _trading_days(conn: psycopg.Connection, schema: str, lo: dt.date, hi: dt.date) -> int | None:
    """區間內交易日數：優先 serving.trading_calendar；不存在回 None（改用平日近似）。"""
    have = _rows(
        conn,
        sql.SQL(
            "SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name='trading_calendar'"
        ),
        (schema,),
    )
    if not have:
        return None
    n = _rows(
        conn,
        sql.SQL("SELECT count(*) FROM {}.trading_calendar WHERE date BETWEEN %s AND %s").format(
            sql.Identifier(schema)
        ),
        (lo, hi),
    )
    return int(n[0][0])


def _weekdays_between(lo: dt.date, hi: dt.date) -> int:
    n = 0
    d = lo
    while d <= hi:
        if d.weekday() < 5:
            n += 1
        d += dt.timedelta(days=1)
    return n


def _table_depth(conn: psycopg.Connection, schema: str, table: str) -> dict[str, object] | None:
    t = sql.Identifier(schema, table)
    base = _rows(
        conn,
        sql.SQL("SELECT count(*), min(date), max(date), count(DISTINCT date) FROM {}").format(t),
    )[0]
    total, lo, hi, ndays = int(base[0]), base[1], base[2], int(base[3])
    if total == 0 or lo is None:
        return {"table": table, "rows": 0}
    gap = _rows(
        conn,
        sql.SQL(
            "SELECT max(date - prev) - 1 FROM ("
            "  SELECT date, lag(date) OVER (ORDER BY date) AS prev"
            "  FROM (SELECT DISTINCT date FROM {}) d) g WHERE prev IS NOT NULL"
        ).format(t),
    )[0][0]
    cal = _trading_days(conn, schema, lo, hi)
    expected = cal if cal is not None else _weekdays_between(lo, hi)
    return {
        "table": table,
        "rows": total,
        "lo": lo,
        "hi": hi,
        "ndays": ndays,
        "expected": expected,
        "cal_source": "trading_calendar" if cal is not None else "平日近似",
        "missing": max(expected - ndays, 0),
        "max_gap_calendar_days": int(gap) if gap is not None else 0,
    }


def _yearly(conn: psycopg.Connection, schema: str, table: str) -> list[tuple]:
    return _rows(
        conn,
        sql.SQL(
            "SELECT extract(year FROM date)::int, count(*), count(DISTINCT stock_id),"
            " count(DISTINCT date) FROM {} GROUP BY 1 ORDER BY 1"
        ).format(sql.Identifier(schema, table)),
    )


def _monthly_revenue(conn: psycopg.Connection, schema: str) -> dict[str, object] | None:
    have = _rows(
        conn,
        sql.SQL(
            "SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name='monthly_revenue'"
        ),
        (schema,),
    )
    if not have:
        return None
    r = _rows(
        conn,
        sql.SQL(
            "SELECT count(*), min(year_month), max(year_month),"
            " count(DISTINCT year_month), count(DISTINCT stock_id) FROM {}.monthly_revenue"
        ).format(sql.Identifier(schema)),
    )[0]
    return {"rows": int(r[0]), "lo": r[1], "hi": r[2], "n_months": int(r[3]), "n_stocks": int(r[4])}


def _survivorship(conn: psycopg.Connection, schema: str) -> dict[str, int]:
    s = sql.Identifier(schema)
    meta = _rows(
        conn,
        sql.SQL(
            "SELECT count(*), count(*) FILTER (WHERE delist_date IS NOT NULL) FROM {}.stock_meta"
        ).format(s),
    )[0]
    delisted_with_px = _rows(
        conn,
        sql.SQL(
            "SELECT count(DISTINCT m.stock_id) FROM {}.stock_meta m"
            " WHERE m.delist_date IS NOT NULL"
            " AND EXISTS (SELECT 1 FROM {}.prices_daily p WHERE p.stock_id = m.stock_id)"
        ).format(s, s),
    )[0]
    return {
        "total": int(meta[0]),
        "delisted": int(meta[1]),
        "delisted_with_prices": int(delisted_with_px[0]),
    }


def _drive_summary(inventory: Path) -> list[str]:
    lines = inventory.read_text(encoding="utf-8").splitlines()
    bases = [ln for ln in lines if ln.startswith("base/")]
    day_re = re.compile(r"^daily/(\d{4})/(\d{8})/")
    days = sorted({m.group(2) for ln in lines if (m := day_re.match(ln))})
    out = [f"- base dump：{len(bases)} 個（{', '.join(bases) or '無'}）"]
    if days:
        out.append(f"- daily 增量：{len(days)} 日，{days[0]} ~ {days[-1]}")
        expected = _weekdays_between(
            dt.datetime.strptime(days[0], "%Y%m%d").date(),
            dt.datetime.strptime(days[-1], "%Y%m%d").date(),
        )
        out.append(f"- 區間平日數 {expected}，缺 {expected - len(days)} 日（含國定假日，屬預期）")
    else:
        out.append("- daily 增量：無")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn-env", default="TWQUANT_PG_DSN", help="DSN 環境變數名")
    ap.add_argument("--schema", default="serving")
    ap.add_argument("--out", default="audit_report.md")
    ap.add_argument("--drive-inventory", default="", help="rclone lsf 輸出檔（選填）")
    args = ap.parse_args()

    dsn = os.environ.get(args.dsn_env)
    if not dsn:
        raise SystemExit(f"環境變數 {args.dsn_env} 未設定")

    md: list[str] = [
        "# T-065 資料深度審計報告",
        "",
        f"產出：{dt.datetime.now(dt.UTC).isoformat(timespec='seconds')}（審計庫＝Drive base dump 還原；"
        "factor_values/composite_daily/factor_cross_stats 可重算、還原時排除，列數 0 屬預期）",
        "",
    ]
    with psycopg.connect(dsn) as conn:
        md += [
            "## 各表深度總覽",
            "",
            "| 表 | 列數 | 起 | 迄 | 有資料日 | 期望交易日 | 缺日 | 最長缺口(曆日) |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for table in _date_tables(conn, args.schema):
            d = _table_depth(conn, args.schema, table)
            if d is None:
                continue
            if d["rows"] == 0:
                md.append(f"| {table} | 0 | — | — | — | — | — | — |")
                continue
            md.append(
                f"| {table} | {d['rows']:,} | {d['lo']} | {d['hi']} | {d['ndays']} "
                f"| {d['expected']}（{d['cal_source']}） | {d['missing']} | {d['max_gap_calendar_days']} |"
            )

        mr = _monthly_revenue(conn, args.schema)
        if mr:
            md += [
                "",
                "## monthly_revenue（月頻）",
                "",
                f"- {mr['rows']:,} 列、{mr['n_months']} 個月（{mr['lo']} ~ {mr['hi']}）、"
                f"{mr['n_stocks']} 檔",
            ]

        md += ["", "## 逐年細目（ML 特徵表）", ""]
        for table in DETAIL_TABLES:
            yearly = _yearly(conn, args.schema, table)
            if not yearly:
                continue
            md += [f"### {table}", "", "| 年 | 列數 | 檔數 | 交易日 |", "|---|---|---|---|"]
            md += [f"| {y} | {n:,} | {s} | {nd} |" for y, n, s, nd in yearly]
            md.append("")

        sv = _survivorship(conn, args.schema)
        md += [
            "## 生存者偏誤檢查（stock_meta）",
            "",
            f"- 總檔數 {sv['total']}、已下市 {sv['delisted']}、"
            f"已下市且留有價格歷史 {sv['delisted_with_prices']}",
            "- 判讀：已下市=0 或「已下市但無價格歷史」比例高 → 湖有生存者偏誤，"
            "訓練宇宙須另行處理（回填 SPEC-010 §2）",
        ]

    if args.drive_inventory:
        md += ["", "## Drive 歸檔庫存", "", *_drive_summary(Path(args.drive_inventory))]

    Path(args.out).write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
