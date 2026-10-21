"""T-413：新聞情緒的純函式——分層抽樣、提示詞、回應解析、探針訓練與評估（離線、決定性）。"""

from __future__ import annotations

import numpy as np
import pytest

from twquant.data import news_sentiment as ns
from twquant.errors import DataError


def test_stratified_sample_round_robin_and_deterministic() -> None:
    pairs = [ns.Pair(f"i{k}", "2330", "2021|cnyes") for k in range(10)] + [
        ns.Pair(f"j{k}", "2317", "2022|mops") for k in range(3)
    ]
    a = ns.stratified_sample(pairs, 8, seed=1)
    b = ns.stratified_sample(pairs, 8, seed=1)
    assert a == b and len(a) == 8
    # 兩層輪流抽：小的那層 3 對全進、大的那層補到 8
    assert sum(p.stratum == "2022|mops" for p in a) == 3
    assert len(ns.stratified_sample(pairs, 100, seed=1)) == 13
    with pytest.raises(ValueError):
        ns.stratified_sample(pairs, 0, seed=1)


def test_prompt_and_parse() -> None:
    p = ns.build_prompt("台積電", "2330", "台積電上調財測", "內文" * 100, max_body=10)
    assert "台積電（2330）" in p and p.endswith("內文：內文內文內文內文內文")
    assert ns.parse_label('{"label": "好"}') == 1
    assert ns.parse_label('<think>嗯</think>\n{"label":"壞"}') == -1
    assert ns.parse_label(' {"label": "中性"} ') == 0
    assert ns.parse_label("利空") == -1
    with pytest.raises(DataError, match="三類"):
        ns.parse_label('{"label": "不知道"}')
    with pytest.raises(DataError):
        ns.parse_label("")


def _toy(n: int = 600, seed: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """三團線性可分的向量：類別方向 + 雜訊。"""
    rng = np.random.default_rng(seed)
    dirs = np.eye(8)[:3] * 3
    y = rng.integers(0, 3, n)
    x = dirs[y] + rng.normal(0, 0.8, (n, 8))
    return x, np.array([(-1, 0, 1)[i] for i in y])


def test_probe_learns_separable_data_and_gate_is_applied() -> None:
    x, y = _toy()
    tr, va = ns.split_train_valid(len(x), 0.25, seed=7)
    probe = ns.train_probe(x[tr], y[tr], epochs=200)
    ev = ns.evaluate_probe(probe, x[va], y[va], f1_min=0.70, auc_min=0.85, n_train=len(tr))
    assert ev.macro_f1 > 0.9 and ev.pos_neg_auc > 0.95 and ev.passed and ev.n_valid == len(va)
    # 同資料同 seed → 同結果（決定性）
    assert np.allclose(ns.train_probe(x[tr], y[tr], epochs=200).w, probe.w)
    # 門檻是事前寫死的：拉高就不過（不是 probe 變了）
    assert not ns.evaluate_probe(
        probe, x[va], y[va], f1_min=0.999, auc_min=0.85, n_train=len(tr)
    ).passed


def test_metrics_edge_cases() -> None:
    assert ns.macro_f1(np.array([1, 1, -1]), np.array([1, 1, -1])) == 1.0
    assert ns.auc(np.array([0.9, 0.8, 0.1, 0.2]), np.array([True, True, False, False])) == 1.0
    with pytest.raises(DataError):
        ns.auc(np.array([0.1, 0.2]), np.array([True, True]))
    with pytest.raises(ValueError):
        ns.train_probe(np.zeros((0, 2)), np.zeros(0))
