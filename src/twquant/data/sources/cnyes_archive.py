"""鉅亨網歷史新聞回補（T-412／SPEC-018 §4、D-201 §五）：同一個列表 API 加 `startAt`／`endAt`／`page` 一天一天往回抓。

`https://api.cnyes.com/media/api/v1/newslist/category/<分類>?startAt=<秒>&endAt=<秒>&limit=30&page=<頁>`
回 JSON：`items.total`、`items.last_page`、`items.data[]`（newsId、title、summary、content（HTML）、publishAt（秒）、
stock（['2317','HK-1800',…]）、keyword、categoryName）。不是正式開放的介面（同 news_headlines.py）。

條款（D-201 §五）：只做研究、只存本機、不對外重發。一天約 170 則（頭條＋台股），2021-01 起約 53 萬則。
純解析在 parse_page；抓取 fetch_day 用 HttpClient（限流在 system.yaml source_rates.cnyes）。
"""

from __future__ import annotations

import datetime as dt
import html
import re
from dataclasses import dataclass
from typing import Any

from twquant.conventions import TZ
from twquant.data.http import HttpClient
from twquant.errors import DataError

API = "https://api.cnyes.com/media/api/v1/newslist/category/"
NEWS_URL = "https://news.cnyes.com/news/id/"
PER_PAGE = 30
MAX_PAGES = 60  # 一天最多翻幾頁（30 × 60＝1,800 則；實際一天 100 多則）

_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")
_TW_CODE = re.compile(r"^\d{4,6}[A-Z]?$")


@dataclass(frozen=True)
class ArchiveItem:
    """鉅亨網一則（歷史回補）。content 已去掉 HTML、截短。"""

    news_id: int
    title: str
    summary: str | None
    content: str | None
    published_at: dt.datetime  # 台北時間
    category: str
    stocks: tuple[str, ...]  # 鉅亨網自帶的台股代號標籤（去掉 HK-／US-）
    keywords: tuple[str, ...]
    url: str


def strip_html(raw: Any, max_chars: int) -> str | None:
    """HTML／文字 → 純文字（去標籤、解實體、壓空白），截到 max_chars 字；空的 → None。
    鉅亨網的 content 是「轉義過的 HTML」（&lt;p&gt;…），所以先解實體再去標籤、最後再解一次實體；
    2026-10-21 實見：原本先去標籤再解實體，23.6 萬則內文全部留著 <p>。"""
    text = _SPACES.sub(" ", html.unescape(_TAG.sub(" ", html.unescape(str(raw or ""))))).strip()
    if not text:
        return None
    return text[:max_chars]


def parse_page(
    body: Any, label: str, content_chars: int
) -> tuple[list[ArchiveItem], int, int, int]:
    """列表回應 → (這一頁的新聞, last_page, total, 略過幾則)。整頁格式不對 → DataError（不吞）；
    單則沒有標題（鉅亨網下架的文章，2026-02-28 實見）→ 略過並計數，呼叫端記進 backfill_runs.error。"""
    if not isinstance(body, dict) or body.get("statusCode") != 200:
        why = body.get("message") if isinstance(body, dict) else type(body).__name__
        raise DataError("cnyes_archive", None, f"鉅亨網 {label} 回應異常：{why!r}")
    items = body.get("items")
    if not isinstance(items, dict) or not isinstance(items.get("data"), list):
        raise DataError("cnyes_archive", None, f"鉅亨網 {label} 沒有 items.data")
    out: list[ArchiveItem] = []
    skipped = 0
    for it in items["data"]:
        nid, title, ts = it.get("newsId"), strip_html(it.get("title"), 300), it.get("publishAt")
        try:
            nid_i, ts_i = int(nid), int(ts)
        except (TypeError, ValueError) as e:
            raise DataError(
                "cnyes_archive", None, f"鉅亨網 {label} 有一則缺編號或時間：{str(it)[:120]}"
            ) from e
        if not title:
            skipped += 1
            continue
        stocks = tuple(
            dict.fromkeys(str(s) for s in (it.get("stock") or []) if _TW_CODE.match(str(s)))
        )
        keywords = tuple(dict.fromkeys(str(k) for k in (it.get("keyword") or []) if str(k).strip()))
        out.append(
            ArchiveItem(
                news_id=nid_i,
                title=title,
                summary=strip_html(it.get("summary"), 1000),
                content=strip_html(it.get("content"), content_chars),
                published_at=dt.datetime.fromtimestamp(ts_i, TZ),
                category=strip_html(it.get("categoryName"), 40) or label,
                stocks=stocks,
                keywords=keywords,
                url=f"{NEWS_URL}{nid_i}",
            )
        )
    last_page = int(items.get("last_page") or 1)
    total = int(items.get("total") or len(out))
    return out, last_page, total, skipped


def day_bounds(day: dt.date) -> tuple[int, int]:
    """台北一天的起訖（秒）。"""
    start = dt.datetime.combine(day, dt.time(0, 0), tzinfo=TZ)
    return int(start.timestamp()), int((start + dt.timedelta(days=1)).timestamp()) - 1


def fetch_day(
    client: HttpClient,
    category: str,
    day: dt.date,
    *,
    content_chars: int,
    max_pages: int = MAX_PAGES,
) -> tuple[list[ArchiveItem], int, int]:
    """抓一個分類一天的全部（翻頁到 last_page）；回 (新聞, total, 略過幾則)。同一則在不同頁重複出現只留一次。"""
    start, end = day_bounds(day)
    seen: dict[int, ArchiveItem] = {}
    total = 0
    skipped = 0
    page = 1
    while page <= max_pages:
        resp = client.get(
            f"{API}{category}",
            params={
                "startAt": str(start),
                "endAt": str(end),
                "limit": str(PER_PAGE),
                "page": str(page),
            },
        )
        if resp.status_code != 200:
            raise DataError(
                "cnyes_archive",
                None,
                f"鉅亨網 {category} {day} 第 {page} 頁 HTTP {resp.status_code}",
            )
        items, last_page, total, sk = parse_page(
            _json(resp.text), f"{category} {day}", content_chars
        )
        skipped += sk
        for it in items:
            seen.setdefault(it.news_id, it)
        if page >= last_page or not items:
            break
        page += 1
    return list(seen.values()), total, skipped


def _json(text: str) -> Any:
    import json

    try:
        return json.loads(text)
    except ValueError as e:
        raise DataError("cnyes_archive", None, f"鉅亨網回應不是 JSON：{text[:80]!r}") from e
