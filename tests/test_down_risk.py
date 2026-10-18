"""易跌標記（T-391／D-193）：D-192 模型對當天大盤精選打機率、標 5 檔。離線：用小型合成模型。"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pytest

from twquant.config import ServingConfig, load_config
from twquant.conventions import TZ
from twquant.data.serving import schema as sch
from twquant.errors import DataError
from twquant.ml import down_risk as dr

_ROOT = Path(__file__).resolve().parent.parent
DAY = dt.date(2026, 10, 16)
AT = dt.datetime(2026, 10, 16, 18, 0, tzinfo=TZ)
FEATS = ["price.vol_20d", "price.mom_20d", "score", "rank"]


def _model_dir(tmp_path: Path) -> Path:
    """vol 越高越危險的合成模型（deterministic）。"""
    rng = np.random.default_rng(0)
    x = np.column_stack(
        [
            rng.uniform(0, 0.08, 400),
            rng.normal(size=400),
            rng.normal(size=400),
            rng.integers(1, 16, 400),
        ]
    )
    y = (x[:, 0] + rng.normal(scale=0.01, size=400) > 0.04).astype(int)
    booster = lgb.train(
        {
            "objective": "binary",
            "num_leaves": 7,
            "learning_rate": 0.1,
            "min_data_in_leaf": 10,
            "seed": 1,
            "verbose": -1,
            "deterministic": True,
            "num_threads": 1,
        },
        lgb.Dataset(x, label=y),
        num_boost_round=30,
    )
    d = tmp_path / "lgbm_d192_core_down15_20261018"
    d.mkdir()
    booster.save_model(str(d / "model.txt"))
    (d / "meta.json").write_text(
        json.dumps(
            {"model_id": "lgbm_d192_core_down15_20261018", "features": FEATS, "flag_top_k": 5}
        ),
        encoding="utf-8",
    )
    return d


# ── AC-1：特徵矩陣照 meta 順序、score／rank 來自名單、缺值 NaN、整欄缺 raise ──────────────
def test_ac1_feature_matrix_order_and_missing() -> None:
    picks: list[tuple[str, int, float | None]] = [("2330", 1, 0.9), ("2317", 2, None)]
    raw: dict[tuple[str, str], tuple[float | None, float | None]] = {
        ("2330", "price.vol_20d"): (0.02, 0.5),
        ("2330", "price.mom_20d"): (0.1, 0.7),
        ("2317", "price.vol_20d"): (0.05, 0.9),
    }
    x, missing = dr.feature_matrix(picks, raw, FEATS)
    assert x.shape == (2, 4) and missing == []
    assert x[0].tolist() == [0.02, 0.1, 0.9, 1.0]
    assert x[1, 0] == 0.05 and np.isnan(x[1, 1]) and np.isnan(x[1, 2]) and x[1, 3] == 2.0
    # 一個因子整欄缺（兩個因子裡的一個，不超過一半）→ 回報不 raise；型態類稀疏因子就是這樣
    x2, missing2 = dr.feature_matrix(picks, {("2330", "price.vol_20d"): (0.02, 0.5)}, FEATS)
    assert missing2 == ["price.mom_20d"] and np.isnan(x2[:, 1]).all()
    # 超過一半的因子整欄缺（兩個都缺）→ 物化沒到齊 → raise；但若都是當天全市場沒算的（structural）就不算
    with pytest.raises(DataError, match="物化沒到齊"):
        dr.feature_matrix(picks, {}, FEATS)
    x3, missing3 = dr.feature_matrix(picks, {}, FEATS, {"price.vol_20d", "price.mom_20d"})
    assert sorted(missing3) == ["price.mom_20d", "price.vol_20d"] and np.isnan(x3[:, :2]).all()


# ── AC-2：打分——每檔一列、機率最高 5 檔 flagged、同輸入兩次 bit-identical、與 PG spec 同欄 ───
def test_ac2_score_picks_flags_top_k_deterministically(tmp_path: Path) -> None:
    model = dr.load_model(_model_dir(tmp_path))
    assert (model.model_id, model.flag_top_k, model.features) == (
        "lgbm_d192_core_down15_20261018",
        5,
        FEATS,
    )
    picks: list[tuple[str, int, float | None]] = [
        (f"{2000 + i}", i + 1, 0.5 - i * 0.01) for i in range(15)
    ]
    vols = [
        0.07,
        0.065,
        0.06,
        0.055,
        0.05,
        0.01,
        0.012,
        0.011,
        0.013,
        0.014,
        0.015,
        0.016,
        0.017,
        0.018,
        0.019,
    ]
    raw: dict[tuple[str, str], tuple[float | None, float | None]] = {}
    for (sid, _, _), v in zip(picks, vols, strict=True):
        raw[(sid, "price.vol_20d")] = (v, None)
        raw[(sid, "price.mom_20d")] = (0.0, None)
    a, missing = dr.score_picks(DAY, picks, raw, model, AT)
    b, _ = dr.score_picks(DAY, picks, raw, model, AT)
    assert a.equals(b) and missing == []
    assert list(a.columns) == list(dr.SCHEMA) == list(sch.PICK_DOWN_RISK_SPEC.column_names)
    assert a.height == 15 and a["flagged"].sum() == 5 and set(a["line"].to_list()) == {"CORE"}
    assert set(a.filter(a["flagged"])["stock_id"].to_list()) == {
        "2000",
        "2001",
        "2002",
        "2003",
        "2004",
    }  # 高波動那 5 檔
    assert a["p"].is_between(0.0, 1.0).all()
    assert dr.score_picks(DAY, [], raw, model, AT)[0].is_empty()
    with pytest.raises(ValueError, match="時區"):
        dr.score_picks(DAY, picks, raw, model, dt.datetime(2026, 10, 16))


# ── AC-3：每日 pipeline 地端雲端都跑「易跌標記」，排在名單理由之後；模型檔缺席不 raise ────────
def test_ac3_pipeline_wiring_and_absent_model_is_reported_not_raised() -> None:
    source = (_ROOT / "src" / "twquant" / "cli.py").read_text(encoding="utf-8")
    assert 'PipelineStep("易跌標記"' in source
    assert source.index('PipelineStep("名單理由"') < source.index('PipelineStep("易跌標記"')
    assert '@app.command(name="down-risk")' in source
    assert 'DOWN_RISK_MODEL_ID = "lgbm_d192_core_down15_20261018"' in source
    assert "模型檔缺席" in source
    scfg = load_config(_ROOT / "configs" / "serving.yaml", ServingConfig)
    assert "易跌標記" not in scfg.cloud.skip_pipeline_steps
    assert "pick_down_risk" not in scfg.cloud.windows  # 15 列／天，全史保留
    ml = (_ROOT / "configs" / "ml.yaml").read_text(encoding="utf-8")
    assert "lgbm_d192_core_down15" not in ml  # D-193：不進生產模型清單
