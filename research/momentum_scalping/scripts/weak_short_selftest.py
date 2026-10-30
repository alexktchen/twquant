#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""weak_short 的自我測試：python weak_short_selftest.py。"""
from datetime import datetime

import weak_short as W

T0 = int(datetime(2026, 11, 2, 9, 0, tzinfo=W.TPE).timestamp()) * W.US


def row(p, m, sym="2330", prev=100.0, vw=None):
    vol = 1000
    return {"market": "TWSE", "symbol": sym, "name": "測", "price": p, "prev_close": prev, "volume": vol,
            "turnover": (vw if vw is not None else p + 2) * vol * 1000, "event_time": T0 + m * 60 * W.US}


# 每分鐘一個價：先做出兩個下降的擺動高點，中間的擺動低點 95.0
PATH = [98, 98.5, 99, 98.5, 98, 97.5, 97, 96.5, 96, 95.5, 95, 95.5, 96, 96.5, 97, 96.5, 96, 95.8, 95.6, 95.4]
TRIGGER = 94.8
FIRST = 15  # 第一個價在 09:15


def run(path, end=None, market_ok=False, shortable=lambda s: True, index=0.0, vw=None, first=FIRST):
    ev = []
    b = W.WeakBook(lambda k, t, i: ev.append((k, i)), shortable)
    for i, p in enumerate(path):
        m = first + i
        b.on_snapshot(T0 + m * 60 * W.US, [row(p, m, vw=vw)], {"TWSE": index}, market_ok)
    for m, p in end or []:
        b.on_snapshot(T0 + m * 60 * W.US, [row(p, m, vw=vw)], {"TWSE": index}, market_ok)
    return b, ev


def test_swing_uses_finished_bars_only():
    s = W.Series()
    for m, p in enumerate([1, 2, 5, 2, 1]):
        s.add(m, p)
    assert s.swing_high == [], "第 5 根還沒完成，不能認擺動高點"
    s.add(5, 1)
    assert s.swing_high == [(2, 5)]


def test_no_lookahead():
    """前面的進場不因後面的資料改變。"""
    a, _ = run(PATH + [TRIGGER])
    b, _ = run(PATH + [TRIGGER, 90, 120])
    sa, sb = a.series["2330"], b.series["2330"]
    assert sa.swing_high == sb.swing_high[: len(sa.swing_high)]
    assert a.open["2330"]["entry_price"] == b.trades[0]["entry_price"]


def test_entry_when_rules_met():
    b, ev = run(PATH + [TRIGGER])
    s = b.series["2330"]
    assert "2330" in b.open, (b.blocked_counts(), s.swing_high, s.swing_low, s.fast[-2:], s.slow[-2:])
    o = b.open["2330"]
    assert o["entry_price"] == round(TRIGGER - W.tick_size(TRIGGER), 2)
    assert o["stop"] == 97
    assert [k for k, _ in ev] == ["short_signal", "short_fill"]
    assert "反彈 97 不過前高後跌破 95" in ev[0][1]["text"]


def test_rising_highs_do_not_enter():
    path = list(PATH)
    path[14] = 99.5  # 第二個高點比第一個高
    b, _ = run(path + [TRIGGER])
    assert not b.open


def test_no_entry_above_vwap():
    assert not run(PATH + [TRIGGER], vw=90.0)[0].open


def test_no_entry_if_not_weaker_than_market():
    assert not run(PATH + [TRIGGER], index=-4.0)[0].open


def test_no_chasing_far_below_low():
    assert not run(PATH + [93.9])[0].open


def test_entry_window():
    assert not run(PATH + [TRIGGER], first=-6)[0].open, "09:14 跌破：09:15 之前不進場"
    assert run(PATH + [TRIGGER], first=-5)[0].open, "09:15 跌破：可以進場"
    assert not run(PATH + [TRIGGER], first=170)[0].open, "12:00 以後不進場"
    assert run(PATH + [TRIGGER], first=159)[0].open, "11:59 跌破：可以進場"


def test_not_shortable_blocked():
    b, _ = run(PATH + [TRIGGER], shortable=lambda s: False)
    assert not b.open and b.blocked_counts() == {"W:NOT_SHORTABLE": 1}


def test_stop_with_costs_and_once_per_day():
    b, _ = run(PATH + [TRIGGER], end=[(50, 97.0), (51, TRIGGER)])
    assert len(b.trades) == 1 and b.trades[0]["exit_reason"] == "STOP" and not b.open
    t = b.trades[0]
    fill_in, fill_out = TRIGGER - 0.1, 97.0 + 0.1
    fee = lambda x: max(20, int(x * 1000 * 0.001425 * 0.28))
    want = (fill_in - fill_out) * 1000 - fee(fill_in) - fee(fill_out) - int(fill_in * 1000 * 0.0015) - fill_in * 1000 * 0.0005
    assert abs(t["net_pnl"] - want) <= 1, (t, want)


def test_cover_at_vwap_and_time():
    b, _ = run(PATH + [TRIGGER])
    b.on_snapshot(T0 + 60 * W.US * 41, [row(95.0, 41, vw=94.0)], {"TWSE": 0.0}, False)
    assert b.trades[-1]["exit_reason"] == "VWAP"
    b2, _ = run(PATH + [TRIGGER], end=[(245, 94.0)])
    assert b2.trades[-1]["exit_reason"] == "TIME"


def test_max_open():
    b, _ = run(PATH + [TRIGGER])
    b.open.update({str(i): dict(b.open["2330"], sym=str(i)) for i in range(4)})
    b.open.pop("2330")
    b.open["x"] = dict(b.open["0"], sym="x")
    b.done.discard("2330")
    b._check_entry(row(TRIGGER, 40), T0 + 40 * 60 * W.US, {"TWSE": 0.0}, False)
    assert b.blocked_counts() == {"W:MAX": 1} and "2330" not in b.open


def test_stale_row_ignored():
    b = W.WeakBook(lambda *a: None)
    b.on_snapshot(T0 + 30 * 60 * W.US, [row(95, 20)], {"TWSE": 0.0}, False)
    assert "2330" not in b.series


def test_tick_size_matches_market_rule():
    assert [W.tick_size(p) for p in (9.9, 49, 99, 499, 999, 1000)] == [0.01, 0.05, 0.1, 0.5, 1.0, 5.0]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print("ok", f.__name__)
    print(f"{len(fns)} passed")
