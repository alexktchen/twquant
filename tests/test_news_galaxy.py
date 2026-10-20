"""T-418：新聞全景（星雲圖）快照腳本的純函式——設定、群名清理、分群、t-SNE、整包組裝（不連資料庫、不連網）。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "scripts" / "news_galaxy.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("news_galaxy", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["news_galaxy"] = mod
    spec.loader.exec_module(mod)
    return mod


g: Any = _load()


def _cfg(**over: Any) -> Any:
    base = {
        "days": 7,
        "max_items": 1500,
        "k": 3,
        "island": 0.35,
        "perplexity": 5,
        "iters": 250,
        "hub_min": 2,
        "keep": 3,
        "seed": 7,
    }
    base.update(over)
    return g.load_config({"galaxy": base})


def _blobs(n_per: int = 12, dim: int = 16, seed: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """三團清楚分開的向量（每團一個方向＋小雜訊）。"""
    r = np.random.default_rng(seed)
    centers = np.eye(dim)[:3] * 5
    x = np.vstack([c + r.normal(0, 0.3, (n_per, dim)) for c in centers])
    return x, np.repeat(np.arange(3), n_per)


def test_config_in_repo_loads_and_bad_values_raise() -> None:
    doc = yaml.safe_load((_ROOT / "configs" / "news.yaml").read_text(encoding="utf-8"))
    cfg = g.load_config(doc)
    assert cfg.max_items >= 100 and cfg.k >= 2
    with pytest.raises(g.GalaxyError, match="galaxy"):
        g.load_config({})
    with pytest.raises(g.GalaxyError):
        _cfg(k=0)
    with pytest.raises(g.GalaxyError):
        g.load_config({"galaxy": {"days": 7}})


def test_clean_name_strips_think_quotes_and_extra_words() -> None:
    assert g.clean_name("記憶體漲價") == "記憶體漲價"
    assert g.clean_name("<think>\n\n</think>\n\n「聯準會利率」") == "聯準會利率"
    assert g.clean_name("法國動亂 法債暴跌") == "法國動亂"
    assert g.clean_name("非常非常長的主題名稱超過十個字") == "非常非常長的主題名稱"
    assert g.clean_name("  。 ") is None


def test_kmeans_separates_clear_blobs_and_is_deterministic() -> None:
    x, truth = _blobs()
    x = g.normalize(x)
    a = g.kmeans(x, 3, seed=7)
    b = g.kmeans(x, 3, seed=7)
    assert (a == b).all()
    # 每個真實群只對到一個 k-means 群
    for t in range(3):
        assert len(set(a[truth == t].tolist())) == 1


def test_tsne_shape_scale_and_groups_stay_together() -> None:
    x, truth = _blobs()
    x = g.normalize(x)
    y = g.tsne(x, truth, _cfg())
    assert y.shape == (36, 3)
    assert np.linalg.norm(y, axis=1).max() <= 1.15 + 1e-9
    # 同群的平均距離比不同群的小
    d = np.linalg.norm(y[:, None] - y[None, :], axis=2)
    same = d[truth[:, None] == truth[None, :]].mean()
    diff = d[truth[:, None] != truth[None, :]].mean()
    assert same < diff * 0.6
    with pytest.raises(g.GalaxyError, match="太少"):
        g.tsne(x[:3], truth[:3], _cfg())


def test_build_snapshot_names_hubs_and_fallback() -> None:
    x, truth = _blobs()
    items = [
        g.Item(
            f"i{j}",
            "cnyes-headline" if j % 2 else "bloomberg-tech",
            f"https://x/{j}",
            f"2026-10-20T0{j % 10}:00:00+00:00",
            f"title {j}",
            f"標題 {j}" if j % 2 else None,
            f"e{j // 4}",
        )
        for j in range(len(x))
    ]
    mentions = {
        f"i{j}": (["tw:2330"] if truth[j] == 0 else ["us:NVDA", "tw:2330"] if truth[j] == 1 else [])
        for j in range(len(x))
    }
    mentions["i30"] = ["th:oil"]  # 只出現一次：不到 hub_min，不是亮點
    entities = {
        "tw:2330": {"label": "台積電", "type": "tw", "sub": "2330"},
        "us:NVDA": {"label": "輝達", "type": "us", "sub": "NVDA"},
        "th:oil": {"label": "油價", "type": "theme", "sub": "主題"},
    }
    sources = {
        "cnyes-headline": {"name": "鉅亨網", "kind": "tw"},
        "bloomberg-tech": {"name": "Bloomberg", "kind": "intl"},
    }
    asked: list[list[str]] = []

    def namer(titles: list[str], avoid: list[str]) -> str | None:
        asked.append(titles)
        return None if len(asked) == 1 else f"群{len(asked)}"

    snap = g.build_snapshot(
        items, x, mentions, entities, sources, _cfg(), namer, "2026-10-20T12:00:00Z"
    )
    assert len(snap["items"]) == 36 and len(snap["clusters"]) == 3 and len(asked) == 3
    names = [c["name"] for c in snap["clusters"]]
    # 取名失敗 → 用群裡最常被提到的實體（哪一群排第一看 k-means 的編號，三群各自最常提到的是台積電、台積電／輝達、油價）
    assert names[0] in {"台積電", "輝達", "油價"}, names
    assert names[1:] == ["群2", "群3"]
    assert [h["label"] for h in snap["hubs"]] == ["台積電", "輝達"]
    assert snap["hubs"][0]["n"] == 24
    it = snap["items"][1]
    assert (
        it["title"] == "標題 1"
        and it["tr"] is True
        and it["src"] == "鉅亨網"
        and it["kind"] == "tw"
    )
    assert snap["items"][30]["m"] == [] and snap["items"][30]["ml"] == ["油價"]
    assert snap["links"] == sum(len(i["m"]) for i in snap["items"])
    with pytest.raises(g.GalaxyError, match="對不起來"):
        g.build_snapshot(items[:5], x, mentions, entities, sources, _cfg(), namer, "t")


def test_build_snapshot_retries_duplicate_names_then_falls_back() -> None:
    """撞名（2026-10-20 實見三群都叫「AI」）：帶著已用的名字再問一次；還是撞就接上最常提到的實體，絕不重複。"""
    x, truth = _blobs()
    items = [
        g.Item(f"i{j}", "cnyes-headline", "u", "2026-10-20T00:00:00+00:00", f"t{j}", None, None)
        for j in range(len(x))
    ]
    mentions = {f"i{j}": [["tw:2330"], ["us:NVDA"], ["th:oil"]][truth[j]] for j in range(len(x))}
    entities = {
        "tw:2330": {"label": "台積電", "type": "tw", "sub": "2330"},
        "us:NVDA": {"label": "輝達", "type": "us", "sub": "NVDA"},
        "th:oil": {"label": "油價", "type": "theme", "sub": "主題"},
    }
    avoids: list[list[str]] = []

    def stubborn(titles: list[str], avoid: list[str]) -> str | None:
        avoids.append(avoid)
        return "AI"

    snap = g.build_snapshot(items, x, mentions, entities, {}, _cfg(), stubborn, "t")
    names = [c["name"] for c in snap["clusters"]]
    assert len(set(names)) == 3 and names[0] == "AI"
    assert all(n.startswith("AI・") and n != "AI・AI" for n in names[1:]), names
    # 接的是群裡最常提到、名字裡沒有的（公司優先）
    assert set(names[1:]) <= {"AI・台積電", "AI・輝達", "AI・油價"}, names
    assert avoids[1:3] == [[], ["AI"]], "第二群第一次不帶、撞名後帶著已用過的名字再問"
