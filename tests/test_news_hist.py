"""T-412：本機新聞歷史語料（news_hist）的純函式與 SQL 參數（假連線，離線）。"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from twquant.conventions import TZ
from twquant.data import news_hist as nh
from twquant.errors import ConfigError


class _Cur:
    def __init__(self, fetch: list[Any] | None = None, fetchone: Any = None) -> None:
        self.sql: list[tuple[str, Any]] = []
        self.many: list[tuple[str, list[Any]]] = []
        self._fetch, self._one = fetch or [], fetchone
        self.rowcount = 7
        self.description = [("x",)]

    def execute(self, sql: str, params: Any = None) -> None:
        self.sql.append((sql, params))

    def executemany(self, sql: str, params: list[Any]) -> None:
        self.many.append((sql, params))

    def fetchall(self) -> list[Any]:
        return self._fetch

    def fetchone(self) -> Any:
        return self._one

    def __enter__(self) -> _Cur:
        return self

    def __exit__(self, *a: object) -> None:
        return None


class _Conn:
    def __init__(self, cur: _Cur) -> None:
        self.cur, self.commits = cur, 0

    def cursor(self) -> _Cur:
        return self.cur

    def commit(self) -> None:
        self.commits += 1


def _row(i: str = "a", **over: Any) -> nh.ItemRow:
    base: dict[str, Any] = dict(
        id=f"cnyes:{i}",
        origin="cnyes",
        source="cnyes-tw-stock",
        published_at=dt.datetime(2021, 1, 4, 10, tzinfo=TZ),
        available_at=dt.datetime(2021, 1, 4, 10, tzinfo=TZ),
        title="t",
        summary=None,
        content="c",
        stocks=("2330",),
        keywords=("k",),
        url="u",
    )
    base.update(over)
    return nh.ItemRow(**base)


def test_ddl_mentions_every_table_and_available_at() -> None:
    for t in ("items", "backfill_runs", "mentions", "pull_state"):
        assert f"{nh.SCHEMA}.{t}" in nh.DDL
    assert "available_at" in nh.DDL and "halfvec(1024)" in nh.EMBEDDINGS_DDL


def test_upsert_items_requires_tz_and_passes_rows() -> None:
    cur = _Cur()
    conn = _Conn(cur)
    assert nh.upsert_items(conn, [_row("1"), _row("2", stocks=())]) == 2
    sql, params = cur.many[0]
    assert "on conflict (id) do update" in sql and params[0][0] == "cnyes:1" and params[1][8] == []
    assert conn.commits == 1
    with pytest.raises(ValueError, match="時區"):
        nh.upsert_items(conn, [_row("3", published_at=dt.datetime(2021, 1, 4, 10))])
    assert nh.upsert_items(conn, []) == 0


def test_days_between_newest_first_and_done_days() -> None:
    assert nh.days_between(dt.date(2021, 1, 4), dt.date(2021, 1, 6)) == [
        dt.date(2021, 1, 6),
        dt.date(2021, 1, 5),
        dt.date(2021, 1, 4),
    ]
    with pytest.raises(ValueError):
        nh.days_between(dt.date(2021, 1, 6), dt.date(2021, 1, 4))
    cur = _Cur(fetch=[(dt.date(2021, 1, 4),), (dt.date(2021, 1, 5),)])
    assert nh.done_days(_Conn(cur), "cnyes-headline") == {dt.date(2021, 1, 4), dt.date(2021, 1, 5)}
    assert cur.sql[0][1] == ("cnyes-headline",)


def test_embeddings_table_needs_pgvector() -> None:
    conn = _Conn(_Cur(fetchone=None))
    with pytest.raises(ConfigError, match="pgvector"):
        nh.ensure_embeddings_table(conn)
    cur = _Cur(fetchone=(1,))
    nh.ensure_embeddings_table(_Conn(cur))
    assert any("create extension if not exists vector" in s for s, _ in cur.sql)


def test_embed_text_matches_cloud_rule_and_vec_literal() -> None:
    # 同 news-service/src/embed.ts embedText：有譯文用譯文（摘要也用譯文那邊的），標題＋換行＋摘要，截字
    assert nh.embed_text("Nvidia", None, "AI demand", None, 100) == "Nvidia\nAI demand"
    assert nh.embed_text("Nvidia", "輝達", "AI demand", "AI 需求", 100) == "輝達\nAI 需求"
    assert nh.embed_text("Nvidia", "輝達", "AI demand", None, 100) == "輝達"
    assert nh.embed_text("Nvidia", None, None, None, 3) == "Nvi"
    assert nh.vec_literal([0.5, -0.25, 1e-9, 0.1234567]) == "[0.500000,-0.250000,0,0.123457]"


def test_cloud_rows_to_items_uses_first_seen_as_available_at() -> None:
    t = dt.datetime(2026, 10, 19, 13, tzinfo=TZ)
    rows: list[dict[str, Any]] = [
        {
            "id": "x1",
            "source": "bloomberg-tech",
            "lang": "en",
            "url": "u",
            "published_at": None,
            "first_seen_at": t,
            "title": "T",
            "summary": "S",
            "title_zh": "譯",
            "summary_zh": None,
        },
        {
            "id": "x2",
            "source": "cnyes-headline",
            "lang": "zh",
            "url": "u2",
            "published_at": t - dt.timedelta(hours=1),
            "first_seen_at": t,
            "title": "T2",
            "summary": None,
            "title_zh": None,
            "summary_zh": None,
        },
    ]
    a, b = nh.cloud_rows_to_items(rows)
    assert (a.id, a.origin, a.available_at, a.published_at, a.title_zh, a.lang) == (
        "cloud:x1",
        "cloud",
        t,
        t,
        "譯",
        "en",
    )
    assert b.published_at == t - dt.timedelta(hours=1) and b.stocks == ()


def test_pull_cloud_requires_tz_and_writes_three_tables() -> None:
    t = dt.datetime(2026, 10, 19, 13, tzinfo=TZ)
    cloud_cur = _Cur()
    cloud_cur.description = [
        (k,)
        for k in (
            "id",
            "source",
            "lang",
            "url",
            "published_at",
            "first_seen_at",
            "title",
            "summary",
            "title_zh",
            "summary_zh",
        )
    ]
    calls: list[list[Any]] = [
        [("x1", "cnyes-headline", "zh", "u", None, t, "T", None, None, None)],
        [("x1", "tw:2330", "alias", "ev", "2026-10-20.6")],
        [("x1", "@cf/baai/bge-m3", "[0.1,0.2]")],
    ]
    cloud_cur.fetchall = lambda: calls.pop(0)  # type: ignore[method-assign]
    local_cur = _Cur()
    local = _Conn(local_cur)
    n = nh.pull_cloud(
        local,
        _Conn(cloud_cur),
        rule_version="2026-10-20.6",
        since=t - dt.timedelta(days=1),
        with_embeddings=True,
    )
    assert n == {"items": 1, "mentions": 1, "embeddings": 1}
    assert local_cur.many[1][1] == [("cloud:x1", "tw:2330", "alias", "ev", "2026-10-20.6")]
    assert local_cur.many[2][1] == [("cloud:x1", "@cf/baai/bge-m3", "[0.1,0.2]")]
    assert any(
        "pull_state" in s and p == ("cloud_pull_since", t.isoformat()) for s, p in local_cur.sql
    )
    with pytest.raises(ValueError, match="時區"):
        nh.pull_cloud(
            local,
            _Conn(_Cur()),
            rule_version="x",
            since=dt.datetime(2026, 10, 19),
            with_embeddings=False,
        )


def test_daily_pipeline_has_news_pull_step_and_cloud_skips_it() -> None:
    """T-412：每日 pipeline 地端有「新聞語料拉回」（排在內部人申讓之後）；雲端 runner 的主庫不放語料，
    列在 cloud.skip_pipeline_steps；pull 回看天數在 configs/news.yaml。"""
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "twquant" / "cli.py").read_text(encoding="utf-8")
    assert source.index('PipelineStep("內部人申讓"') < source.index('PipelineStep("新聞語料拉回"')
    scfg = yaml.safe_load((root / "configs" / "serving.yaml").read_text(encoding="utf-8"))
    assert "新聞語料拉回" in scfg["cloud"]["skip_pipeline_steps"]
    news = yaml.safe_load((root / "configs" / "news.yaml").read_text(encoding="utf-8"))
    assert int(news["hist"]["pull_lookback_days"]) >= 1
