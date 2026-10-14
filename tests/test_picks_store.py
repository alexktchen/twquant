"""T-332：每日名單 daily_picks 的寫入（PG 整合測試，無 DSN 則 skip）。"""

from __future__ import annotations

import datetime as dt
import os
from typing import Any

import pytest

from twquant.conventions import TZ

_SCHEMA = "serving_test_daily_picks"
_DAY = dt.date(2026, 10, 8)
_AT = dt.datetime(2026, 10, 8, 20, 30, tzinfo=TZ)


def _connect_or_skip() -> Any:
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        pytest.skip("TWQUANT_PG_DSN 未設，跳過 PG 整合測試")
    try:
        conn = psycopg.connect(dsn)
    except psycopg.Error as exc:  # pragma: no cover - 依環境而定
        pytest.skip(f"PG 不可達，跳過：{exc}")
    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
        cur.execute(f'CREATE SCHEMA "{_SCHEMA}"')
    conn.commit()
    return conn


def _drop(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
    conn.commit()
    conn.close()


def test_t332_ac4_replace_line_is_idempotent_and_keeps_rank_order() -> None:
    conn = _connect_or_skip()
    from twquant.data.serving.picks_store import read_line, replace_line

    try:
        first = [("2330", 0.31), ("2317", 0.27), ("2454", None)]
        assert replace_line(conn, _SCHEMA, _DAY, "QUIET", first, "m1", _AT) == 3
        assert read_line(conn, _SCHEMA, _DAY, "QUIET") == [
            (1, "2330", 0.31),
            (2, "2317", 0.27),
            (3, "2454", None),
        ]
        # 重跑同一天＝整份覆蓋，不留舊名單的殘列
        replace_line(conn, _SCHEMA, _DAY, "QUIET", [("2454", 0.4)], "m1", _AT)
        assert read_line(conn, _SCHEMA, _DAY, "QUIET") == [(1, "2454", 0.4)]
        # 另一條線、另一天互不影響
        replace_line(conn, _SCHEMA, _DAY, "CORE", [("2330", None)], None, _AT)
        replace_line(
            conn, _SCHEMA, _DAY + dt.timedelta(days=1), "QUIET", [("1101", 0.1)], "m1", _AT
        )
        assert read_line(conn, _SCHEMA, _DAY, "QUIET") == [(1, "2454", 0.4)]
        assert read_line(conn, _SCHEMA, _DAY, "CORE") == [(1, "2330", None)]
    finally:
        _drop(conn)


def test_d176_rerun_with_same_list_keeps_the_publish_time() -> None:
    """名單績效用 created_at 分即時／事後補：重跑得到一樣的名單，不能把公布時間改成重跑的時間。"""
    conn = _connect_or_skip()
    from twquant.data.serving.picks_store import read_line, replace_line

    def written() -> list[dt.datetime]:
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT created_at FROM "{_SCHEMA}".daily_picks WHERE date = %s AND line = %s '
                "ORDER BY rank",
                (_DAY, "QUIET"),
            )
            return [r[0] for r in cur.fetchall()]

    try:
        picks = [("2330", 0.31), ("2317", 0.27)]
        replace_line(conn, _SCHEMA, _DAY, "QUIET", picks, "m1", _AT)
        later = _AT + dt.timedelta(days=1, hours=5)  # 隔天下午手動重跑
        # 代號與名次相同（分數有一點點不同）：不動，公布時間與分數都留第一次的
        assert (
            replace_line(
                conn, _SCHEMA, _DAY, "QUIET", [("2330", 0.32), ("2317", 0.26)], "m1", later
            )
            == 2
        )
        assert written() == [_AT, _AT]
        assert read_line(conn, _SCHEMA, _DAY, "QUIET") == [(1, "2330", 0.31), (2, "2317", 0.27)]
        # 名次換了＝名單不同：整份覆蓋，記新的寫入時間（那天之後就算事後補）
        replace_line(conn, _SCHEMA, _DAY, "QUIET", [("2317", 0.3), ("2330", 0.2)], "m1", later)
        assert written() == [later, later]
        assert read_line(conn, _SCHEMA, _DAY, "QUIET") == [(1, "2317", 0.3), (2, "2330", 0.2)]
    finally:
        _drop(conn)


def test_t332_ac4_rejects_empty_duplicate_and_naive_time() -> None:
    conn = _connect_or_skip()
    from twquant.data.serving.picks_store import replace_line

    try:
        with pytest.raises(ValueError, match="空"):
            replace_line(conn, _SCHEMA, _DAY, "CORE", [], None, _AT)
        with pytest.raises(ValueError, match="重複"):
            replace_line(conn, _SCHEMA, _DAY, "CORE", [("2330", None), ("2330", None)], None, _AT)
        with pytest.raises(ValueError, match="時區"):
            replace_line(
                conn, _SCHEMA, _DAY, "CORE", [("2330", None)], None, _AT.replace(tzinfo=None)
            )
    finally:
        _drop(conn)


def test_d174_write_line_allows_empty_only_when_line_says_so() -> None:
    """D-174：有門檻的線（allow_empty）沒有股票＝正常的一天：清掉那天舊名單、回 0；其他線空名單照樣報錯。"""
    conn = _connect_or_skip()
    from twquant.data.serving.picks_store import read_line, write_line

    try:
        assert write_line(conn, _SCHEMA, _DAY, "QUIETV", [("2330", 0.4)], "m1", _AT, True) == 1
        # 重跑同一天變成沒有股票：舊名單要清掉，不能留著讓網站以為那天有推薦
        assert write_line(conn, _SCHEMA, _DAY, "QUIETV", [], "m1", _AT, True) == 0
        assert read_line(conn, _SCHEMA, _DAY, "QUIETV") == []
        # 沒宣告 allow_empty 的線：空名單仍代表上游壞了
        with pytest.raises(ValueError, match="空"):
            write_line(conn, _SCHEMA, _DAY, "QUIET", [], "m1", _AT, False)
        assert write_line(conn, _SCHEMA, _DAY, "QUIET", [("2317", 0.3)], "m1", _AT, False) == 1
    finally:
        _drop(conn)
