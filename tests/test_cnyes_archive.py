"""T-412：鉅亨網歷史回補的解析與翻頁（離線：假的 HttpClient 回錄好的 JSON）。"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any, cast

import pytest

from twquant.conventions import TZ
from twquant.data.http import HttpClient
from twquant.data.sources import cnyes_archive as ca
from twquant.errors import DataError


def _item(nid: int, ts: int, **over: Any) -> dict[str, Any]:
    base = {
        "newsId": nid,
        "title": f"<b>標題 {nid}</b>&nbsp;測試",
        "summary": "摘要  有  空白",
        "content": "<p>第一段</p><p>第二段 &amp; 第三段</p>",
        "publishAt": ts,
        "categoryName": "台股新聞",
        "stock": ["2317", "HK-1800", "US-NVDA", "2330", "2317", "00878"],
        "keyword": ["鴻海", " ", "電動車", "鴻海"],
    }
    base.update(over)
    return base


def _body(
    items: list[dict[str, Any]], last_page: int = 1, total: int | None = None
) -> dict[str, Any]:
    return {
        "statusCode": 200,
        "items": {
            "total": total if total is not None else len(items),
            "last_page": last_page,
            "per_page": 30,
            "data": items,
        },
    }


def test_parse_page_cleans_text_and_keeps_tw_codes_only() -> None:
    rows, last_page, total = ca.parse_page(
        _body([_item(5, 1609765936)], last_page=3, total=76), "x", content_chars=12
    )
    assert (last_page, total) == (3, 76)
    it = rows[0]
    assert it.news_id == 5 and it.title == "標題 5 測試" and it.summary == "摘要 有 空白"
    assert it.content == "第一段 第二段 & 第三"  # 去標籤、解實體、截 12 字
    assert (
        it.published_at == dt.datetime.fromtimestamp(1609765936, TZ)
        and it.published_at.tzinfo is not None
    )
    assert it.stocks == ("2317", "2330", "00878") and it.keywords == ("鴻海", "電動車")
    assert it.url == "https://news.cnyes.com/news/id/5" and it.category == "台股新聞"


@pytest.mark.parametrize(
    "body,msg",
    [
        ({"statusCode": 500, "message": "boom"}, "回應異常"),
        ({"statusCode": 200, "items": {}}, "items.data"),
        (_body([_item(1, 1609765936, newsId=None)]), "缺編號或時間"),
        (_body([_item(1, 1609765936, title="<p></p>")]), "沒有標題"),
    ],
)
def test_parse_page_rejects_bad_bodies(body: dict[str, Any], msg: str) -> None:
    with pytest.raises(DataError, match=msg):
        ca.parse_page(body, "x", content_chars=100)


def test_day_bounds_is_taipei_day() -> None:
    a, b = ca.day_bounds(dt.date(2021, 1, 4))
    assert a == int(dt.datetime(2021, 1, 4, tzinfo=TZ).timestamp()) and b - a == 86_399


class _Resp:
    def __init__(self, body: dict[str, Any], status: int = 200) -> None:
        self.status_code, self.text = status, json.dumps(body)
        self.content = self.text.encode()


class _Client:
    """假的 HttpClient：記下每次的參數，依 page 回不同頁。"""

    def __init__(self, pages: dict[int, dict[str, Any]], status: int = 200) -> None:
        self.pages, self.status = pages, status
        self.calls: list[dict[str, str]] = []

    def get(self, url: str, params: dict[str, str] | None = None) -> _Resp:
        assert url.endswith("/tw_stock") and params
        self.calls.append(params)
        return _Resp(self.pages[int(params["page"])], self.status)


def test_fetch_day_pages_to_last_page_and_dedups() -> None:
    pages = {
        1: _body([_item(1, 1609765936), _item(2, 1609765900)], last_page=2, total=3),
        2: _body([_item(2, 1609765900), _item(3, 1609765800)], last_page=2, total=3),
    }
    client = _Client(pages)
    items, total = ca.fetch_day(
        cast(HttpClient, client), "tw_stock", dt.date(2021, 1, 4), content_chars=50
    )
    assert total == 3 and [i.news_id for i in items] == [1, 2, 3]
    a, b = ca.day_bounds(dt.date(2021, 1, 4))
    assert [c["page"] for c in client.calls] == ["1", "2"]
    assert client.calls[0] == {"startAt": str(a), "endAt": str(b), "limit": "30", "page": "1"}


def test_fetch_day_raises_on_http_error_and_stops_on_empty_page() -> None:
    with pytest.raises(DataError, match="HTTP 503"):
        ca.fetch_day(
            cast(HttpClient, _Client({1: _body([])}, status=503)),
            "tw_stock",
            dt.date(2021, 1, 4),
            content_chars=50,
        )
    client = _Client({1: _body([], last_page=5)})
    items, total = ca.fetch_day(
        cast(HttpClient, client), "tw_stock", dt.date(2021, 1, 4), content_chars=50
    )
    assert items == [] and total == 0 and len(client.calls) == 1
