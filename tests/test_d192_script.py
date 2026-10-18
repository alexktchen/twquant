"""D-192／T-390：名單內「避開跌」模型的腳本——守登記的常數、純函數的正確性與決定性。離線、不連 PG。"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import numpy as np
import polars as pl
import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "scripts" / "d192_list_loser_model.py"


def _load() -> object:
    spec = importlib.util.spec_from_file_location("d192", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["d192"] = mod
    spec.loader.exec_module(mod)
    return mod


m = _load()


# ── AC-1：登記的定義寫死在腳本裡（D-192 §二～§三） ─────────────────────────────────
def test_ac1_registered_constants() -> None:
    assert (dt.date(2017, 9, 26), dt.date(2026, 9, 8)) == (m.DEV_START, m.DEV_END)  # type: ignore[attr-defined]
    assert (m.HORIZON, m.STOP, m.TOP_K, m.DROP_K) == (20, -0.15, 15, 5)  # type: ignore[attr-defined]
    assert (m.AUC_GATE, m.T_GATE) == (0.02, 2.0)  # type: ignore[attr-defined]
    assert (m.HIT_GATE_PP, m.RET_GATE_PP, m.VS_VOL_PP) == (0.03, 0.005, 0.003)  # type: ignore[attr-defined]
    p = m.LGB_PARAMS  # type: ignore[attr-defined]
    assert (p["num_leaves"], p["learning_rate"], m.NUM_ROUNDS, p["min_data_in_leaf"]) == (  # type: ignore[attr-defined]
        15,
        0.05,
        300,
        50,
    )
    assert p["deterministic"] is True and p["num_threads"] == 1
    assert (p["feature_fraction"], p["bagging_fraction"], p["bagging_freq"], p["seed"]) == (
        0.8,
        0.8,
        1,
        20261018,
    )
    assert m.SEED == 20261018 and m.BOOT == 10_000  # type: ignore[attr-defined]
    assert (dt.date(2019, 7, 1), dt.date(2026, 8, 1)) == (m.FIRST_TEST_MONTH, m.LAST_TEST_MONTH)  # type: ignore[attr-defined]
    assert set(m.MODELS) == {"CORE", "QUIET"}  # type: ignore[attr-defined]
    text = (_ROOT / "DECISIONS.md").read_text(encoding="utf-8")
    assert "### D-192（" in text and "num_leaves 15、learning_rate 0.05、n_estimators 300" in text


# ── AC-2：AUC（Mann-Whitney、平手取半）與月份列舉 ───────────────────────────────────
def test_ac2_auc_and_months() -> None:
    assert m.auc(np.array([0.9, 0.8, 0.2, 0.1]), np.array([1, 1, 0, 0])) == 1.0  # type: ignore[attr-defined]
    assert m.auc(np.array([0.1, 0.2, 0.8, 0.9]), np.array([1, 1, 0, 0])) == 0.0  # type: ignore[attr-defined]
    assert m.auc(np.array([0.5, 0.5, 0.5, 0.5]), np.array([1, 1, 0, 0])) == 0.5  # type: ignore[attr-defined]
    # 配對：(0.9,0.5)=1、(0.9,0.1)=1、(0.5,0.5)=½、(0.5,0.1)=1 → 3.5/4
    assert m.auc(np.array([0.9, 0.5, 0.5, 0.1]), np.array([1, 0, 1, 0])) == pytest.approx(0.875)  # type: ignore[attr-defined]
    assert m.auc(np.array([0.9, 0.8]), np.array([1, 1])) is None  # type: ignore[attr-defined]
    months = m.month_starts()  # type: ignore[attr-defined]
    assert (
        months[0] == dt.date(2019, 7, 1) and months[-1] == dt.date(2026, 8, 1) and len(months) == 86
    )


# ── AC-3：濾網——每天每條線剔掉分數最高的 5 檔；gate1 的平均差與 t ───────────────────
def test_ac3_filter_and_gate1_math() -> None:
    day = dt.date(2026, 1, 5)
    preds = pl.DataFrame(
        {
            "date": [day] * 15,
            "line": ["CORE"] * 15,
            "stock_id": [f"{1000 + i}" for i in range(15)],
            "y": [1] * 5 + [0] * 10,
            "ret": [-0.2] * 5 + [0.02] * 10,
            "price.vol_20d": [0.05] * 5 + [0.01] * 10,
            "p": [0.9] * 5 + [0.1] * 10,
        }
    )
    kept = m._keep_lowest(preds, "p")  # type: ignore[attr-defined]
    assert kept.height == 10 and kept["y"].sum() == 0
    g2 = m.gate2(preds)  # type: ignore[attr-defined]
    assert g2["hit_full"] == pytest.approx(5 / 15) and g2["hit_model_kept"] == 0.0
    assert (
        g2["a_hit_drop"] is True and g2["c_beats_vol"] is False
    )  # vol 濾網一樣完美 → 沒贏過波動度
    W = m.Window  # type: ignore[attr-defined]
    wins = [
        W("m", 10, 0.3, 0.70, 0.65),
        W("m", 10, 0.3, 0.68, 0.65),
        W("m", 10, 0.3, 0.72, 0.66),
        W("m", 10, 0.3, None, 0.6),
    ]
    g1 = m.gate1(wins)  # type: ignore[attr-defined]
    assert g1["n_windows"] == 3 and g1["mean_diff"] == pytest.approx((0.05 + 0.03 + 0.06) / 3)
    assert g1["pass"] is True
    assert m.gate1([W("m", 10, 0.3, 0.7, 0.7)])["pass"] is False  # type: ignore[attr-defined]


# ── AC-4：走動式只用 end_date 早於測試月的列（不偷看），且決定性 ───────────────────────
def test_ac4_walk_forward_is_causal_and_deterministic() -> None:
    rng = np.random.default_rng(1)
    n = 3000
    dates = [
        dt.date(2017, 10, 2) + dt.timedelta(days=int(i)) for i in rng.integers(0, 365 * 3, size=n)
    ]
    x = rng.normal(size=n)
    y = (x + rng.normal(scale=0.5, size=n) > 0.8).astype(int)
    df = pl.DataFrame(
        {
            "date": dates,
            "end_date": [d + dt.timedelta(days=28) for d in dates],
            "stock_id": [f"{2000 + i % 300}" for i in range(n)],
            "line": ["CORE"] * n,
            "y": y,
            "ret": rng.normal(scale=0.05, size=n),
            "price.vol_20d": rng.uniform(0.005, 0.06, size=n),
            "f1": x,
            "score": rng.normal(size=n),
            "rank": rng.integers(1, 16, size=n),
        }
    ).sort("date")
    m.FIRST_TEST_MONTH, m.LAST_TEST_MONTH = dt.date(2019, 7, 1), dt.date(2019, 9, 1)  # type: ignore[attr-defined]
    w1, p1 = m.walk_forward(df, ["f1", "score", "rank"])  # type: ignore[attr-defined]
    _w2, p2 = m.walk_forward(df, ["f1", "score", "rank"])  # type: ignore[attr-defined]
    assert [w.month for w in w1] == ["2019-07", "2019-08", "2019-09"]
    assert all(w.auc_model is not None and w.auc_model > 0.8 for w in w1), (
        "特徵有訊號時模型要學得到"
    )
    assert p1.equals(p2), "同一份資料跑兩次要 bit-identical"
    # 每個測試列的訓練集只含 end_date 早於該月第一天的列（靜態：讀程式碼）
    src = _SCRIPT.read_text(encoding="utf-8")
    assert 'train = data.filter(pl.col("end_date") < m0)' in src
