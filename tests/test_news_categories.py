"""T-378／D-188：重大訊息分類表——configs 的正本與 Pages Functions 的鏡像必須相同。"""

from __future__ import annotations

import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _mirror() -> list[dict[str, object]]:
    ts = (_ROOT / "frontend" / "functions" / "_lib" / "news-categories.ts").read_text(
        encoding="utf-8"
    )
    m = re.search(r"export const NEWS_CATEGORIES = (\[.*?\]) as const", ts, re.S)
    assert m, "找不到 NEWS_CATEGORIES 的 JSON 字面值"
    mirror: list[dict[str, object]] = json.loads(m.group(1))
    return mirror


def test_ac1_mirror_equals_config() -> None:
    cfg = json.loads((_ROOT / "configs" / "news_categories.json").read_text(encoding="utf-8"))
    assert cfg["categories"] == _mirror()


def test_ac1_categories_are_well_formed() -> None:
    cfg = json.loads((_ROOT / "configs" / "news_categories.json").read_text(encoding="utf-8"))
    ids = [c["id"] for c in cfg["categories"]]
    assert len(ids) == len(set(ids))
    for c in cfg["categories"]:
        assert c["keywords"] and all(k.strip() == k and k for k in c["keywords"])
        assert isinstance(c["market_wide"], bool) and c["label"]
    assert any(c["market_wide"] for c in cfg["categories"]) and any(
        not c["market_wide"] for c in cfg["categories"]
    )
