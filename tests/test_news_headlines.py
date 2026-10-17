"""晨報的新聞頭條（2026-10-17，使用者：「也加上昨晚國際金融大事、新聞頭條」）：鉅亨網列表。錄下的回應，不連網。"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from twquant.config import SystemConfig, load_config
from twquant.conventions import TZ
from twquant.data.sources import news_headlines as nh
from twquant.errors import DataError

_ROOT = Path(__file__).resolve().parent.parent
_FIX = _ROOT / "tests" / "fixtures" / "http" / "news" / "cnyes_headline.json"


def _body() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(_FIX.read_text(encoding="utf-8"))
    return payload


# ── AC-1：列表解析——標題、摘要去 HTML 實體、台北時間、原文網址 ─────────────────────
def test_ac1_parse_headlines() -> None:
    out = nh.parse(_body(), "頭條")
    assert len(out) == 6
    h = out[0]
    assert h.news_id == 6632593
    assert h.title.startswith("外資持有美債規模8月回升")
    assert h.published_at == dt.datetime(2026, 10, 17, 17, 0, 2, tzinfo=TZ)
    assert h.url == "https://news.cnyes.com/news/id/6632593"
    assert (h.source, h.category) == ("鉅亨網", "美股雷達")
    assert h.summary is not None and "&#10;" not in h.summary
    assert out[2].summary is None  # 空摘要 → None，不是空字串


# ── AC-2：時間窗、去重、新到舊、上限 ─────────────────────────────────────────
def test_ac2_window_dedupes_and_keeps_newest_within_range() -> None:
    items = nh.parse(_body(), "頭條")
    since = dt.datetime(2026, 10, 17, 14, 30, tzinfo=TZ)
    until = dt.datetime(2026, 10, 17, 16, 30, tzinfo=TZ)
    got = nh.window(items + items[:3], since, until, limit=10)
    assert [h.news_id for h in got] == [6632622, 6632662, 6632691, 6632625]
    assert len(nh.window(items, since, until, limit=2)) == 2


# ── AC-3：壞回應 raise，不默默給空清單 ─────────────────────────────────────────
def test_ac3_bad_responses_raise() -> None:
    with pytest.raises(DataError, match="回應異常"):
        nh.parse({"statusCode": 500, "message": "busy"}, "頭條")
    with pytest.raises(DataError, match=r"items.data"):
        nh.parse({"statusCode": 200, "items": {}}, "頭條")
    body = _body()
    del body["items"]["data"][0]["publishAt"]
    with pytest.raises(DataError, match="缺標題"):
        nh.parse(body, "頭條")


# ── AC-4：設定——兩個分類、從前一天收盤後起算、限速 ─────────────────────────────
def test_ac4_config_feeds_and_rate() -> None:
    cfg = load_config(_ROOT / "configs" / "system.yaml", SystemConfig)
    mb = cfg.morning_brief
    assert [(f.category, f.label) for f in mb.news_feeds] == [
        ("headline", "頭條"),
        ("wd_stock", "國際股"),
    ]
    assert mb.news_since_hour == 14 and mb.headline_limit >= 1
    assert "cnyes" in cfg.source_rates
