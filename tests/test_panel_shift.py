"""T-139／T-140：缺口感知的列位移與零分母防護（`twquant.panel`）。

兩個缺陷同一個根：`shift(n)` 是**列位移**，不是曆日位移。

某檔中間缺 N 個開市日時，「20 日報酬」實際橫跨數月，而結果**不會有任何異常標記**
——它只是一個看起來很大的數字。實測 `prices_daily` 有 413 處 >5 開市日的個股缺口
（最大 260 日），h=20 下約 8,673 個訓練樣本的標籤橫跨缺口。

`factor_values` 的 22,988 列 `inf` 是同一件事在特徵端的表現：5547 在 2026-03 只有 6 列，
`shift(20)` 往回落到 **2021-02** 一列 `close=0` 的無效資料上 → 除以零 → `inf`。
而 `inf` **不是** null，`drop_nulls` 攔不住它。

故兩件事一起修：位移要驗跨距，除法要驗分母。
"""

from __future__ import annotations

import datetime as dt
import math

import polars as pl
import pytest

from twquant.panel import safe_ratio, session_index, shift_within_span


def _panel(rows: list[tuple[str, dt.date, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "stock_id": [r[0] for r in rows],
            "date": [r[1] for r in rows],
            "close": [r[2] for r in rows],
        }
    )


_D = [dt.date(2026, 1, d) for d in (5, 6, 7, 8, 9, 12, 13, 14, 15, 16)]


# ── session_index：市場日曆的稠密序號 ────────────────────────────────
def test_ac1_session_index_is_dense_over_the_market_calendar() -> None:
    """序號取自**全市場**出現過的日期，不是個股自己的列——個股缺列正是要偵測的東西。"""
    rows = [("A", d, 10.0) for d in _D] + [("B", _D[0], 5.0), ("B", _D[9], 6.0)]
    out = session_index(_panel(rows))
    a = out.filter(pl.col("stock_id") == "A").sort("date")["_sidx"].to_list()
    b = out.filter(pl.col("stock_id") == "B").sort("date")["_sidx"].to_list()
    assert a == list(range(10)), "A 每天都在，序號應連續"
    assert b == [0, 9], "B 只有頭尾兩列，序號須反映它跳過了 8 個開市日"


def test_ac1_session_index_rejects_missing_columns() -> None:
    with pytest.raises(ValueError, match="缺少欄位"):
        session_index(pl.DataFrame({"stock_id": ["A"]}))


# ── shift_within_span：跨距超標即 null ──────────────────────────────
def test_ac2_normal_shift_is_unchanged() -> None:
    """無缺口時必須與樸素 `shift` 逐位元相同——既有結果不得因這道防護而改變。"""
    df = session_index(_panel([("A", d, 10.0 + i) for i, d in enumerate(_D)]))
    got = df.with_columns(p=shift_within_span(pl.col("close"), 3, max_span=3)).sort("date")
    naive = df.sort("date").with_columns(
        p=pl.col("close").shift(3).over("stock_id", order_by="date")
    )
    assert got["p"].to_list() == naive["p"].to_list()


def test_ac2_shift_across_a_gap_returns_null_not_a_number() -> None:
    """核心案例：往回數 3 列卻跨越 8 個開市日 → null。

    不設這道防護時，它會安靜地回傳五年前的價格，算出一個看起來很大的報酬。
    """
    rows = [("A", d, 10.0) for d in _D]  # A 撐起完整日曆
    rows += [("B", _D[0], 100.0), ("B", _D[1], 101.0), ("B", _D[2], 102.0), ("B", _D[9], 200.0)]
    df = session_index(_panel(rows))
    out = (
        df.filter(pl.col("stock_id") == "B")
        .sort("date")
        .with_columns(p=shift_within_span(pl.col("close"), 3, max_span=3))
    )
    assert out["p"].to_list() == [None, None, None, None], (
        "第 4 列往回 3 列跨了 9 個開市日，超過 max_span=3，必須是 null"
    )


def test_ac2_tolerance_allows_holidays_but_not_outages() -> None:
    """容忍度要放得下連假，放不下停牌。

    `max_span` 給的是**開市日**跨距，農曆年封關不漏開市日故不受影響；
    真正的停牌才會被攔下。
    """
    rows = [("A", d, 10.0) for d in _D]
    rows += [("B", _D[0], 100.0), ("B", _D[1], 101.0), ("B", _D[3], 103.0)]  # 只缺 1 個開市日
    df = session_index(_panel(rows))
    out = (
        df.filter(pl.col("stock_id") == "B")
        .sort("date")
        .with_columns(p=shift_within_span(pl.col("close"), 2, max_span=3))
    )
    assert out["p"].to_list() == [None, None, 100.0], "跨 3 個開市日 ≤ max_span=3，應放行"


def test_ac2_bad_params_raise() -> None:
    df = session_index(_panel([("A", _D[0], 1.0)]))
    with pytest.raises(ValueError, match="max_span"):
        df.with_columns(p=shift_within_span(pl.col("close"), 3, max_span=2))


# ── safe_ratio：零分母回 null，不回 inf ──────────────────────────────
def test_ac3_zero_denominator_is_null_not_inf() -> None:
    """`inf` **不是** null，`drop_nulls`／`is_not_null` 都會放它過去。

    實測 `factor_values` 有 22,988 列 inf，源頭是 2021-01~02 的 1,185 列 `close=0`
    （那些列有成交量與金額，只有價格欄是 0——清洗層漏掉的無效值）。
    """
    df = pl.DataFrame({"a": [10.0, 10.0, 10.0], "b": [2.0, 0.0, None]})
    out = df.with_columns(r=safe_ratio(pl.col("a"), pl.col("b")))
    got = out["r"].to_list()
    assert got[0] == pytest.approx(5.0)
    assert got[1] is None, "分母 0 必須是 null，不得是 inf"
    assert got[2] is None


def test_ac3_negative_denominator_is_also_null() -> None:
    """價格為負沒有意義；讓它算出一個數字只會把錯誤往下游推。"""
    out = pl.DataFrame({"a": [10.0], "b": [-5.0]}).with_columns(
        r=safe_ratio(pl.col("a"), pl.col("b"))
    )
    assert out["r"][0] is None


def test_ac3_result_is_always_finite_or_null() -> None:
    """輸出的不變量：每個值不是 null 就是有限。這是可以斷言的，先前沒有人斷言。"""
    df = pl.DataFrame({"a": [1.0, -1.0, 0.0, 1e300], "b": [0.0, 0.0, 0.0, 1e-300]})
    vals = df.with_columns(r=safe_ratio(pl.col("a"), pl.col("b")))["r"].to_list()
    assert all(v is None or math.isfinite(v) for v in vals), f"出現非有限值：{vals}"
