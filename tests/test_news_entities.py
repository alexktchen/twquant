"""T-402／T-404：新聞實體（configs/news_entities.yaml）與草擬關係線（configs/news_edges.yaml）的格式核對。

別名比對規則在 news-service/src/mention.ts（SPEC-017 §6）：label 也算別名、stopwords 整條不建索引、
拉丁別名大小寫要一樣；全大寫 2～5 字當 ticker，其他拉丁別名至少 mention.latin_min 字元。
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_CJK = re.compile(r"[㐀-鿿豈-﫿]")
_TICKER_ALIAS = re.compile(r"^[A-Z]{2,5}$")
_TICKER = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")
_LOWER_ID = re.compile(r"^[a-z][a-z0-9]*$")
_TW_CODE = re.compile(r"^\d{4,6}$")
_STATUSES = {"draft", "approved"}
_PEER_LABELS = {"同業", "結盟"}


def _load(name: str) -> Any:
    return yaml.safe_load((_ROOT / "configs" / name).read_text(encoding="utf-8"))


def _mention_cfg() -> dict[str, Any]:
    cfg: dict[str, Any] = _load("news.yaml")["mention"]
    return cfg


def _entities() -> list[tuple[str, dict[str, Any]]]:
    """(實體 id, 原始列)：us:<ticker>／co:<id>／th:<id>，跟 scripts/news_sync_entities.py 寫進新聞庫的 id 一致。"""
    src = _load("news_entities.yaml")
    out = [(f"us:{r['ticker']}", r) for r in src["us"]]
    out += [(f"co:{r['id']}", r) for r in src["companies"]]
    out += [(f"th:{r['id']}", r) for r in src["themes"]]
    return out


def test_ac_both_yaml_load_with_expected_sections() -> None:
    ent = _load("news_entities.yaml")
    edges = _load("news_edges.yaml")
    assert set(ent) == {"us", "companies", "themes"}
    assert set(edges) == {"supplier", "theme", "peer"}
    for key in ("us", "companies", "themes"):
        assert ent[key], f"news_entities.yaml 的 {key} 是空的"
        for r in ent[key]:
            assert isinstance(r["label"], str) and r["label"], r
            assert isinstance(r["aliases"], list), r
    for r in ent["companies"]:
        assert isinstance(r["country"], str) and r["country"], r


def test_ac_ids_and_tickers_unique_and_well_formed() -> None:
    ent = _load("news_entities.yaml")
    tickers = [r["ticker"] for r in ent["us"]]
    for t in tickers:
        # YAML 1.1 會把沒加引號的 ON／NO 讀成布林
        assert isinstance(t, str) and _TICKER.match(t), f"ticker 格式不對：{t!r}"
    assert len(tickers) == len(set(tickers)), "us 有重複的 ticker"
    for key in ("companies", "themes"):
        ids = [r["id"] for r in ent[key]]
        for i in ids:
            assert isinstance(i, str) and _LOWER_ID.match(i), f"{key} 的 id 要小寫英文：{i!r}"
        assert len(ids) == len(set(ids)), f"{key} 有重複的 id"
    all_ids = [eid for eid, _ in _entities()]
    assert len(all_ids) == len(set(all_ids))


def test_ac_every_alias_long_enough_and_not_stopword() -> None:
    cfg = _mention_cfg()
    stop = {str(s) for s in cfg["stopwords"]}
    for eid, r in _entities():
        for a in [r["label"], *r["aliases"]]:
            assert isinstance(a, str), f"{eid} 的別名不是字串：{a!r}"
            assert a == a.strip() and "\n" not in a, f"{eid} 的別名前後有空白：{a!r}"
            assert len(a) >= 2, f"{eid} 的別名太短：{a!r}"
            if _CJK.search(a):
                assert len(a) >= cfg["cjk_min"], f"{eid} 的中文別名太短：{a!r}"
            elif not _TICKER_ALIAS.match(a):
                assert len(a) >= cfg["latin_min"], f"{eid} 的拉丁別名太短：{a!r}"
            assert a not in stop, f"{eid} 的別名 {a!r} 在 stopwords 裡，不會建索引"
        assert len(r["aliases"]) == len(set(r["aliases"])), f"{eid} 的別名重複"


def test_ac_no_alias_points_to_two_entities() -> None:
    owners: dict[str, set[str]] = defaultdict(set)
    for eid, r in _entities():
        for a in [r["label"], *r["aliases"]]:
            owners[a].add(eid)
    clash = {a: sorted(ids) for a, ids in owners.items() if len(ids) > 1}
    assert not clash, f"同一條別名指到不同實體：{clash}"


def test_ac_edges_tw_codes_are_digit_strings() -> None:
    edges = _load("news_edges.yaml")
    for kind, rows in edges.items():
        for r in rows:
            for code in r.get("tw", []):
                assert isinstance(code, str) and _TW_CODE.match(code), (
                    f"{kind} {r['src']} 的台股代號要是 4～6 碼數字字串：{code!r}"
                )
            assert len(r.get("tw", [])) == len(set(r.get("tw", []))), f"{kind} {r['src']} 代號重複"


def test_ac_edges_status_only_draft_or_approved() -> None:
    edges = _load("news_edges.yaml")
    for kind, rows in edges.items():
        for r in rows:
            assert r["status"] in _STATUSES, f"{kind} {r['src']} 的 status 不對：{r['status']!r}"


def test_ac_edges_reference_defined_entities() -> None:
    known = {eid for eid, _ in _entities()}
    edges = _load("news_edges.yaml")
    seen: set[tuple[str, str, str]] = set()
    for kind, rows in edges.items():
        for r in rows:
            src = r["src"]
            assert src in known, f"{kind} 的 src {src!r} 不在 news_entities.yaml"
            if kind == "theme":
                assert src.startswith("th:"), src
                assert r.get("grp") or r.get("tw"), f"{src} 沒有族群也沒有台股"
                for g in r.get("grp", []):
                    assert isinstance(g, str) and g.strip() == g and g, f"{src} 的族群名不對：{g!r}"
            else:
                assert src.startswith(("us:", "co:")), f"{kind} 的 src 要是國外公司：{src}"
                assert r["tw"], f"{kind} {src} 沒有台股"
            if kind == "peer":
                assert r["label"] in _PEER_LABELS, f"{src} 的 label 要是同業或結盟：{r['label']!r}"
            dsts = [f"tw:{c}" for c in r.get("tw", [])] + [f"grp:{g}" for g in r.get("grp", [])]
            for dst in dsts:
                key = (src, dst, kind)
                assert key not in seen, f"重複的線：{key}"
                seen.add(key)
