"""晨報接新聞（2026-10-20）：新聞庫的多家同報事件——分組、家數、排序、事實句；SQL 用假連線驗參數。離線。"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from twquant.conventions import TZ
from twquant.reporting.news_events import NewsEvent, event_line, fetch_multi_outlet, group_events

OUTLET = {
    "cnyes-headline": "鉅亨網",
    "cnyes-tw-stock": "鉅亨網",
    "udn-money-stock": "經濟日報",
    "cna-finance": "中央社",
    "ltn-business": "自由財經",
}
UTC = dt.UTC


def _row(
    ev: str,
    i: str,
    source: str,
    h: int,
    m: int = 0,
    *,
    title: str = "",
    zh: str | None = None,
    stocks: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "cluster_id": ev,
        "id": i,
        "source": source,
        "title": title or f"T{i}",
        "title_zh": zh,
        "url": f"https://x/{i}",
        "first_seen_at": dt.datetime(2026, 10, 20, h, m, tzinfo=UTC),
        "stocks": stocks or [],
    }


def test_group_events_counts_distinct_outlets_and_orders() -> None:
    rows = [
        _row(
            "A", "a1", "cnyes-headline", 1, title="TSMC falls", zh="台積電早盤跌", stocks=["台積電"]
        ),
        _row("A", "a2", "cnyes-tw-stock", 1, 5, stocks=["台積電"]),
        _row("A", "a3", "udn-money-stock", 1, 20, stocks=["聯電"]),
        _row("A", "a4", "cna-finance", 2),
        _row("B", "b1", "cnyes-headline", 3),
        _row("B", "b2", "cnyes-tw-stock", 3, 1),
        _row("B", "b3", "udn-money-stock", 3, 2),
        _row("C", "c1", "cna-finance", 4),
        _row("C", "c2", "ltn-business", 4, 1),
        _row("C", "c3", "udn-money-stock", 4, 2),
    ]
    got = group_events(rows, OUTLET, min_outlets=3, limit=5)
    # A、C 都是三家：同家數新的在前（C 最後一則 04:02、A 02:00）；B 只有鉅亨網兩個頻道＋經濟日報＝兩家，不列
    assert [e.event_id for e in got] == ["C", "A"]
    a = got[1]
    assert a.outlets == ("中央社", "經濟日報", "鉅亨網") and a.items == 4
    assert a.title == "台積電早盤跌" and a.url == "https://x/a1"
    assert a.first_seen_at == dt.datetime(2026, 10, 20, 9, 0, tzinfo=TZ)
    assert a.last_seen_at == dt.datetime(2026, 10, 20, 10, 0, tzinfo=TZ)
    assert a.stocks == ("台積電", "聯電")
    assert group_events(rows, OUTLET, min_outlets=3, limit=1) == [got[0]]
    assert group_events(rows, OUTLET, min_outlets=4, limit=5) == []


def test_event_line_format() -> None:
    e = NewsEvent(
        "A",
        "台積電早盤跌",
        "https://x",
        ("中央社", "鉅亨網", "經濟日報"),
        4,
        dt.datetime(2026, 10, 20, 9, 0, tzinfo=TZ),
        dt.datetime(2026, 10, 20, 10, 13, tzinfo=TZ),
        ("台積電",),
    )
    assert (
        event_line(e)
        == "多家同報（3 家：中央社、鉅亨網、經濟日報；10/20 10:13）：台積電早盤跌（提到 台積電）"
    )
    assert event_line(
        NewsEvent("B", "聯準會", "u", ("A", "B", "C"), 3, e.first_seen_at, e.last_seen_at, ())
    ).endswith("：聯準會")


class _Cur:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.params: tuple[Any, ...] | None = None
        self.description = [(k,) for k in (rows[0] if rows else {"cluster_id": 0})]

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        assert "news.clusters" in sql and "news.mentions" in sql
        self.params = params

    def fetchall(self) -> list[tuple[Any, ...]]:
        return [tuple(r.values()) for r in self.rows]

    def __enter__(self) -> _Cur:
        return self

    def __exit__(self, *a: object) -> None:
        return None


class _Conn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.cur = _Cur(rows)

    def cursor(self) -> _Cur:
        return self.cur


def test_fetch_passes_window_and_rule_version_and_rejects_naive_times() -> None:
    rows = [
        _row("A", "a1", "cna-finance", 1),
        _row("A", "a2", "ltn-business", 1, 1),
        _row("A", "a3", "udn-money-stock", 1, 2),
    ]
    conn = _Conn(rows)
    since, until = (
        dt.datetime(2026, 10, 19, 14, 0, tzinfo=TZ),
        dt.datetime(2026, 10, 20, 6, 30, tzinfo=TZ),
    )
    got = fetch_multi_outlet(
        conn, since, until, rule_version="2026-10-20.5", outlet_of=OUTLET, min_outlets=3, limit=5
    )
    assert conn.cur.params == (since, until, "2026-10-20.5")
    assert len(got) == 1 and got[0].outlets == ("中央社", "經濟日報", "自由財經")
    with pytest.raises(ValueError, match="時區"):
        fetch_multi_outlet(
            conn,
            since.replace(tzinfo=None),
            until,
            rule_version="x",
            outlet_of=OUTLET,
            min_outlets=3,
            limit=5,
        )
