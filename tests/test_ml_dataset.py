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
import math
from collections.abc import Sequence

import polars as pl
import pytest

from twquant.ml.dataset import (
    assemble,
    assert_feature_coverage,
    excess_rank_labels,
    forward_open_returns,
)

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


# ── T-088 AC-3：訓練前的特徵覆蓋閘門 ─────────────────────────────────────────
# Phase 1 的 25 個 pin 特徵裡有 2 個在 factor_values 從未有值（D-045）、1 個全 null（T-082），
# 訓練照樣「成功」。訓練路徑須在 assemble 前 raise；生產 ml score 不掛此閘門（單日缺某因子屬正常）。


def test_t088_ac3_empty_feature_raises_with_name() -> None:
    d = dt.date(2026, 6, 1)
    feats = {"price.mom_20d": _feat([(d, "1101", 0.1)]), "chip.short_margin_ratio": _feat([])}
    with pytest.raises(ValueError, match=r"chip\.short_margin_ratio"):
        assert_feature_coverage(feats)


def test_t088_ac3_all_null_feature_raises() -> None:
    d = dt.date(2026, 6, 1)
    feats = {
        "price.mom_20d": _feat([(d, "1101", 0.1)]),
        "price.rs_market_20d": _feat([(d, "1101", None), (d, "2330", None)]),
    }
    with pytest.raises(ValueError, match=r"price\.rs_market_20d"):
        assert_feature_coverage(feats)


def test_t088_ac3_min_share_threshold_catches_near_empty_feature() -> None:
    """覆蓋率低於門檻也要抓（Phase 1 的籌碼因子只有 14%）。門檻由呼叫端傳入，不寫死。"""
    d = dt.date(2026, 6, 1)
    sparse = _feat([(d, "1101", 0.2)] + [(d, f"{2000 + i}", None) for i in range(9)])
    feats = {
        "price.mom_20d": _feat([(d, f"{2000 + i}", 0.1) for i in range(10)]),
        "chip.foreign_net_10d": sparse,
    }
    with pytest.raises(ValueError, match=r"chip\.foreign_net_10d"):
        assert_feature_coverage(feats, min_non_null_share=0.5)
    assert_feature_coverage(feats, min_non_null_share=0.05)  # 門檻放低即通過


def test_t088_ac3_healthy_features_pass_and_report_shares() -> None:
    d = dt.date(2026, 6, 1)
    feats = {
        "a": _feat([(d, "1101", 0.1), (d, "2330", None)]),
        "b": _feat([(d, "1101", 0.3), (d, "2330", 0.4)]),
    }
    shares = assert_feature_coverage(feats)
    assert shares == {"a": pytest.approx(0.5), "b": pytest.approx(1.0)}


# ── T-098／D-054：股利還原後的總報酬標籤 ────────────────────────────────
#
# 實測（DECISIONS.md D-054）：未還原時 6–8 月有 38.2% 的樣本換分位、原「最差一成」
# 有 17.9% 是除息假象。標籤是前瞻構造，不受 assert_no_lookahead 與還原價錨定規則
# （D-055）約束，故可獨立於因子端先行。


def _px_with_adj() -> pl.DataFrame:
    """1101 在 D4 除息（價格 100 → 90，adj_factor 0.9）；1102 全程平盤無事件。

    `adj` 是**固定過去錨**的累積還原因子：只含 ex_date <= 該列日期的事件，
    故與 as_of 無關（這正是它能通過 bit-exact 比較的原因）。
    """
    days = [dt.date(2024, 7, d) for d in (1, 2, 3, 4, 5, 8, 9, 10)]
    rows = []
    for i, d in enumerate(days):
        ex_passed = i >= 3
        rows.append(
            {
                "date": d,
                "stock_id": "1101",
                "open": 90.0 if ex_passed else 100.0,
                "adj": 0.9 if ex_passed else 1.0,
                "amount": 5.0e8,
            }
        )
        rows.append({"date": d, "stock_id": "1102", "open": 100.0, "adj": 1.0, "amount": 5.0e8})
    return pl.DataFrame(rows)


def test_ac3_forward_open_returns_total_return_removes_ex_dividend_drop() -> None:
    """除息造成的機械跌幅不是虧損。未還原時 1101 看起來 −10%，還原後應為 0%。"""
    from twquant.ml.dataset import forward_open_returns

    px = _px_with_adj()
    d0 = dt.date(2024, 7, 1)  # entry = open(D2)=100 / 100，exit = open(D4)=90 / 100
    raw = forward_open_returns(px.drop("adj"), 2)
    adj = forward_open_returns(px, 2, adjusted=True)

    def at(f: pl.DataFrame, sid: str) -> float:
        return float(f.filter((pl.col("date") == d0) & (pl.col("stock_id") == sid))["fwd_ret"][0])

    assert at(raw, "1101") == pytest.approx(-0.1)
    assert at(raw, "1102") == pytest.approx(0.0)
    assert at(adj, "1101") == pytest.approx(0.0)
    assert at(adj, "1102") == pytest.approx(0.0)


def test_ac3_total_return_label_no_longer_ranks_ex_dividend_stock_last() -> None:
    """這條是 D-054 的核心：未還原時除息股被標成當日最差，還原後與同儕並列。"""
    from twquant.ml.dataset import forward_open_returns

    px = _px_with_adj()
    d0 = dt.date(2024, 7, 1)
    raw = excess_rank_labels(forward_open_returns(px.drop("adj"), 2))
    adj = excess_rank_labels(forward_open_returns(px, 2, adjusted=True))

    def lab(f: pl.DataFrame, sid: str) -> float:
        return float(f.filter((pl.col("date") == d0) & (pl.col("stock_id") == sid))["label"][0])

    assert lab(raw, "1101") < lab(raw, "1102")  # 除息股被判為輸家
    assert lab(adj, "1101") == lab(adj, "1102")  # 還原後打平


def test_ac3_adjusted_requires_adj_column() -> None:
    """缺 adj 欄卻要求還原＝靜默回退成價格報酬，正是 D-054 要消滅的失效模式。"""
    from twquant.ml.dataset import forward_open_returns

    with pytest.raises(ValueError, match="adj"):
        forward_open_returns(_px_with_adj().drop("adj"), 2, adjusted=True)


def test_ac3_adjusted_is_identity_when_no_events() -> None:
    """adj 恆為 1（該股全期無除權息）時，還原版必須與原版 bit-identical。"""
    from twquant.ml.dataset import forward_open_returns

    px = _px_with_adj().filter(pl.col("stock_id") == "1102")
    assert forward_open_returns(px, 2, adjusted=True).equals(
        forward_open_returns(px.drop("adj"), 2)
    )


def test_ac3_cost_model_uses_raw_price_not_adjusted() -> None:
    """成本必須用**可成交的真實價格**算：滑價的半價差是價格相依的
    （`costs.py:_half_spread_frac` = max(0.5×tick/price, 5bps)），股數也由真實價決定。

    兩檔股票的**還原後**價格序列完全相同（皆 100），但真實價分別是 11 與 99。
    毛報酬因此相同、超額為 0，label 只剩成本項——若成本誤用還原價，兩者會相等。
    """
    from twquant.backtest.costs import CostConfig
    from twquant.ml.dataset import cost_adjusted_return_labels

    days = [dt.date(2024, 7, d) for d in (1, 2, 3, 4, 5, 8)]
    rows = []
    for d in days:
        rows.append({"date": d, "stock_id": "1101", "open": 11.0, "adj": 0.11, "amount": 5.0e8})
        rows.append({"date": d, "stock_id": "1102", "open": 99.0, "adj": 0.99, "amount": 5.0e8})
    px = pl.DataFrame(rows)
    out = cost_adjusted_return_labels(
        px,
        horizon=2,
        adv_window_sessions=2,
        position_notional_twd=500_000.0,
        downside_penalty=0.0,
        cost_cfg=CostConfig(),
        adjusted=True,
        # 取第二列：adv_window_sessions=2 需要兩列才有 ADV，首列的成本必為 null
    ).filter(pl.col("date") == dt.date(2024, 7, 2))
    by = {r["stock_id"]: r["label"] for r in out.to_dicts()}
    assert by["1101"] is not None and by["1102"] is not None
    assert by["1101"] < 0.0 and by["1102"] < 0.0  # 毛報酬 0 → label 純成本
    assert by["1101"] != by["1102"], "成本未反映真實價格水準（疑似誤用還原價）"


def test_ac3_attach_adjustment_is_cumulative_and_asof() -> None:
    """累積還原因子：固定過去錨（只含 ex_date ≤ 該列日期的事件）。

    實測約 5.75% 的除息日在 prices_daily 沒有對應列（颱風休市、停牌）。內連接會
    靜默丟掉那些事件的調整，故必須 as-of：調整從**下一個有行情的交易日**起生效。
    """
    from twquant.ml.dataset import attach_adjustment

    px = pl.DataFrame(
        [
            {"date": dt.date(2024, 7, d), "stock_id": "1101", "open": 100.0}
            for d in (1, 2, 4, 5, 8)
        ]  # 7/3 無行情列
    )
    ev = pl.DataFrame(
        [
            {"date": dt.date(2024, 7, 2), "stock_id": "1101", "adj_factor": 0.9},
            {"date": dt.date(2024, 7, 3), "stock_id": "1101", "adj_factor": 0.5},  # 該日無行情
        ]
    )
    got = {r["date"]: r["adj"] for r in attach_adjustment(px, ev).to_dicts()}
    assert got[dt.date(2024, 7, 1)] == pytest.approx(1.0)
    assert got[dt.date(2024, 7, 2)] == pytest.approx(0.9)
    # 7/3 的事件沒有行情列，但不得遺失：從 7/4 起併入累積
    assert got[dt.date(2024, 7, 4)] == pytest.approx(0.45)
    assert got[dt.date(2024, 7, 8)] == pytest.approx(0.45)


def test_ac3_attach_adjustment_leaves_eventless_stocks_at_one() -> None:
    """無事件的股票 adj 恆為 1.0，且不得因 join 丟列。"""
    from twquant.ml.dataset import attach_adjustment

    px = pl.DataFrame(
        [
            {"date": dt.date(2024, 7, d), "stock_id": s, "open": 100.0}
            for d in (1, 2)
            for s in ("1101", "1102")
        ]
    )
    ev = pl.DataFrame([{"date": dt.date(2024, 7, 2), "stock_id": "1101", "adj_factor": 0.9}])
    out = attach_adjustment(px, ev)
    assert out.height == px.height
    assert out.filter(pl.col("stock_id") == "1102")["adj"].to_list() == [1.0, 1.0]


def test_ac3_attach_adjustment_rejects_nonpositive_factor() -> None:
    """adj ≤ 0 會讓還原價變成 0／負數，並經累乘污染該股全部歷史（黃金規則 5）。"""
    from twquant.ml.dataset import attach_adjustment

    px = pl.DataFrame([{"date": dt.date(2024, 7, 1), "stock_id": "1101", "open": 100.0}])
    ev = pl.DataFrame([{"date": dt.date(2024, 7, 1), "stock_id": "1101", "adj_factor": 0.0}])
    with pytest.raises(ValueError, match="adj_factor"):
        attach_adjustment(px, ev)


def test_ac3_attach_adjustment_refuses_empty_events() -> None:
    """空事件表必須 raise，不得回傳 adj=1.0。

    這是複查抓到的 critical：`attach_adjustment` 只在呼叫端已選擇 total_return 時
    才會被呼叫，此時空表**一定**是錯誤狀態。原本靜默回 1.0 會讓 cli 照樣印
    「標籤改用總報酬」然後用未還原標籤訓練——把 D-054 要消滅的東西原樣復活。
    """
    from twquant.ml.dataset import attach_adjustment

    px = pl.DataFrame([{"date": dt.date(2024, 7, 1), "stock_id": "1101", "open": 100.0}])
    empty = pl.DataFrame(schema={"date": pl.Date, "stock_id": pl.String, "adj_factor": pl.Float64})
    with pytest.raises(ValueError, match="0 筆"):
        attach_adjustment(px, empty)


def test_ac3_attach_adjustment_accumulates_per_stock_not_globally() -> None:
    """累乘必須 .over("stock_id")。複查用突變測試證明：拿掉它，全套 572 測試仍全綠
    ——因為兩個既有 fixture 都只有**一檔**股票有事件。"""
    from twquant.ml.dataset import attach_adjustment

    days = [dt.date(2024, 7, d) for d in (1, 2, 3)]
    px = pl.DataFrame(
        [{"date": d, "stock_id": s, "open": 100.0} for d in days for s in ("1101", "1102")]
    )
    ev = pl.DataFrame(
        [
            {"date": dt.date(2024, 7, 2), "stock_id": "1101", "adj_factor": 0.5},
            {"date": dt.date(2024, 7, 2), "stock_id": "1102", "adj_factor": 0.8},
        ]
    )
    out = attach_adjustment(px, ev)
    got = {(r["stock_id"], r["date"]): r["adj"] for r in out.to_dicts()}
    # 逐股票累乘：各自 0.5 與 0.8。全域累乘會得到 0.5 與 0.4（或 0.8 與 0.4，視排序）
    assert got[("1101", dt.date(2024, 7, 3))] == pytest.approx(0.5)
    assert got[("1102", dt.date(2024, 7, 3))] == pytest.approx(0.8)


def test_ac3_forward_open_returns_zero_price_yields_null_not_inf() -> None:
    """schema 允許 open >= 0，故 0 是合法值。未防護時 open(t+1)=0 → +inf，
    而 `excess_rank_labels` 的 mean().over("date") 會把 inf 擴散到整個橫斷面，
    讓當日所有 label 變成同一個常數。成本感知路徑早已有這道防護，此路徑先前沒有。"""
    from twquant.ml.dataset import forward_open_returns

    days = [dt.date(2024, 7, d) for d in (1, 2, 3, 4)]
    px = pl.DataFrame(
        [{"date": d, "stock_id": "1101", "open": 0.0 if d == days[1] else 100.0} for d in days]
        + [{"date": d, "stock_id": "1102", "open": 100.0} for d in days]
    )
    fwd = forward_open_returns(px, 1)
    vals = fwd.filter(pl.col("stock_id") == "1101")["fwd_ret"].to_list()
    assert all(v is None or math.isfinite(v) for v in vals), f"出現 inf：{vals}"
    lab = excess_rank_labels(fwd).filter(pl.col("date") == days[0])["label"].to_list()
    assert len({v for v in lab if v is not None}) > 0
