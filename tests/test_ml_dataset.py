"""T-066：資料集組裝（SPEC-010 §2，AC-2/AC-3）。

契約（由本測試凍結）：
- `forward_open_returns(prices, horizon)`：t 列 → open(t+1)→open(t+1+h) 報酬，
  依「各股票自身的下一個有行情列」順延（停牌期間不可成交）；未來列不足 → null（保留列）。
  欄位 (date, stock_id, fwd_ret)，依 (date, stock_id) 排序。horizon < 1 → raise。
- `excess_rank_labels(fwd)`：減同日均值 → 當日橫斷面百分位 label ∈ (0, 1]；
  fwd_ret null → label null（不丟列）。rank 之下超額化不改變順位（性質測試）。
- `assemble(features, labels)`：特徵 dict{名: (date, stock_id, value)} 對 (date, stock_id)
  outer join 成寬表（欄名＝dict 鍵），labels 以 left join 掛上；缺值一律保留 null、
  禁止填 0；輸出依 (date, stock_id) 排序（決定性）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

import polars as pl
import pytest

from twquant.ml.dataset import assemble, excess_rank_labels, forward_open_returns

_D = [dt.date(2026, 3, 2 + i) for i in range(6)]  # 連續 6 個曆日；純函數只依列序，不依交易日曆


def _prices(rows: list[tuple[dt.date, str, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [r[0] for r in rows],
            "stock_id": [r[1] for r in rows],
            "open": [r[2] for r in rows],
        }
    )


# ── AC-2：前向報酬（open-to-open、停牌順延、尾端 null）──────────────


def test_ac2_forward_open_returns_hand_computed() -> None:
    px = _prices(
        [(_D[i], "1101", o) for i, o in enumerate([100.0, 102.0, 104.0, 106.0, 108.0, 110.0])]
    )
    out = forward_open_returns(px, horizon=2)
    got = {r["date"]: r["fwd_ret"] for r in out.filter(pl.col("stock_id") == "1101").to_dicts()}
    # t=D0：open(D1)=102 → open(D3)=106
    assert got[_D[0]] == pytest.approx(106.0 / 102.0 - 1.0)
    # t=D2：open(D3)=106 → open(D5)=110
    assert got[_D[2]] == pytest.approx(110.0 / 106.0 - 1.0)
    # 尾端未來列不足 → null（列保留）
    assert got[_D[3]] is None
    assert got[_D[4]] is None
    assert got[_D[5]] is None
    assert out.height == 6


def test_ac2_halted_stock_uses_own_next_row() -> None:
    # "2202" 缺 D2（停牌）：t=D1 的進場列是它自己的下一列 D3
    px = _prices(
        [
            (_D[i], "2202", o)
            for i, o in zip([0, 1, 3, 4, 5], [50.0, 51.0, 53.0, 54.0, 55.0], strict=True)
        ]
    )
    out = forward_open_returns(px, horizon=1)
    got = {r["date"]: r["fwd_ret"] for r in out.to_dicts()}
    # t=D1：進場 open(D3)=53、出場為其後 1 列 open(D4)=54
    assert got[_D[1]] == pytest.approx(54.0 / 53.0 - 1.0)


def test_ac2_bad_horizon_raises() -> None:
    px = _prices([(_D[0], "1101", 100.0)])
    with pytest.raises(ValueError):
        forward_open_returns(px, horizon=0)


# ── AC-2：超額＋秩轉換 ───────────────────────────────────────────────


def _fwd(rows: Sequence[tuple[dt.date, str, float | None]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [r[0] for r in rows],
            "stock_id": [r[1] for r in rows],
            "fwd_ret": pl.Series([r[2] for r in rows], dtype=pl.Float64),
        }
    )


def test_ac2_rank_labels_hand_computed() -> None:
    d = _D[0]
    out = excess_rank_labels(
        _fwd([(d, "A", 0.10), (d, "B", 0.05), (d, "C", -0.05), (d, "D", None)])
    )
    got = {r["stock_id"]: r["label"] for r in out.to_dicts()}
    assert got["A"] == pytest.approx(3 / 3)
    assert got["B"] == pytest.approx(2 / 3)
    assert got["C"] == pytest.approx(1 / 3)
    assert got["D"] is None  # null 保留、不丟列
    assert all(0.0 < v <= 1.0 for v in got.values() if v is not None)


def test_ac2_rank_invariant_to_same_day_shift() -> None:
    """性質：對同日全體加常數（市場漲跌）不改變 label——超額化在 rank 下的不變量。"""
    d = _D[0]
    base = [(d, s, v) for s, v in [("A", 0.02), ("B", -0.01), ("C", 0.05), ("E", 0.0)]]
    shifted = [(dd, s, v + 0.123) for dd, s, v in base]
    a = excess_rank_labels(_fwd(base)).sort("stock_id")
    b = excess_rank_labels(_fwd(shifted)).sort("stock_id")
    assert a["label"].to_list() == pytest.approx(b["label"].to_list())


def test_ac2_rank_single_stock_day() -> None:
    out = excess_rank_labels(_fwd([(_D[0], "A", 0.01)]))
    assert out["label"].to_list() == [pytest.approx(1.0)]


# ── AC-3：組裝——缺值保留 null、禁止填 0 ─────────────────────────────


def _feat(rows: list[tuple[dt.date, str, float | None]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [r[0] for r in rows],
            "stock_id": [r[1] for r in rows],
            "value": pl.Series([r[2] for r in rows], dtype=pl.Float64),
        }
    )


def test_ac3_assemble_preserves_nulls_no_zero_fill() -> None:
    d = _D[0]
    features = {
        "price.mom_20d": _feat([(d, "A", 1.5), (d, "B", None)]),  # B 顯式 null
        "chip.trust_net_10d": _feat([(d, "A", -0.3)]),  # B 整列缺（outer join 產生 null）
    }
    labels = excess_rank_labels(_fwd([(d, "A", 0.02)]))  # B 無標籤
    out = assemble(features, labels)
    assert set(out.columns) == {"date", "stock_id", "price.mom_20d", "chip.trust_net_10d", "label"}
    b = out.filter(pl.col("stock_id") == "B").to_dicts()[0]
    assert b["price.mom_20d"] is None
    assert b["chip.trust_net_10d"] is None
    assert b["label"] is None
    # 全表無任何「憑空出現的 0」
    zeros = out.select(
        pl.sum_horizontal(
            pl.col("price.mom_20d").eq(0.0).fill_null(False),
            pl.col("chip.trust_net_10d").eq(0.0).fill_null(False),
        ).sum()
    ).item()
    assert zeros == 0


def test_ac3_assemble_outer_join_and_sorted() -> None:
    features = {
        "f1": _feat([(_D[1], "B", 2.0), (_D[0], "A", 1.0)]),
        "f2": _feat([(_D[0], "C", 3.0)]),
    }
    labels = excess_rank_labels(_fwd([(_D[0], "A", 0.01), (_D[1], "B", 0.02)]))
    out = assemble(features, labels)
    # (date, stock_id) 聯集：D0×{A,C} + D1×{B}，依 (date, stock_id) 排序
    assert out.select("date", "stock_id").rows() == [
        (_D[0], "A"),
        (_D[0], "C"),
        (_D[1], "B"),
    ]


def test_ac3_assemble_deterministic() -> None:
    d = _D[0]
    features = {
        "f1": _feat([(d, "A", 1.0), (d, "B", 2.0)]),
        "f2": _feat([(d, "B", 4.0), (d, "A", 3.0)]),
    }
    labels = excess_rank_labels(_fwd([(d, "A", 0.01), (d, "B", -0.01)]))
    assert assemble(features, labels).equals(assemble(features, labels))


def test_ac3_assemble_empty_features_raises() -> None:
    with pytest.raises(ValueError):
        assemble({}, excess_rank_labels(_fwd([(_D[0], "A", 0.01)])))
