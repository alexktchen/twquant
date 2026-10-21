"""晨報接新聞（使用者 2026-10-20 選的「晨報接新聞」）：新聞服務裡「多家同報」的事件 → 晨報的事實句。

新聞服務（news-service，另一個 Supabase 專案）把同一件事的不同報導併成事件群（news.clusters）。
一件事被越多家不同媒體報，越可能是重點；晨報只拿「至少 min_outlets 家」的事件當事實句，模型照舊只能引用事實句裡的數字。
同一家媒體的兩個頻道（鉅亨網頭條、鉅亨網台股）算一家：outlet_of 把來源代號換成媒體名。

這裡是 reporting 層：可以連新聞庫（TWQUANT_NEWS_DSN）；insight/ 不碰資料庫。查詢只讀。
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from twquant.conventions import TZ


@dataclass(frozen=True)
class NewsEvent:
    """一件被多家媒體報的事：標題用最早那則（有譯文用譯文）。"""

    event_id: str
    title: str
    url: str
    outlets: tuple[str, ...]  # 不同媒體名，排序過
    items: int  # 幾則報導
    first_seen_at: dt.datetime  # 台北時間，最早那則
    last_seen_at: dt.datetime  # 台北時間，最新那則
    stocks: tuple[str, ...]  # 報導裡別名比對到的台股名（最多 4 個）


_SQL = """
with m as (
  select c.cluster_id, i.id, i.source, i.url, i.first_seen_at,
         coalesce(v.title_orig, i.title_orig) as title, tr.title_zh
  from news.raw_items i
  join news.clusters c on c.item_id = i.id
  left join lateral (
    select title_orig, content_sha from news.raw_item_versions
    where item_id = i.id order by seen_at desc limit 1
  ) v on true
  left join news.translations tr
    on tr.item_id = i.id and tr.content_sha = coalesce(v.content_sha, i.content_sha) and tr.status = 'ok'
  where i.first_seen_at >= %s and i.first_seen_at < %s
)
select m.cluster_id, m.id, m.source, m.title, m.title_zh, m.url, m.first_seen_at,
       coalesce((
         select array_agg(distinct e.label order by e.label)
         from news.mentions mm join news.entities e on e.id = mm.entity_id
         where mm.item_id = m.id and mm.rule_version = %s and e.type = 'tw'
       ), '{}') as stocks
from m
order by m.first_seen_at, m.id
"""


def group_events(
    rows: list[dict[str, Any]],
    outlet_of: Mapping[str, str],
    *,
    min_outlets: int,
    limit: int,
) -> list[NewsEvent]:
    """查詢結果 → 多家同報的事件：家數多的在前，同家數新的在前；最多 limit 件。純函式。

    rows 每列：cluster_id, id, source, title, title_zh, url, first_seen_at（帶時區）, stocks（list）。
    """
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[str(r["cluster_id"])].append(r)
    out: list[NewsEvent] = []
    for ev, rs in by.items():
        outlets = sorted({outlet_of.get(str(r["source"]), str(r["source"])) for r in rs})
        if len(outlets) < min_outlets:
            continue
        rs = sorted(rs, key=lambda r: (r["first_seen_at"], str(r["id"])))
        first = rs[0]
        stocks = sorted({str(s) for r in rs for s in (r.get("stocks") or [])})[:4]
        out.append(
            NewsEvent(
                event_id=ev,
                title=str(first["title_zh"] or first["title"]),
                url=str(first["url"]),
                outlets=tuple(outlets),
                items=len(rs),
                first_seen_at=_taipei(first["first_seen_at"]),
                last_seen_at=_taipei(rs[-1]["first_seen_at"]),
                stocks=tuple(stocks),
            )
        )
    out.sort(key=lambda e: (-len(e.outlets), e.last_seen_at, e.event_id), reverse=False)
    # 家數多的在前；同家數新的在前（上面 last_seen_at 是正序，所以再排一次）
    out.sort(key=lambda e: (-len(e.outlets), -e.last_seen_at.timestamp(), e.event_id))
    return out[:limit]


def _taipei(t: Any) -> dt.datetime:
    if not isinstance(t, dt.datetime):
        raise TypeError(f"first_seen_at 應是 datetime，拿到 {type(t).__name__}")
    if t.tzinfo is None:
        raise ValueError("新聞庫的 first_seen_at 應帶時區（timestamptz）")
    return t.astimezone(TZ)


def fetch_multi_outlet(
    conn: Any,
    since: dt.datetime,
    until: dt.datetime,
    *,
    rule_version: str,
    outlet_of: Mapping[str, str],
    min_outlets: int,
    limit: int,
) -> list[NewsEvent]:
    """新聞庫裡 [since, until) 抓到的報導 → 多家同報的事件（只讀）。since／until 要帶時區。"""
    if since.tzinfo is None or until.tzinfo is None:
        raise ValueError("since／until 必須帶時區")
    with conn.cursor() as cur:
        cur.execute(_SQL, (since, until, rule_version))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
    return group_events(rows, outlet_of, min_outlets=min_outlets, limit=limit)


def event_line(e: NewsEvent) -> str:
    """晨報的事實句：「多家同報（3 家：中央社、經濟日報、鉅亨網；10/20 10:13）：標題（提到 台積電）」。"""
    stamp = e.last_seen_at.strftime("%m/%d %H:%M")
    line = f"多家同報（{len(e.outlets)} 家：{'、'.join(e.outlets)}；{stamp}）：{e.title}"
    return line + (f"（提到 {'、'.join(e.stocks)}）" if e.stocks else "")
