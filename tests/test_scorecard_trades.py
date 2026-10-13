"""T-350／D-176 §二：名單績效逐筆——每一檔新進名單各算一筆，隔天開盤買、抱滿 20 個交易日、再隔天開盤賣。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest

import twquant.factors.builtins  # noqa: F401
from tests.test_backtest_slots import BASE, N_DAYS, START, _cfg, _wh
from twquant.backtest import costs as ct
from twquant.backtest.engine import price_panel, run_backtest
from twquant.backtest.list_trades import EntryTrade, new_entries, simulate_entries
from twquant.data import calendar as cal

_CC = ct.CostConfig()
_D = [dt.date(2026, 5, 4) + dt.timedelta(days=i) for i in range(40)]
_HOLD = 20
_NOTIONAL = 1_000_000.0


def _row(px: float, **kw: Any) -> dict[str, Any]:
    r: dict[str, Any] = {
        "open": px,
        "close": px,
        "_prev_close": px,
        "_adv": 1.0e10,
        "limit_up_price": None,
        "limit_down_price": None,
    }
    r.update(kw)
    return r


def _panel(
    px: dict[int, float] | None = None, **overrides: dict[str, Any] | None
) -> dict[dt.date, dict[str, dict[str, Any]]]:
    """1101 每天 100 元；`px` 改某幾天的價格；overrides 用 'd<i>' 鍵改某天整列（None＝停牌）。"""
    out: dict[dt.date, dict[str, dict[str, Any]]] = {}
    for i, d in enumerate(_D):
        p = (px or {}).get(i, 100.0)
        row: dict[str, Any] | None = _row(p)
        if f"d{i}" in overrides:
            o = overrides[f"d{i}"]
            row = None if o is None else _row(p, **o)
        out[d] = {} if row is None else {"1101": row}
    return out


def _sim(panel: Any, signal: int = 0, *, divs: Any = None, splits: Any = None) -> EntryTrade:
    (t,) = simulate_entries(
        [(_D[signal], "1101")],
        _D,
        panel,
        divs or {},
        splits or {},
        _CC,
        hold_sessions=_HOLD,
        notional=_NOTIONAL,
        max_participation=0.05,
        lot=1,
    )
    return t


def test_ac4_buy_next_open_hold_20_sessions_sell_the_open_after() -> None:
    t = _sim(_panel({i: 100.0 + i for i in range(40)}))
    assert t.status == "closed"
    assert t.entry_date == _D[1] and t.entry_price == pytest.approx(101.0)  # 名單隔天開盤
    assert t.exit_date == _D[22] and t.exit_price == pytest.approx(
        122.0
    )  # 第 21 天收盤後判斷 → 隔天開盤
    assert t.sessions_held == _HOLD
    shares = int(_NOTIONAL // 101.0)
    buy = ct.trade_cost(
        side="buy", price=101.0, shares=shares, participation=101.0 * shares / 1e10, cfg=_CC
    )
    sell = ct.trade_cost(
        side="sell", price=122.0, shares=shares, participation=122.0 * shares / 1e10, cfg=_CC
    )
    assert t.cost == pytest.approx(buy.total + sell.total)
    assert t.ret == pytest.approx(
        (122.0 * shares - sell.total) / (101.0 * shares + buy.total) - 1.0
    )
    assert t.ret is not None and t.ret < 122.0 / 101.0 - 1.0  # 有扣成本（含證交稅）


def test_ac4_limit_up_open_cannot_be_bought() -> None:
    t = _sim(_panel(d1={"limit_up_price": 100.0}))
    assert (t.status, t.unfilled_reason, t.ret) == ("unfilled", "limit_up", None)


def test_ac4_suspended_on_entry_day_is_unfilled_not_dropped() -> None:
    t = _sim(_panel(d1=None))
    assert (t.status, t.unfilled_reason) == ("unfilled", "suspended")


def test_ac4_limit_down_open_delays_the_sale_to_the_next_session() -> None:
    t = _sim(_panel(d22={"limit_down_price": 100.0}))
    assert t.exit_date == _D[23]
    assert t.sessions_held == _HOLD + 1  # 多抱一晚


def test_ac4_dividends_are_credited_and_splits_change_shares_not_value() -> None:
    # 80 元附近：跳空前後同一個跳動點級距（0.1），滑價比例相同，差異只剩股利
    flat = {i: 80.0 for i in range(40)}
    plain = _sim(_panel(flat))
    # 第 5 天除息：每股 80 ×（1 − 0.95）＝ 4 元；第 5 天起價格跟著跳空到 76
    paid = _sim(
        _panel(flat | {i: 76.0 for i in range(5, 40)}),
        divs={_D[5]: [("1101", 0.95, "cash_dividend")]},
    )
    shares = int(_NOTIONAL // 80.0)
    assert paid.dividends == pytest.approx(4.0 * shares)
    assert paid.ret == pytest.approx(
        plain.ret, abs=2e-4
    )  # 除息不是虧損（只差賣價低一點的手續費與稅）
    # 第 5 天 1 拆 2：價格變一半、股數變兩倍，報酬不變
    split = _sim(_panel(flat | {i: 40.0 for i in range(5, 40)}), splits={_D[5]: [("1101", 0.5)]})
    assert split.status == "closed" and split.exit_reason == "time_stop"
    assert split.ret == pytest.approx(plain.ret, abs=2e-4)


def test_ac4_pending_and_open_trades_are_marked_not_counted() -> None:
    assert _sim(_panel(), signal=39).status == "pending"
    t = _sim(_panel({i: 110.0 for i in range(33, 40)}), signal=30)
    assert t.status == "open" and t.exit_price == pytest.approx(110.0)
    assert t.ret is not None and 0.0 < t.ret < 0.1  # 以最新收盤估、扣預估賣出成本


def test_ac5_new_entries_compare_with_the_last_published_list() -> None:
    lists = {_D[0]: ["A", "B"], _D[1]: ["B", "C"], _D[3]: ["C", "D"], _D[4]: [], _D[5]: ["C"]}
    assert new_entries(lists, _D[:6]) == [
        (_D[0], "A"),
        (_D[0], "B"),  # 第一份名單全部是新進
        (_D[1], "C"),
        (_D[3], "D"),  # 第 2 天沒公布：跟第 1 天比，C 不算新進
        (_D[5], "C"),  # 第 4 天公布了空名單：C 隔天再出現就是新進
    ]


def test_ac4_matches_the_engine_round_trip(tmp_path: Path) -> None:
    """同一份資料、同一檔：逐筆模擬的進出日、價格與報酬要和引擎（一個位子、抱滿 N 日）對得上。"""
    wh = _wh(tmp_path)
    start, end = BASE + dt.timedelta(days=START), BASE + dt.timedelta(days=N_DAYS - 1)
    hold = 10
    cfg = _cfg([{"kind": "time_stop", "sessions": hold}], n=1)
    res = run_backtest(
        cfg, _CC, start, end, wh, initial_capital=_NOTIONAL, lot_size=1, picks={start: ["1101"]}
    )
    buy, sell = res.ledger
    sessions = cal.load(wh).between(start, end)
    (t,) = simulate_entries(
        [(start, "1101")],
        sessions,
        price_panel(wh, start, end),
        {},
        {},
        _CC,
        hold_sessions=hold,
        notional=_NOTIONAL,
        max_participation=0.05,
        lot=1,
    )
    assert (t.entry_date, t.entry_price) == (buy.date, buy.price)
    assert (t.exit_date, t.exit_price) == (sell.date, sell.price)
    engine_ret = (sell.price * sell.shares - sell.commission - sell.tax - sell.slippage) / (
        buy.price * buy.shares + buy.commission + buy.tax + buy.slippage
    ) - 1.0
    assert t.ret == pytest.approx(
        engine_ret, abs=1e-5
    )  # 引擎受帳戶現金限制少買幾股，報酬率幾乎一樣
