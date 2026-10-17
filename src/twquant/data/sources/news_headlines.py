"""晨報的新聞頭條（2026-10-17，使用者：「也加上昨晚國際金融大事、新聞頭條」）：鉅亨網新聞列表 API。

`https://api.cnyes.com/media/api/v1/newslist/category/<分類>?limit=N`，回 JSON：`items.data[]` 每則有
newsId、title、summary（含 HTML 實體，例 `&#10;`）、publishAt（epoch 秒）、categoryName。原文網址
`https://news.cnyes.com/news/id/<newsId>`。不是正式開放的介面：格式不對就 raise，晨報那段留空並記原因。

只取標題與摘要給晨報當「事實」，網站列標題與連結；不存全文、不轉載。
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
from dataclasses import dataclass
from typing import Any

from twquant.conventions import TZ
from twquant.data.http import HttpClient
from twquant.errors import DataError

API = "https://api.cnyes.com/media/api/v1/newslist/category/"
NEWS_URL = "https://news.cnyes.com/news/id/"
SOURCE = "鉅亨網"
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class Headline:
    """One news headline as shown on the home page and cited by the brief."""

    news_id: int
    title: str
    summary: str | None
    published_at: dt.datetime  # 台北時間
    category: str | None
    url: str
    source: str


def _clean(text: Any) -> str:
    return _SPACES.sub(" ", html.unescape(str(text or ""))).strip()


def parse(body: Any, label: str) -> list[Headline]:
    """鉅亨網列表回應 → 頭條。statusCode 不是 200 或沒有 items.data → raise；缺標題或時間的單則 raise。"""
    if not isinstance(body, dict) or body.get("statusCode") != 200:
        why = body.get("message") if isinstance(body, dict) else type(body).__name__
        raise DataError("news_headlines", None, f"鉅亨網 {label} 回應異常：{why!r}")
    items = (body.get("items") or {}).get("data")
    if not isinstance(items, list):
        raise DataError("news_headlines", None, f"鉅亨網 {label} 沒有 items.data")
    out: list[Headline] = []
    for it in items:
        title = _clean(it.get("title"))
        ts = it.get("publishAt")
        nid = it.get("newsId")
        if not title or not isinstance(ts, (int, float)) or not isinstance(nid, int):
            raise DataError(
                "news_headlines", None, f"鉅亨網 {label} 有一則缺標題／時間／編號：{str(it)[:120]}"
            )
        summary = _clean(it.get("summary")) or None
        out.append(
            Headline(
                news_id=nid,
                title=title,
                summary=summary,
                published_at=dt.datetime.fromtimestamp(ts, TZ),
                category=_clean(it.get("categoryName")) or label,
                url=f"{NEWS_URL}{nid}",
                source=SOURCE,
            )
        )
    return out


def fetch(client: HttpClient, category: str, limit: int, label: str) -> list[Headline]:
    resp = client.get(f"{API}{category}", params={"limit": str(limit)})
    if resp.status_code != 200:
        raise DataError("news_headlines", None, f"鉅亨網 {label} HTTP {resp.status_code}")
    return parse(json.loads(resp.text), label)


def window(
    items: list[Headline], since: dt.datetime, until: dt.datetime, limit: int
) -> list[Headline]:
    """[since, until] 之間、去重（同 newsId）、新到舊，最多 limit 則。"""
    seen: set[int] = set()
    out: list[Headline] = []
    for h in sorted(items, key=lambda h: h.published_at, reverse=True):
        if h.news_id in seen or not (since <= h.published_at <= until):
            continue
        seen.add(h.news_id)
        out.append(h)
        if len(out) >= limit:
            break
    return out
