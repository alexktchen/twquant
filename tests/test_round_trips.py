"""T-133：由成交流水算逐筆（round-trip）損益統計。

D-061 的審查明列：「分開呈現交易勝率、賺賠比、成本後期望值與相對基準表現，
**避免把 IC 勝率、日勝率和逐筆勝率混用**」。

現況只有 `Metrics.win_rate`，而它是**日勝率**（報酬為正的交易日比例），
不是逐筆勝率。兩者可以差很多——右尾型策略常見「賠得比較多次、但賺得大筆」，
日勝率 53% 配逐筆勝率 44% 是完全可能的，混用會得到相反的印象。

一趟 round trip ＝ 部位由 0 建立到回到 0 的完整循環（與
`_average_holding_sessions` 的定義同源）。期末仍持有者為**未平倉**，
不計入已實現統計，但須據實回報筆數——把它當成平倉會虛報勝率。
"""

from __future__ import annotations

import datetime as dt

import pytest

from twquant.backtest.diagnostics import round_trip_stats
from twquant.backtest.engine import Trade

_D = [dt.date(2026, 1, d) for d in (2, 5, 6, 7, 8, 9)]


def _t(i: int, sid: str, side: str, shares: int, price: float, fee: float = 0.0) -> Trade:
    return Trade(_D[i], sid, side, shares, price, fee, 0.0, 0.0)


def test_ac1_single_winning_round_trip() -> None:
    """買 1000 股 @10 賣 @12，無費用 → 損益 +2,000、報酬 +20%。"""
    st = round_trip_stats([_t(0, "1101", "buy", 1000, 10.0), _t(1, "1101", "sell", 1000, 12.0)])
    assert st.n_closed == 1
    assert st.n_open == 0
    assert st.win_rate == pytest.approx(1.0)
    assert st.total_pnl == pytest.approx(2000.0)
    assert st.avg_win == pytest.approx(0.20)


def test_ac1_costs_are_charged_on_both_legs() -> None:
    """買進費用加進成本、賣出費用從價款扣除——只扣一邊會高估報酬。"""
    st = round_trip_stats(
        [_t(0, "1101", "buy", 1000, 10.0, fee=100.0), _t(1, "1101", "sell", 1000, 12.0, fee=200.0)]
    )
    # 成本 10,000+100 = 10,100；價款 12,000−200 = 11,800；損益 +1,700
    assert st.total_pnl == pytest.approx(1700.0)
    assert st.avg_win == pytest.approx(1700.0 / 10100.0)


def test_ac2_partial_exits_belong_to_the_same_cycle() -> None:
    """分批出場仍是**一趟**：部位沒歸零就還沒結束，拆成兩趟會讓筆數與勝率都失真。"""
    st = round_trip_stats(
        [
            _t(0, "1101", "buy", 1000, 10.0),
            _t(1, "1101", "sell", 400, 12.0),
            _t(2, "1101", "sell", 600, 9.0),
        ]
    )
    assert st.n_closed == 1
    # 成本 10,000；價款 4,800 + 5,400 = 10,200 → +200
    assert st.total_pnl == pytest.approx(200.0)


def test_ac2_reentry_starts_a_new_cycle() -> None:
    """清空後再進場是新的一趟；併成一趟會把兩段行情混在一起。"""
    st = round_trip_stats(
        [
            _t(0, "1101", "buy", 1000, 10.0),
            _t(1, "1101", "sell", 1000, 12.0),  # +2,000
            _t(2, "1101", "buy", 1000, 20.0),
            _t(3, "1101", "sell", 1000, 18.0),  # −2,000
        ]
    )
    assert st.n_closed == 2
    assert st.win_rate == pytest.approx(0.5)
    assert st.total_pnl == pytest.approx(0.0)


def test_ac3_open_position_is_reported_not_counted_as_a_win() -> None:
    """期末未平倉不得計入已實現統計——當成平倉會虛報勝率。"""
    st = round_trip_stats(
        [
            _t(0, "1101", "buy", 1000, 10.0),
            _t(1, "1101", "sell", 1000, 12.0),
            _t(2, "2330", "buy", 500, 100.0),  # 未平倉
        ]
    )
    assert st.n_closed == 1
    assert st.n_open == 1
    assert st.win_rate == pytest.approx(1.0)


def test_ac3_profit_factor_is_gross_wins_over_gross_losses() -> None:
    """賺賠比 ＝ 總獲利 ÷ 總虧損（絕對值），不是平均賺 ÷ 平均賠。"""
    st = round_trip_stats(
        [
            _t(0, "1101", "buy", 1000, 10.0),
            _t(1, "1101", "sell", 1000, 13.0),  # +3,000
            _t(2, "2330", "buy", 1000, 10.0),
            _t(3, "2330", "sell", 1000, 9.0),  # −1,000
        ]
    )
    assert st.profit_factor == pytest.approx(3.0)
    assert st.n_closed == 2
    assert st.win_rate == pytest.approx(0.5)


def test_ac3_no_losses_gives_infinite_profit_factor_not_a_crash() -> None:
    """全勝時分母為 0。回傳 None 而非 0 或極大值——0 會被讀成「全賠」。"""
    st = round_trip_stats([_t(0, "1101", "buy", 1000, 10.0), _t(1, "1101", "sell", 1000, 11.0)])
    assert st.profit_factor is None


def test_empty_ledger_is_all_zeroes_not_a_crash() -> None:
    st = round_trip_stats([])
    assert st.n_closed == 0 and st.n_open == 0
    assert st.win_rate == 0.0 and st.total_pnl == 0.0


def test_oversell_raises_rather_than_silently_going_negative() -> None:
    """放空未實作；負部位只可能是流水損壞（黃金規則 5）。"""
    from twquant.errors import DataError

    with pytest.raises(DataError, match="負"):
        round_trip_stats([_t(0, "1101", "buy", 100, 10.0), _t(1, "1101", "sell", 300, 11.0)])


# ── T-134：持有期分桶的每筆期望值與損益集中度 ──────────────────────
#
# 這個診斷回答的是「策略靠什麼賺錢」，不是「該抱多久」。
# 兩者的差別在 D-042 已經付過代價：落後 IC 支持的是降低調倉頻率，
# 不是遲滯持有；把分桶結果讀成「抱久一點比較好」是條件化陷阱。


def test_ac1_buckets_partition_all_closed_trips() -> None:
    """每一趟只能落在一個桶裡，且桶的筆數加總 ＝ 已平倉趟數。"""
    from twquant.backtest.diagnostics import holding_buckets

    ledger = [
        _t(0, "1101", "buy", 1000, 10.0),
        _t(1, "1101", "sell", 1000, 12.0),  # 3 曆日
        _t(0, "2330", "buy", 1000, 10.0),
        _t(5, "2330", "sell", 1000, 9.0),  # 7 曆日
    ]
    bs = holding_buckets(ledger, edges=(0, 5, 999))
    assert sum(b.n for b in bs) == 2
    assert [b.n for b in bs] == [1, 1]
    assert bs[0].share == pytest.approx(0.5)


def test_ac2_pnl_share_exposes_concentration() -> None:
    """損益佔比是重點：少數趟數貢獻多數獲利，是集中度脆弱性的直接證據。"""
    from twquant.backtest.diagnostics import holding_buckets

    ledger = [
        _t(0, "1101", "buy", 1000, 10.0),
        _t(1, "1101", "sell", 1000, 10.1),  # +100，短
        _t(0, "2330", "buy", 1000, 10.0),
        _t(5, "2330", "sell", 1000, 20.0),  # +10,000，長
    ]
    bs = holding_buckets(ledger, edges=(0, 5, 999))
    short, long_ = bs[0], bs[1]
    assert short.n == 1 and long_.n == 1
    assert long_.pnl_share == pytest.approx(10000.0 / 10100.0, abs=1e-6)
    assert short.pnl_share == pytest.approx(100.0 / 10100.0, abs=1e-6)


def test_ac3_empty_bucket_is_reported_with_zeroes_not_dropped() -> None:
    """空桶要留著。悄悄消失會讓讀者以為那段持有期不存在，而不是「沒有樣本」。"""
    from twquant.backtest.diagnostics import holding_buckets

    # 該趟持有 3 曆日（1/2 → 1/5），落在 [2,100)
    bs = holding_buckets(
        [_t(0, "1101", "buy", 1000, 10.0), _t(1, "1101", "sell", 1000, 12.0)],
        edges=(0, 2, 100, 999),
    )
    assert len(bs) == 3
    assert [b.n for b in bs] == [0, 1, 0]
    assert bs[0].expectancy == 0.0 and bs[0].pnl_share == 0.0
    assert bs[2].expectancy == 0.0 and bs[2].pnl_share == 0.0


def test_ac3_all_losses_gives_negative_pnl_share_without_crashing() -> None:
    """總損益為負時佔比仍要能算（分母用絕對值），不得除以 0 或回傳 NaN。"""
    from twquant.backtest.diagnostics import holding_buckets

    bs = holding_buckets(
        [_t(0, "1101", "buy", 1000, 10.0), _t(1, "1101", "sell", 1000, 8.0)],
        edges=(0, 999),
    )
    assert bs[0].n == 1
    assert bs[0].expectancy < 0
    assert bs[0].pnl == pytest.approx(-2000.0)
    assert bs[0].pnl_share == pytest.approx(1.0)  # 唯一的一趟，佔全部（分母帶號）


def test_ac1_edges_must_be_increasing() -> None:
    from twquant.backtest.diagnostics import holding_buckets

    with pytest.raises(ValueError, match="遞增"):
        holding_buckets([], edges=(0, 30, 10))


def test_ac1_bucket_boundary_lands_in_exactly_one_bucket() -> None:
    """恰好落在桶界的趟數只能算一次。

    全用閉區間會讓它同時進兩個桶，筆數加總就不等於總趟數——而那個錯誤在
    彙總表上完全看不出來，只會讓每個桶的佔比都偏高。
    """
    from twquant.backtest.diagnostics import holding_buckets

    # 1/2 → 1/7 恰好 5 曆日，正好是桶界
    bs = holding_buckets(
        [_t(0, "1101", "buy", 1000, 10.0), _t(3, "1101", "sell", 1000, 11.0)],
        edges=(0, 5, 999),
    )
    assert sum(b.n for b in bs) == 1, "邊界值被重複計入兩個桶"
    assert [b.n for b in bs] == [0, 1], "左閉右開：5 應落在 [5,999] 而非 [0,5)"
