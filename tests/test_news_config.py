"""T-400／D-200：新聞服務的設定只有一份來源（configs/news.yaml），Edge Function 讀的 JSON 鏡像必須一模一樣。"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent


def test_ac_config_mirror_matches_yaml() -> None:
    src = yaml.safe_load((_ROOT / "configs" / "news.yaml").read_text(encoding="utf-8"))
    mirror = json.loads(
        (_ROOT / "news-service" / "config" / "news.json").read_text(encoding="utf-8")
    )
    assert mirror == src, "改了 configs/news.yaml 要同步改 news-service/config/news.json"


def test_ac_sources_record_terms_and_check_date() -> None:
    src = yaml.safe_load((_ROOT / "configs" / "news.yaml").read_text(encoding="utf-8"))
    ids = [s["id"] for s in src["sources"]]
    assert len(ids) == len(set(ids))
    for s in src["sources"]:
        assert s["terms"].startswith("https://"), s["id"]
        assert len(s["checked"]) == 10, s["id"]
