"""本機新聞歷史語料（T-412／SPEC-018 §3.2、D-201 §二）：schema `news_hist`，研究正本。

來源三種（origin）：cnyes（鉅亨網 2021 起回補）、mops（本機 serving.material_news 的主旨與截短內容）、cloud（新聞服務每天拉下來的線上新聞）。
研究用可得時間 available_at：cnyes＝發布時間；mops＝公告的 available_at；cloud＝first_seen_at。
D-109：研究腳本一律只用 ≤ 2026-08-31 的列（測試強制，見 T-414／T-415）。

這裡是資料層：可以連 PG、可以寫。向量表要 pgvector（本機 PG 換 pgvector/pgvector:pg16 映像後才建）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from twquant.errors import ConfigError

SCHEMA = "news_hist"

DDL = f"""
create schema if not exists {SCHEMA};
create table if not exists {SCHEMA}.items (
  id            text primary key,            -- cnyes:<newsId> / mops:<content_md5> / cloud:<id>
  origin        text not null check (origin in ('cnyes', 'mops', 'cloud')),
  source        text not null,               -- cnyes-headline / cnyes-tw-stock / mops / 雲端來源代號
  published_at  timestamptz not null,
  available_at  timestamptz not null,        -- 研究用可得時間（黃金規則 1）
  title         text not null,
  summary       text,
  content       text,                        -- 截短的全文（鉅亨網）或公告內容（MOPS）
  stocks        text[] not null default '{{}}',  -- 來源自帶的台股標籤（鉅亨 stock、公告 stock_id）
  keywords      text[] not null default '{{}}',
  url           text,
  lang          text not null default 'zh',
  title_zh      text,
  summary_zh    text,
  ingested_at   timestamptz not null default now()
);
create index if not exists items_published_at on {SCHEMA}.items (published_at);
create index if not exists items_origin_published on {SCHEMA}.items (origin, published_at);
create table if not exists {SCHEMA}.backfill_runs (
  source  text not null,
  day     date not null,
  total   int,
  fetched int,
  ok      boolean not null,
  error   text,
  ran_at  timestamptz not null default now(),
  primary key (source, day)
);
create table if not exists {SCHEMA}.mentions (
  item_id      text not null references {SCHEMA}.items (id),
  entity_id    text not null,
  method       text not null check (method in ('alias', 'semantic', 'tag')),
  evidence     text,
  rule_version text,
  score        double precision,
  primary key (item_id, entity_id, method)
);
create index if not exists mentions_entity on {SCHEMA}.mentions (entity_id);
create table if not exists {SCHEMA}.mention_scans (
  item_id      text not null references {SCHEMA}.items (id),
  rule_version text not null,
  n            int not null,
  scanned_at   timestamptz not null default now(),
  primary key (item_id, rule_version)
);
create table if not exists {SCHEMA}.pull_state (key text primary key, value text not null, updated_at timestamptz not null default now());
"""

EMBEDDINGS_DDL = f"""
create table if not exists {SCHEMA}.embeddings (
  item_id    text primary key references {SCHEMA}.items (id),
  model      text not null,
  vec        halfvec(1024) not null,
  created_at timestamptz not null default now()
);
"""


def ensure_schema(conn: Any) -> None:
    """建 schema 與不需要 pgvector 的表（冪等）。"""
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()


def has_pgvector(conn: Any) -> bool:
    with conn.cursor() as cur:
        cur.execute("select 1 from pg_extension where extname = 'vector'")
        return cur.fetchone() is not None


def ensure_embeddings_table(conn: Any) -> None:
    """向量表：要本機 PG 已換 pgvector 映像並 `create extension vector`；沒有就明說，不假裝建好。"""
    with conn.cursor() as cur:
        cur.execute("select 1 from pg_available_extensions where name = 'vector'")
        if cur.fetchone() is None:
            raise ConfigError(
                "本機 PG 沒有 pgvector：docker-compose 的 pg 要換成 pgvector/pgvector:pg16（SPEC-018 §3.3，先 pg_dump）"
            )
        cur.execute("create extension if not exists vector")
        cur.execute(EMBEDDINGS_DDL)
    conn.commit()


@dataclass(frozen=True)
class ItemRow:
    id: str
    origin: str
    source: str
    published_at: dt.datetime
    available_at: dt.datetime
    title: str
    summary: str | None
    content: str | None
    stocks: tuple[str, ...]
    keywords: tuple[str, ...]
    url: str | None
    lang: str = "zh"
    title_zh: str | None = None
    summary_zh: str | None = None


_UPSERT = f"""
insert into {SCHEMA}.items (id, origin, source, published_at, available_at, title, summary, content, stocks, keywords, url, lang, title_zh, summary_zh)
values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
on conflict (id) do update set
  title = excluded.title, summary = excluded.summary, content = excluded.content, stocks = excluded.stocks,
  keywords = excluded.keywords, url = excluded.url, title_zh = coalesce(excluded.title_zh, {SCHEMA}.items.title_zh),
  summary_zh = coalesce(excluded.summary_zh, {SCHEMA}.items.summary_zh)
"""


def upsert_items(conn: Any, rows: Iterable[ItemRow]) -> int:
    """寫入（同 id 覆蓋文字欄；不動 published_at／available_at）。回寫了幾列。時間必須帶時區。"""
    data = []
    for r in rows:
        if r.published_at.tzinfo is None or r.available_at.tzinfo is None:
            raise ValueError(f"{r.id}：published_at／available_at 必須帶時區")
        data.append(
            (
                r.id,
                r.origin,
                r.source,
                r.published_at,
                r.available_at,
                r.title,
                r.summary,
                r.content,
                list(r.stocks),
                list(r.keywords),
                r.url,
                r.lang,
                r.title_zh,
                r.summary_zh,
            )
        )
    if not data:
        return 0
    with conn.cursor() as cur:
        cur.executemany(_UPSERT, data)
    conn.commit()
    return len(data)


def record_run(
    conn: Any,
    source: str,
    day: dt.date,
    *,
    total: int | None,
    fetched: int | None,
    ok: bool,
    error: str | None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"""insert into {SCHEMA}.backfill_runs (source, day, total, fetched, ok, error, ran_at)
                values (%s, %s, %s, %s, %s, %s, now())
                on conflict (source, day) do update set total = excluded.total, fetched = excluded.fetched,
                  ok = excluded.ok, error = excluded.error, ran_at = now()""",
            (source, day, total, fetched, ok, error),
        )
    conn.commit()


def done_days(conn: Any, source: str) -> set[dt.date]:
    """這個來源已經成功回補的日子（重跑略過）。"""
    with conn.cursor() as cur:
        cur.execute(f"select day from {SCHEMA}.backfill_runs where source = %s and ok", (source,))
        return {r[0] for r in cur.fetchall()}


def days_between(start: dt.date, end: dt.date) -> list[dt.date]:
    """start..end（含）每一天，新的先（先補近的，研究先有得用）。"""
    if end < start:
        raise ValueError(f"結束日 {end} 早於起始日 {start}")
    return [end - dt.timedelta(days=i) for i in range((end - start).days + 1)]


# ── MOPS：本機 serving.material_news（9/28 研究全文快照）→ items（origin mops）──
MOPS_IMPORT = f"""
insert into {SCHEMA}.items (id, origin, source, published_at, available_at, title, summary, content, stocks, keywords, url, lang)
select 'mops:' || content_md5, 'mops', 'mops', announced_at, coalesce(available_at, announced_at),
       left(subject, 300), null, left(content, %s), array[stock_id], '{{}}', null, 'zh'
from serving.material_news
where content_md5 is not null and subject is not null and announced_at is not null
on conflict (id) do nothing
"""


def import_mops(conn: Any, content_chars: int) -> int:
    """公告主旨＋截短內容搬進語料（冪等）。回新增幾列。"""
    with conn.cursor() as cur:
        cur.execute(MOPS_IMPORT, (content_chars,))
        n = cur.rowcount
    conn.commit()
    return int(n)


# ── 雲端新聞服務 → 本機（每天 news pull；D-201 §二：只拉不反向）──
CLOUD_ITEMS_SQL = """
select i.id, i.source, i.lang, i.url, i.published_at, i.first_seen_at,
       coalesce(v.title_orig, i.title_orig) as title, coalesce(v.summary_orig, i.summary_orig) as summary, tr.title_zh, tr.summary_zh
from news.raw_items i
left join lateral (select title_orig, summary_orig, content_sha from news.raw_item_versions where item_id = i.id order by seen_at desc limit 1) v on true
left join news.translations tr on tr.item_id = i.id and tr.content_sha = coalesce(v.content_sha, i.content_sha) and tr.status = 'ok'
where i.first_seen_at > %s
order by i.first_seen_at
"""
CLOUD_MENTIONS_SQL = """
select m.item_id, m.entity_id, m.method, m.evidence, m.rule_version
from news.mentions m join news.raw_items i on i.id = m.item_id
where i.first_seen_at > %s and m.rule_version = %s
"""
CLOUD_EMBED_SQL = """
select e.item_id, e.model, e.vec::text from news.embeddings e join news.raw_items i on i.id = e.item_id where i.first_seen_at > %s
"""


def cloud_rows_to_items(rows: Sequence[dict[str, Any]]) -> list[ItemRow]:
    """雲端 raw_items 列 → 本機語料列（id 加 cloud: 前綴；available_at＝first_seen_at）。"""
    out: list[ItemRow] = []
    for r in rows:
        out.append(
            ItemRow(
                id=f"cloud:{r['id']}",
                origin="cloud",
                source=str(r["source"]),
                published_at=r["published_at"] or r["first_seen_at"],
                available_at=r["first_seen_at"],
                title=str(r["title"]),
                summary=r.get("summary"),
                content=None,
                stocks=(),
                keywords=(),
                url=r.get("url"),
                lang=str(r.get("lang") or "zh"),
                title_zh=r.get("title_zh"),
                summary_zh=r.get("summary_zh"),
            )
        )
    return out


def get_state(conn: Any, key: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute(f"select value from {SCHEMA}.pull_state where key = %s", (key,))
        row = cur.fetchone()
    return None if row is None else str(row[0])


def set_state(conn: Any, key: str, value: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"insert into {SCHEMA}.pull_state (key, value, updated_at) values (%s, %s, now()) on conflict (key) do update set value = excluded.value, updated_at = now()",
            (key, value),
        )
    conn.commit()


def pull_cloud(
    local: Any, cloud: Any, *, rule_version: str, since: dt.datetime, with_embeddings: bool
) -> dict[str, int]:
    """雲端 first_seen_at > since 的新聞、提及、向量 → 本機。回各寫了幾列。since 要帶時區。
    同 id 再拉一次會覆蓋文字欄與向量（雲端是權威）：英文新聞拉下來時可能還沒翻譯、還沒有向量，
    呼叫端要回看幾天（hist.pull_lookback_days）；2026-10-21 驗收實見 3.6% 的向量因此對不上。"""
    if since.tzinfo is None:
        raise ValueError("since 必須帶時區")
    with cloud.cursor() as cur:
        cur.execute(CLOUD_ITEMS_SQL, (since,))
        cols = [d[0] for d in cur.description]
        items = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        cur.execute(CLOUD_MENTIONS_SQL, (since, rule_version))
        mentions = cur.fetchall()
        embeds = []
        if with_embeddings:
            cur.execute(CLOUD_EMBED_SQL, (since,))
            embeds = cur.fetchall()
    n_items = upsert_items(local, cloud_rows_to_items(items))
    with local.cursor() as cur:
        cur.executemany(
            f"insert into {SCHEMA}.mentions (item_id, entity_id, method, evidence, rule_version) values (%s, %s, %s, %s, %s) on conflict do nothing",
            [(f"cloud:{m[0]}", m[1], m[2], m[3], m[4]) for m in mentions],
        )
        n_emb = 0
        if embeds:
            cur.executemany(
                f"insert into {SCHEMA}.embeddings (item_id, model, vec) values (%s, %s, %s::halfvec) on conflict (item_id) do update set model = excluded.model, vec = excluded.vec",
                [(f"cloud:{e[0]}", e[1], e[2]) for e in embeds],
            )
            n_emb = len(embeds)
    local.commit()
    if items:
        set_state(local, "cloud_pull_since", max(i["first_seen_at"] for i in items).isoformat())
    return {"items": n_items, "mentions": len(mentions), "embeddings": n_emb}


# ── 本機向量（bge-m3，GPU）──
def embed_text(
    title: str, title_zh: str | None, summary: str | None, summary_zh: str | None, max_chars: int
) -> str:
    """跟雲端同一條規則（news-service/src/embed.ts embedText）：有譯文用譯文；標題＋換行＋摘要，截到 max_chars 字。"""
    t = title_zh if title_zh else title
    s = (summary_zh if title_zh else summary) or ""
    return f"{t}\n{s}".strip()[:max_chars]


def vec_literal(v: Sequence[float]) -> str:
    """pgvector 文字格式（halfvec 只有 3 位有效數字，6 位小數夠）。"""
    return "[" + ",".join("0" if abs(x) < 5e-7 else f"{x:.6f}" for x in v) + "]"


def pending_for_embedding(conn: Any, limit: int, max_chars: int) -> list[tuple[str, str]]:
    """還沒有向量的 (id, 文字)，舊的先。max_chars 用 configs/news.yaml embed.max_chars（跟雲端同一個數字，向量才能對得上）。"""
    with conn.cursor() as cur:
        cur.execute(
            f"""select i.id, i.title, i.title_zh, i.summary, i.summary_zh from {SCHEMA}.items i
                left join {SCHEMA}.embeddings e on e.item_id = i.id where e.item_id is null
                order by i.published_at, i.id limit %s""",
            (limit,),
        )
        rows = cur.fetchall()
    return [(r[0], embed_text(r[1], r[2], r[3], r[4], max_chars)) for r in rows]


def save_embeddings(conn: Any, model: str, rows: Iterable[tuple[str, Sequence[float]]]) -> int:
    data = [(i, model, vec_literal(v)) for i, v in rows]
    if not data:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            f"insert into {SCHEMA}.embeddings (item_id, model, vec) values (%s, %s, %s::halfvec) on conflict (item_id) do nothing",
            data,
        )
    conn.commit()
    return len(data)


# ── 提及回補（T-412）：本機語料用雲端同一套 mention.ts 規則（node 離線跑 news-service/scripts/mentions_offline.ts）；
#    來源自帶的股票代號記 method='tag'；0 命中也記 mention_scans，下次不再掃 ──
CLOUD_ENTITIES_SQL = (
    "select id, label, aliases from news.entities where valid_to is null order by id"
)


def export_entities(cloud: Any) -> list[dict[str, Any]]:
    """雲端實體與別名（valid_to is null）→ 給 node 離線比對的清單 [{id, label, aliases}]。"""
    with cloud.cursor() as cur:
        cur.execute(CLOUD_ENTITIES_SQL)
        return [{"id": r[0], "label": r[1], "aliases": list(r[2] or [])} for r in cur.fetchall()]


def mention_texts(
    origin: str, title: str, summary: str | None, content: str | None
) -> list[str | None]:
    """給別名比對的文字：鉅亨網 標題／摘要／內文，MOPS 主旨／內文。雲端新聞的提及由 news pull 拉回，不在本機比。"""
    if origin == "cnyes":
        return [title, summary, content]
    if origin == "mops":
        return [title, content]
    raise ValueError(f"{origin}：這個來源的提及不在本機比（雲端新聞用 news pull 拉回）")


def tag_mentions(
    item_id: str, stocks: Sequence[str], rule_version: str
) -> list[tuple[str, str, str, str | None, str]]:
    """來源自帶的股票代號 → method='tag' 的提及（鉅亨網 stock[] 解析時只留台股；MOPS 是公告公司本身）。"""
    return [(item_id, f"tw:{s}", "tag", None, rule_version) for s in dict.fromkeys(stocks)]


def pending_for_mentions(
    conn: Any, rule_version: str, origins: Sequence[str], limit: int
) -> list[tuple[str, str, str, str | None, str | None, list[str]]]:
    """還沒用這版規則比過的本機新聞：(id, origin, title, summary, content, stocks)，舊的先。"""
    with conn.cursor() as cur:
        cur.execute(
            f"""select i.id, i.origin, i.title, i.summary, i.content, i.stocks from {SCHEMA}.items i
                left join {SCHEMA}.mention_scans s on s.item_id = i.id and s.rule_version = %s
                where s.item_id is null and i.origin = any(%s) order by i.published_at, i.id limit %s""",
            (rule_version, list(origins), limit),
        )
        return [
            (str(r[0]), str(r[1]), str(r[2]), r[3], r[4], list(r[5] or [])) for r in cur.fetchall()
        ]


def save_mentions(
    conn: Any,
    rows: Sequence[tuple[str, str, str, str | None, str]],
    scans: Sequence[tuple[str, str, int]],
) -> int:
    """寫提及（同 (item, entity, method) 不重複）與掃描紀錄。回寫了幾列提及。"""
    with conn.cursor() as cur:
        if rows:
            cur.executemany(
                f"insert into {SCHEMA}.mentions (item_id, entity_id, method, evidence, rule_version) values (%s, %s, %s, %s, %s) on conflict do nothing",
                list(rows),
            )
        if scans:
            cur.executemany(
                f"insert into {SCHEMA}.mention_scans (item_id, rule_version, n) values (%s, %s, %s) on conflict do nothing",
                list(scans),
            )
    conn.commit()
    return len(rows)


_AGREE_CTE = f"""
with s as (select item_id from {SCHEMA}.mention_scans where rule_version = %(rv)s),
t as (select m.item_id, m.entity_id from {SCHEMA}.mentions m join s using (item_id) where m.method = 'tag'),
a as (select m.item_id, m.entity_id, m.evidence from {SCHEMA}.mentions m join s using (item_id)
      where m.method = 'alias' and m.rule_version = %(rv)s and m.entity_id like 'tw:%%'),
tagged as (select distinct item_id from t)
"""
AGREEMENT_SQL = (
    _AGREE_CTE
    + """select
  (select count(*) from s) as scanned,
  (select count(*) from tagged) as items_with_tag,
  (select count(*) from t) as tags,
  (select count(*) from t join a using (item_id, entity_id)) as tags_found,
  (select count(*) from a join tagged using (item_id)) as alias_hits_on_tagged,
  (select count(*) from a join tagged using (item_id) left join t using (item_id, entity_id) where t.item_id is null) as alias_not_tagged
"""
)
DISAGREE_TOP_SQL = (
    _AGREE_CTE
    + """select a.entity_id, count(*) as n, min(a.evidence) as sample
from a join tagged using (item_id) left join t using (item_id, entity_id)
where t.item_id is null group by a.entity_id order by n desc, a.entity_id limit %(top)s
"""
)


def mention_agreement(conn: Any, rule_version: str, top: int) -> dict[str, Any]:
    """T-412 驗收 ②：鉅亨網自帶股票 tag vs 別名命中（台股）——tag 被別名找到的比率、別名命中但來源沒標的比率，
    與沒標的前幾個實體（看誤判）。只算有 tag 的新聞。"""
    with conn.cursor() as cur:
        cur.execute(AGREEMENT_SQL, {"rv": rule_version})
        row = cur.fetchone()
        cols = [d[0] for d in cur.description]
        cur.execute(DISAGREE_TOP_SQL, {"rv": rule_version, "top": top})
        tops = [(str(r[0]), int(r[1]), r[2]) for r in cur.fetchall()]
    out: dict[str, Any] = dict(zip(cols, (int(v) for v in row), strict=True))
    out["tag_recall"] = out["tags_found"] / out["tags"] if out["tags"] else 0.0
    out["alias_untagged_share"] = (
        out["alias_not_tagged"] / out["alias_hits_on_tagged"]
        if out["alias_hits_on_tagged"]
        else 0.0
    )
    out["top_untagged"] = tops
    return out
