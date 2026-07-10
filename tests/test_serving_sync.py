"""T-031 / SPEC-009 AC-2：sync 增量 + 冪等（需真 PG，無則 skip）。

以 tmp_path 假 clean 湖驗證 watermark 只追新日期、重跑不改列數。用獨立 test schema，
跑完 DROP。psycopg 與 serving.sync 皆延後 import（sync→store→psycopg），故無 PG 的 CI
在 collection 階段不會 ImportError。
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from twquant.config import ServingConfig
from twquant.data.warehouse import Warehouse

_SCHEMA = "serving_test_sync"


def _inst(day: dt.date, sids: list[str]) -> pl.DataFrame:
    n = len(sids)
    ts = pl.Series([dt.datetime(day.year, day.month, day.day, 18, 0)] * n).dt.replace_time_zone(
        "Asia/Taipei"
    )
    return pl.DataFrame(
        {
            "date": pl.Series([day] * n, dtype=pl.Date),
            "stock_id": pl.Series(sids, dtype=pl.String),
            "foreign_net": pl.Series(list(range(1000, 1000 + n)), dtype=pl.Int64),
            "trust_net": pl.Series([-50] * n, dtype=pl.Int64),
            "dealer_net": pl.Series([10] * n, dtype=pl.Int64),
            "available_at": ts,
        }
    )


def _write(root: Path, table: str, day: dt.date, df: pl.DataFrame) -> None:
    part = root / "clean" / table / f"date={day.isoformat()}"
    part.mkdir(parents=True, exist_ok=True)
    df.write_parquet(part / "part.parquet")


def _connect_or_skip() -> Any:
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        pytest.skip("TWQUANT_PG_DSN 未設，跳過 PG 整合測試")
    try:
        conn = psycopg.connect(dsn)
    except psycopg.Error as exc:  # pragma: no cover - 依環境而定
        pytest.skip(f"PG 不可達，跳過：{exc}")
    conn.autocommit = True  # 驗證連線不長留交易鎖，免與 sync 的 TRUNCATE 互卡
    return conn


def _drop(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
    conn.commit()


def _count(conn: Any, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f'SELECT count(*) FROM "{_SCHEMA}"."{table}"')
        return int(cur.fetchone()[0])


def test_ac2_incremental_and_idempotent(tmp_path: Path) -> None:
    conn = _connect_or_skip()
    from twquant.data.serving import sync as srv

    cfg = ServingConfig(pg_schema=_SCHEMA)
    wh = Warehouse(tmp_path)
    d1, d2, d3 = dt.date(2026, 6, 1), dt.date(2026, 6, 2), dt.date(2026, 6, 3)
    try:
        _drop(conn)
        _write(tmp_path, "institutional_daily", d1, _inst(d1, ["2330", "2317"]))
        _write(tmp_path, "institutional_daily", d2, _inst(d2, ["2330", "2317"]))

        res = {r.table: r for r in srv.sync_all(wh, cfg)}["institutional_daily"]
        assert res.synced_dates == 2
        assert res.last_date == d2
        assert res.row_count == 4
        assert _count(conn, "institutional_daily") == 4

        _write(tmp_path, "institutional_daily", d3, _inst(d3, ["2330", "2317"]))
        res = {r.table: r for r in srv.sync_all(wh, cfg)}["institutional_daily"]
        assert res.synced_dates == 1  # watermark 只追新日期 d3
        assert res.last_date == d3
        assert res.row_count == 6

        res = {r.table: r for r in srv.sync_all(wh, cfg)}["institutional_daily"]
        assert res.synced_dates == 0  # 無新分區
        assert res.row_count == 6  # 冪等：重跑不改列數
        assert _count(conn, "institutional_daily") == 6

        st = {row["table"]: row for row in srv.status(cfg)}["institutional_daily"]
        assert st["last_date"] == d3
        assert st["row_count"] == 6
    finally:
        _drop(conn)
        conn.close()


def test_rebuild_reloads(tmp_path: Path) -> None:
    conn = _connect_or_skip()
    from twquant.data.serving import sync as srv

    cfg = ServingConfig(pg_schema=_SCHEMA)
    wh = Warehouse(tmp_path)
    d1 = dt.date(2026, 6, 1)
    try:
        _drop(conn)
        _write(tmp_path, "institutional_daily", d1, _inst(d1, ["2330", "2317"]))
        srv.sync_all(wh, cfg)
        assert _count(conn, "institutional_daily") == 2

        res = {r.table: r for r in srv.rebuild(wh, cfg, ["institutional_daily"])}[
            "institutional_daily"
        ]
        assert res.synced_dates == 1  # truncate + 重置後全量重灌 d1
        assert res.row_count == 2
    finally:
        _drop(conn)
        conn.close()


def test_sync_catches_backfilled_older_date(tmp_path: Path) -> None:
    """sync 補齊資料湖裡所有未進 PG 的分區——含晚於初次同步才 backfill 的『舊』日期
    （舊 watermark 邏輯會漏；這是換成比對 PG 已有日期的關鍵行為）。"""
    conn = _connect_or_skip()
    from twquant.data.serving import sync as srv

    cfg = ServingConfig(pg_schema=_SCHEMA)
    wh = Warehouse(tmp_path)
    d1, d2, d3 = dt.date(2026, 6, 1), dt.date(2026, 6, 2), dt.date(2026, 6, 3)
    try:
        _drop(conn)
        _write(tmp_path, "institutional_daily", d1, _inst(d1, ["2330"]))
        _write(tmp_path, "institutional_daily", d3, _inst(d3, ["2330"]))
        srv.sync_all(wh, cfg)  # 先同步 d1、d3（watermark = d3）
        assert _count(conn, "institutional_daily") == 2

        _write(tmp_path, "institutional_daily", d2, _inst(d2, ["2330"]))  # backfill 舊日期 d2 < d3
        res = {r.table: r for r in srv.sync_all(wh, cfg)}["institutional_daily"]
        assert res.synced_dates == 1  # d2 被補上（舊邏輯會因 d2 < watermark 漏掉）
        assert _count(conn, "institutional_daily") == 3
    finally:
        _drop(conn)
        conn.close()


def test_sync_catches_new_source_file_in_existing_date(tmp_path: Path) -> None:
    """日期已在 PG、但事後同一分區補了新來源檔（如 TWSE 晚於 TPEX）→ sync 以檔案 mtime
    偵測並重同步該日，新來源的列被補進 PG。"""
    conn = _connect_or_skip()
    from twquant.data.serving import sync as srv

    cfg = ServingConfig(pg_schema=_SCHEMA)
    wh = Warehouse(tmp_path)
    d1 = dt.date(2026, 6, 1)
    part = tmp_path / "clean" / "institutional_daily" / f"date={d1.isoformat()}"
    try:
        _drop(conn)
        part.mkdir(parents=True)
        _inst(d1, ["2330"]).write_parquet(part / "part-tpex.parquet")
        srv.sync_all(wh, cfg)
        assert _count(conn, "institutional_daily") == 1

        _inst(d1, ["9999"]).write_parquet(part / "part-twse.parquet")  # 同日補第二來源
        res = {r.table: r for r in srv.sync_all(wh, cfg)}["institutional_daily"]
        assert res.synced_dates == 1  # mtime 偵測到該日有新檔 → 重同步
        assert _count(conn, "institutional_daily") == 2  # 9999 的列進來了
    finally:
        _drop(conn)
        conn.close()
