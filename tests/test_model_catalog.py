"""T-251 模型資訊卡（SPEC-014 §10 AC-251-1～3；裁決 D-122）。

卡片上的數字只能來自 `configs/model_catalog.yaml`，而且必須帶得動口徑與出處；
模型重訓過（指紋不符）時寧可不顯示，也不顯示一塊看不出過期的數字。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, cast

import pytest

from twquant.config import ModelCatalogConfig, load_config
from twquant.errors import ConfigError
from twquant.monitoring import api
from twquant.reporting.platform_artifacts import model_info_projection
from twquant.reporting.recommendation_snapshot import RecommendationSnapshot, save_snapshot

_REPO = Path(__file__).resolve().parent.parent
_FINGERPRINT = "dcfbf21c2d9bf2af"
_OTHER_FINGERPRINT = "7fa35dd6f3638bba"
_NOTE = "IC 勝率＝當天橫斷面 RankIC > 0 的訊號日比例，不是買了會賺的比率（D-061）"


def _catalog(fingerprint: str = _FINGERPRINT) -> ModelCatalogConfig:
    """登記一個模型，欄位齊全——測的是投影，不是配置的預設值。"""
    return ModelCatalogConfig.model_validate(
        {
            "hit_rate_definition": _NOTE,
            "models": {
                "model_a": {
                    "name": "長窗口排序模型",
                    "role": "ranking",
                    "description": "每天把全市場排一次序，預測 20 個交易日後扣成本的超額報酬。",
                    "caveat": "部署的是排序不是策略。",
                    "artifact_fingerprint": fingerprint,
                    "measurements": [
                        {
                            "label": "樣本外",
                            "rank_ic": 0.0661,
                            "hit_rate": 0.86,
                            "n_days": 2047,
                            "period_start": "2017-09-26",
                            "period_end": "2026-02-25",
                            "measured_on": "2026-09-20",
                            "source": "scripts/model_ic_hitrate.py",
                        }
                    ],
                }
            },
        }
    )


def _snapshot(model_id: str, fingerprint: str) -> RecommendationSnapshot:
    return RecommendationSnapshot.model_validate(
        {
            "schema_version": 1,
            "run_id": "run_a",
            "model_id": model_id,
            "as_of": "2026-09-18T20:00:00+08:00",
            "generated_at": "2026-09-19T19:20:14+08:00",
            "published_at": None,
            "mode": "research",
            "artifact_fingerprint": fingerprint,
            "candidates": [
                {
                    "stock_id": "2330",
                    "rank": 1,
                    "score": 0.42,
                    "bucket": "ml_top20",
                    "reason_ref": None,
                    "insight_run_id": None,
                    "insight_available_at": None,
                    "data_status": "pending",
                }
            ],
        }
    )


def _run_info(tmp_path: Path, catalog: ModelCatalogConfig | None) -> dict[str, Any] | None:
    response = api.get_platform_recommendations(tmp_path, model_catalog=catalog)
    runs = cast(list[dict[str, Any]], response["runs"])
    return cast(dict[str, Any] | None, runs[0]["model_info"])


def test_ac251_1_registered_model_card_carries_name_description_and_measured_hit_rate(
    tmp_path: Path,
) -> None:
    save_snapshot(tmp_path, _snapshot("model_a", _FINGERPRINT))

    info = _run_info(tmp_path, _catalog())

    assert info is not None
    assert info["name"] == "長窗口排序模型"
    assert info["role"] == "ranking"
    assert info["description"].startswith("每天把全市場排一次序")
    assert info["caveat"] == "部署的是排序不是策略。"
    # 口徑與數字必須一起送到畫面：86% 沒有這一句會被讀成「買了八成六會賺」。
    assert info["hit_rate_note"] == _NOTE
    assert info["stale"] is False
    measurement = cast(list[dict[str, Any]], info["measurements"])[0]
    assert measurement == {
        "label": "樣本外",
        "rank_ic": 0.0661,
        "hit_rate": 0.86,
        "n_days": 2047,
        "period_start": "2017-09-26",
        "period_end": "2026-02-25",
        "measured_on": "2026-09-20",
        "source": "scripts/model_ic_hitrate.py",
    }


def test_ac251_1_unregistered_model_gets_no_card_and_never_borrows_another_entry(
    tmp_path: Path,
) -> None:
    save_snapshot(tmp_path, _snapshot("model_unregistered", _FINGERPRINT))

    assert _run_info(tmp_path, _catalog()) is None


def test_ac251_2_retrained_model_hides_the_numbers_but_keeps_the_description(
    tmp_path: Path,
) -> None:
    # T-180 的情境：同一個 model_id 重訓過，登記的數字對應的是上一版。
    save_snapshot(tmp_path, _snapshot("model_a", _OTHER_FINGERPRINT))

    info = _run_info(tmp_path, _catalog())

    assert info is not None
    assert info["stale"] is True
    assert info["measurements"] == []
    assert info["name"] == "長窗口排序模型"
    assert info["artifact_fingerprint"] == _FINGERPRINT
    assert info["run_fingerprint"] == _OTHER_FINGERPRINT


def test_ac251_2_projection_is_a_pure_lookup_without_reading_any_artifact() -> None:
    catalog = _catalog()

    fresh = model_info_projection(catalog, "model_a", _FINGERPRINT)
    stale = model_info_projection(catalog, "model_a", _OTHER_FINGERPRINT)

    assert fresh is not None and stale is not None
    assert fresh["stale"] is False and len(cast(list[Any], fresh["measurements"])) == 1
    assert stale["stale"] is True and stale["measurements"] == []
    assert model_info_projection(catalog, "model_b", _FINGERPRINT) is None


@pytest.mark.parametrize(
    ("patch", "expect"),
    [
        ({"hit_rate": 1.5}, "hit_rate"),
        ({"n_days": 0}, "n_days"),
        ({"period_end": "2017-09-25"}, "period_end"),
        ({"measured_on": "2026-02-24"}, "measured_on"),
        ({"bogus": 1}, "bogus"),
    ],
)
def test_ac251_3_impossible_measurements_are_rejected_with_the_field_name(
    tmp_path: Path, patch: dict[str, Any], expect: str
) -> None:
    payload = _catalog().model_dump(mode="json")
    payload["models"]["model_a"]["measurements"][0].update(patch)
    path = tmp_path / "model_catalog.yaml"
    import yaml  # 與 load_config 同一個序列化路徑，不另外手寫 YAML 字串

    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ConfigError, match=expect):
        load_config(path, ModelCatalogConfig)


def test_ac251_3_an_entry_without_measurements_or_a_bad_fingerprint_is_rejected(
    tmp_path: Path,
) -> None:
    import yaml

    empty = _catalog().model_dump(mode="json")
    empty["models"]["model_a"]["measurements"] = []
    bad_fp = _catalog().model_dump(mode="json")
    bad_fp["models"]["model_a"]["artifact_fingerprint"] = "not-a-fingerprint"

    for payload, expect in ((empty, "measurements"), (bad_fp, "artifact_fingerprint")):
        path = tmp_path / "model_catalog.yaml"
        path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
        with pytest.raises(ConfigError, match=expect):
            load_config(path, ModelCatalogConfig)


def test_ac251_3_registered_models_require_the_hit_rate_definition() -> None:
    payload = _catalog().model_dump(mode="json")
    payload["hit_rate_definition"] = ""

    # 有登記模型卻沒有口徑說明＝畫面上會出現一個沒有定義的百分比，這在 D-061 下不可接受。
    with pytest.raises(ValueError, match="hit_rate_definition"):
        ModelCatalogConfig.model_validate(payload)


def test_ac251_3_missing_catalog_leaves_the_runs_intact(tmp_path: Path) -> None:
    save_snapshot(tmp_path, _snapshot("model_a", _FINGERPRINT))

    response = api.get_platform_recommendations(tmp_path, model_catalog=None)
    runs = cast(list[dict[str, Any]], response["runs"])

    assert len(runs) == 1
    assert runs[0]["candidate_total"] == 1  # 名單照常
    assert runs[0]["model_info"] is None  # 少一塊資訊，不是整頁壞掉


def test_ac251_3_real_catalog_matches_the_production_bindings() -> None:
    catalog = load_config(_REPO / "configs" / "model_catalog.yaml", ModelCatalogConfig)

    ranking = catalog.models["lgbm_lw_net_h20_20260225_83890036"]
    assert ranking.role == "ranking"  # D-110 的生產綁定
    assert catalog.hit_rate_definition
    for model_id, entry in catalog.models.items():
        assert entry.measurements, model_id
        for measurement in entry.measurements:
            assert 0.0 <= measurement.hit_rate <= 1.0, model_id
            assert measurement.period_start <= measurement.period_end <= measurement.measured_on
            assert measurement.measured_on <= dt.date(2100, 1, 1)
