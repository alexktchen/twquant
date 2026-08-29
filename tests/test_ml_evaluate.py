"""T-069：評估階梯純函數（SPEC-010 §7，AC-9）。

契約（由本測試凍結）：
- `daily_rank_ic(scored, score_col, label_col)`：逐日 spearman，回傳 (date, ic, n)；
  label null 列剔除、單一樣本日 ic 為 null（不入統計、不 raise）。
- `ic_summary(daily_ic, nw_lag)`：mean/std/ir/hit_rate/t_naive/**t_newey_west**。
  重疊標籤（h 日 horizon）使逐日 IC 自相關，樸素 t 高估——NW 校正為誠實版本，
  nw_lag 應設為 horizon。
- `decile_table(scored, ...)`：等分位分組 → 各組平均 label 與筆數；
  `spread`（D_top − D_bottom）與 `is_monotonic`（嚴格遞增）。
- 以上皆純函數、決定性；同輸入兩次結果相同。
"""

from __future__ import annotations

import datetime as dt
import math
import random

import polars as pl
import pytest

from twquant.ml.evaluate import (
    daily_rank_ic,
    decile_table,
    equal_weight_signed,
    ic_summary,
    is_monotonic,
)

_D = [dt.date(2026, 1, 5) + dt.timedelta(days=i) for i in range(6)]


def _scored(rows: list[tuple[dt.date, str, float, float | None]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [r[0] for r in rows],
            "stock_id": [r[1] for r in rows],
            "score": [r[2] for r in rows],
            "label": pl.Series([r[3] for r in rows], dtype=pl.Float64),
        }
    )


def _perfect(
    day: dt.date, n: int = 20, sign: float = 1.0
) -> list[tuple[dt.date, str, float, float | None]]:
    return [(day, f"{1000 + i}", sign * i, i / n) for i in range(n)]


# ── daily_rank_ic ────────────────────────────────────────────────────


def test_ac9_daily_ic_perfect_and_inverted() -> None:
    ic = daily_rank_ic(_scored(_perfect(_D[0]) + _perfect(_D[1], sign=-1.0)), "score", "label")
    got = {r["date"]: r["ic"] for r in ic.to_dicts()}
    assert got[_D[0]] == pytest.approx(1.0)
    assert got[_D[1]] == pytest.approx(-1.0)
    assert ic["n"].to_list() == [20, 20]


def test_ac9_daily_ic_drops_null_labels_and_singleton_days() -> None:
    rows = [*_perfect(_D[0]), (_D[1], "9999", 1.0, 0.5)]  # 單一樣本日 → corr 未定義
    rows += [(_D[0], "8888", 5.0, None)]  # null label 剔除、不影響當日 IC
    ic = daily_rank_ic(_scored(rows), "score", "label")
    got = {r["date"]: r["ic"] for r in ic.to_dicts()}
    assert got[_D[0]] == pytest.approx(1.0)
    assert got[_D[1]] is None  # 不 raise、不入統計


# ── ic_summary（含 Newey-West）───────────────────────────────────────


def test_ac9_ic_summary_basic_stats() -> None:
    ic = pl.DataFrame({"date": _D[:4], "ic": [0.1, 0.0, 0.2, -0.1], "n": [10] * 4})
    s = ic_summary(ic, nw_lag=0)
    assert s.n_days == 4
    assert s.mean == pytest.approx(0.05)
    assert s.hit_rate == pytest.approx(0.5)  # 2/4 為正
    assert s.t_naive == pytest.approx(s.mean / (s.std / math.sqrt(4)))
    assert s.t_newey_west == pytest.approx(s.t_naive, rel=0.02)  # lag=0 退化為樸素


def test_ac9_newey_west_deflates_t_under_autocorrelation() -> None:
    """重疊標籤的實際結構：ic[t] 是 10 個獨立衝擊的移動平均（MA(10)）→ 強正自相關，
    NW t 必須顯著小於樸素 t。"""
    rng = random.Random(42)
    shocks = [rng.gauss(0, 1) for _ in range(129)]
    vals = [0.05 + sum(shocks[i : i + 10]) / 10 * 0.05 for i in range(120)]
    ic = pl.DataFrame(
        {"date": [_D[0] + dt.timedelta(days=i) for i in range(120)], "ic": vals, "n": [10] * 120}
    )
    s = ic_summary(ic, nw_lag=10)
    assert s.t_newey_west < s.t_naive * 0.8
    assert s.t_newey_west > 0


def test_ac9_ic_summary_all_null_raises() -> None:
    ic = pl.DataFrame(
        {"date": _D[:2], "ic": [None, None], "n": [1, 1]}, schema_overrides={"ic": pl.Float64}
    )
    with pytest.raises(ValueError, match="無可用"):
        ic_summary(ic, nw_lag=0)


# ── decile_table / 單調 ──────────────────────────────────────────────


def test_ac9_decile_table_monotonic_on_perfect_signal() -> None:
    rows = []
    for d in _D[:3]:
        rows += _perfect(d, n=50)
    tbl = decile_table(_scored(rows), "score", "label", k=10)
    assert tbl.height == 10
    assert is_monotonic(tbl) is True
    assert tbl["avg_label"][9] > tbl["avg_label"][0]
    assert tbl["n"].sum() == 150


def test_ac9_decile_table_not_monotonic_on_noise() -> None:
    rows: list[tuple[dt.date, str, float, float | None]] = [
        (_D[0], f"{1000 + i}", (i % 3) * 1.0, ((i * 7) % 11) / 11) for i in range(33)
    ]
    tbl = decile_table(_scored(rows), "score", "label", k=3)
    assert tbl.height == 3
    assert is_monotonic(tbl) is False


def test_ac9_evaluate_deterministic() -> None:
    rows = _perfect(_D[0], n=40) + _perfect(_D[1], n=40)
    a = daily_rank_ic(_scored(rows), "score", "label")
    b = daily_rank_ic(_scored(rows), "score", "label")
    assert a.equals(b)
    assert decile_table(_scored(rows), "score", "label").equals(
        decile_table(_scored(rows), "score", "label")
    )


# ── 等權基準（永久對照組）─────────────────────────────────────────────


def test_ac9_equal_weight_signed_respects_direction() -> None:
    w = equal_weight_signed({"price.mom_20d": 1, "price.reversal_5d": -1, "chip.trust_net_10d": 1})
    assert w == pytest.approx(
        {"price.mom_20d": 1 / 3, "price.reversal_5d": -1 / 3, "chip.trust_net_10d": 1 / 3}
    )
    assert sum(abs(v) for v in w.values()) == pytest.approx(1.0)


def test_ac9_equal_weight_rejects_empty_and_bad_direction() -> None:
    with pytest.raises(ValueError, match="為空"):
        equal_weight_signed({})
    with pytest.raises(ValueError, match="±1"):
        equal_weight_signed({"a.b": 0})
