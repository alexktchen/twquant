"""台股英文名（configs/news_tw_en.yaml，2026-10-20）：每條別名要過別名比對的規則、不撞停用詞、不撞美股／國外公司／主題的別名、不跨兩檔。"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_CJK = re.compile(r"[\u3400-\u9fff]")
# 全大寫 2～5 字（可帶數字）當 ticker 看，不受 latin_min 限制（同 news-service/src/mention.ts）
_TICKER_ALIAS = re.compile(r"^[A-Z][A-Z0-9]{1,4}$")


def _load(name: str) -> Any:
    return yaml.safe_load((_ROOT / "configs" / name).read_text(encoding="utf-8"))


def test_ac_tw_en_aliases_follow_mention_rules() -> None:
    doc: dict[str, list[str]] = _load("news_tw_en.yaml")
    cfg = _load("news.yaml")["mention"]
    stop = {str(s) for s in cfg["stopwords"]}
    assert len(doc) >= 50
    for name, aliases in doc.items():
        assert isinstance(aliases, list) and aliases, name
        for a in aliases:
            assert isinstance(a, str) and a == a.strip(), f"{name}：{a!r}"
            assert not _CJK.search(a), f"{name} 的英文名不能有中文（中文名由主庫股名負責）：{a!r}"
            if not _TICKER_ALIAS.match(a):
                assert len(a) >= cfg["latin_min"], f"{name} 的英文名太短：{a!r}"
            assert a not in stop, f"{name} 的英文名 {a!r} 在 stopwords 裡，不會建索引"


def test_ac_tw_en_no_clash_with_other_entities_or_each_other() -> None:
    doc: dict[str, list[str]] = _load("news_tw_en.yaml")
    ent = _load("news_entities.yaml")
    others: set[str] = set()
    for key in ("us", "companies", "themes"):
        for r in ent[key]:
            others.update([r["label"], *r["aliases"]])
    owners: dict[str, set[str]] = defaultdict(set)
    for name, aliases in doc.items():
        for a in aliases:
            owners[a].add(name)
            assert a not in others, f"{name} 的英文名 {a!r} 已經是美股／國外公司／主題的別名"
    clash = {a: sorted(n) for a, n in owners.items() if len(n) > 1}
    assert not clash, f"同一個英文名給了兩檔：{clash}"
