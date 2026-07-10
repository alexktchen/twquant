"""T-031 / SPEC-009 AC-1：serving schema DDL 冪等。

需要真 PG（TWQUANT_PG_DSN + 可連線）；無則 skip，故無 PG 的 CI 仍綠（AC-8）。
用獨立 test schema，跑完 DROP，不碰正式 serving schema。
"""

from __future__ import annotations

import os
from typing import Any

import pytest

_MIRROR = {
    "prices_daily",
    "institutional_daily",
    "margin_daily",
    "monthly_revenue",
    "shareholding_dispersion",
    "stock_meta",
}
_ALL = _MIRROR | {"sync_state"}


def _conn_or_skip() -> Any:
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        pytest.skip("TWQUANT_PG_DSN 未設，跳過 PG 整合測試")
    try:
        return psycopg.connect(dsn)
    except psycopg.Error as exc:  # pragma: no cover - 依環境而定
        pytest.skip(f"PG 不可達，跳過：{exc}")


def test_ac1_ddl_idempotent() -> None:
    from twquant.data.serving import schema as sch

    conn = _conn_or_skip()
    test_schema = "serving_test_ac1"
    try:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{test_schema}" CASCADE')
        conn.commit()

        sch.ensure_schema(conn, test_schema)
        sch.ensure_schema(conn, test_schema)  # 第二次不得報錯（冪等）

        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
                (test_schema,),
            )
            got = {row[0] for row in cur.fetchall()}
        assert got >= _ALL, f"缺表：{_ALL - got}"
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{test_schema}" CASCADE')
        conn.commit()
        conn.close()


def test_ddl_rejects_bad_schema_ident() -> None:
    from twquant.data.serving import schema as sch

    with pytest.raises(ValueError, match="識別字"):
        sch.table_ddls("bad-schema; DROP")
