"""T-424／D-205：雲端可用的新聞情緒小模型——純函式（文字規則、按新聞切、特徵、訓練決定性、匯出）。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from twquant.data import sentiment_mlp as sm

_ROOT = Path(__file__).resolve().parents[1]


def test_pair_text_uses_evidence_else_title() -> None:
    assert sm.pair_text("台積電", "  台積電上調財測 ", "標題") == "台積電：台積電上調財測"
    assert sm.pair_text("台積電", None, "法說會重點") == "台積電：法說會重點"
    assert sm.pair_text("台積電", "  ", "法說會重點") == "台積電：法說會重點"


def test_valid_split_is_by_news_item_and_stable() -> None:
    ids = [f"cnyes:{i}" for i in range(4000)]
    v = [sm.in_valid(i, 0.2) for i in ids]
    assert 0.17 < sum(v) / len(v) < 0.23
    assert v == [sm.in_valid(i, 0.2) for i in ids], "同一則新聞每次都落同一邊"
    assert sm.in_valid("x", 0.0) is False and sm.in_valid("x", 1.0001) is True


def test_features_shape_and_mismatch() -> None:
    a = np.ones((3, 4), np.float32)
    b = np.full((3, 4), 2.0, np.float32)
    x = sm.features(a, b)
    assert x.shape == (3, 12) and float(x[0, 8]) == 2.0
    with pytest.raises(ValueError, match="形狀"):
        sm.features(a, b[:2])


def _toy(n: int, seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """同一則「新聞」對不同股票好壞相反：答案取決於 v_news 與 v_pair 的乘積，只看 v_news 學不到。"""
    rng = np.random.default_rng(seed)
    vn = rng.standard_normal((n, 8)).astype(np.float32)
    vp = rng.standard_normal((n, 8)).astype(np.float32)
    s = (vn[:, 0] * vp[:, 0]).astype(np.float64)
    yi = np.where(s > 0.5, 2, np.where(s < -0.5, 0, 1))
    return vn.astype(np.float16), vp.astype(np.float16), yi


def test_ac_mlp_learns_stock_dependent_label_and_is_deterministic() -> None:
    vn, vp, yi = _toy(4000, 1)
    m1 = sm.train_mlp(vn, vp, yi, 3, hidden=32, l2=1e-4, lr=3e-3, batch=128, epochs=20, seed=7)
    m2 = sm.train_mlp(vn, vp, yi, 3, hidden=32, l2=1e-4, lr=3e-3, batch=128, epochs=20, seed=7)
    assert np.array_equal(m1.w1, m2.w1) and np.array_equal(m1.b2, m2.b2), (
        "同輸入同 seed 結果 bit 一樣"
    )
    tv, tp, ty = _toy(1000, 2)
    acc = float((sm.predict_proba(m1, tv, tp, 256).argmax(axis=1) == ty).mean())
    assert acc > 0.8
    p = sm.predict_proba(m1, tv[:5], tp[:5], 2)
    assert p.shape == (5, 3) and np.allclose(p.sum(axis=1), 1.0, atol=1e-5)


def test_export_weights_and_confusion() -> None:
    vn, vp, yi = _toy(300, 3)
    m = sm.train_mlp(vn, vp, yi, 3, hidden=4, l2=0.0, lr=1e-2, batch=64, epochs=1, seed=1)
    w = sm.export_weights(m, version="v", metrics={"n": 300}, decimals=3)
    assert (
        len(w["w1"]) == 24
        and len(w["w1"][0]) == 4
        and len(w["w2"]) == 4
        and w["classes"] == ["neg", "neu", "pos"]
    )
    json.dumps(w)
    assert sm.confusion([-1, 0, 1, 1], [-1, 1, 1, 0]) == [[1, 0, 0], [0, 0, 1], [0, 1, 1]]


def test_ac_registered_settings_match_d205() -> None:
    """D-205 §二、§三 的數字（事前登記）跟設定檔一樣；要改就是違反登記。"""
    mc = yaml.safe_load((_ROOT / "configs" / "news.yaml").read_text(encoding="utf-8"))["hist"][
        "sentiment"
    ]["mlp"]
    assert (mc["hidden"], mc["l2"], mc["lr"], mc["batch"], mc["epochs"], mc["seed"]) == (
        256,
        0.0001,
        0.001,
        512,
        8,
        7,
    )
    assert (mc["valid_share"], mc["gate_f1"], mc["gate_auc"]) == (0.2, 0.70, 0.85)
