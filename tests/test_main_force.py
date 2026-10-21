"""T-425／D-206 §三：主力淨買超的逐日增量版，要跟 factors/broker.py 的全歷史批次版逐列相同。"""

from __future__ import annotations

import datetime as dt
import random

import polars as pl
import pytest

from twquant.conventions import TZ
from twquant.data.serving import main_force as mf
from twquant.data.serving.schema import ALL_TABLES, spec_of
from twquant.factors.broker import clean_daily, daytrade_events

_STOCKS = ["1101", "2330", "2603", "3008", "6505"]
_BROKERS = [f"B{i}" for i in range(10)]
_DAYTRADERS = {"B0", "B1"}  # 幾乎都今買明賣
_PARAMS: dict[str, object] = {"daytrade_min_ratio": 0.5, "daytrade_min_events": 4}


class _View:
    """只有 table() 的假 PitView（clean_daily／daytrade_events 只用到它）。"""

    def __init__(self, tables: dict[str, pl.DataFrame]) -> None:
        self._t = tables

    def table(self, name: str) -> pl.LazyFrame:
        return self._t[name].lazy()


def _days(n: int) -> list[dt.date]:
    out: list[dt.date] = []
    d = dt.date(2025, 3, 3)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _panel(days: list[dt.date], seed: int = 7) -> pl.DataFrame:
    rng = random.Random(seed)
    rows = []
    held: set[tuple[str, str]] = set()
    for d in days:
        nxt: set[tuple[str, str]] = set()
        for s in _STOCKS:
            for b in rng.sample(_BROKERS, 6):
                if (s, b) in held and b in _DAYTRADERS:
                    buy, sell = 0, rng.randint(100, 900)  # 昨天買的今天倒
                elif b in _DAYTRADERS or rng.random() < 0.5:
                    buy, sell = rng.randint(100, 900), 0
                    nxt.add((s, b))
                else:
                    buy, sell = 0, rng.randint(100, 900)
                rows.append((d, s, b, buy, sell))
        held = nxt
    return pl.DataFrame(
        rows, schema=["date", "stock_id", "broker_id", "buy_shares", "sell_shares"], orient="row"
    ).with_columns(
        available_at=pl.col("date").cast(pl.Datetime("us")).dt.replace_time_zone(str(TZ))
        + pl.duration(hours=18)
    )


def _prices(days: list[dt.date]) -> pl.DataFrame:
    return pl.DataFrame(
        [(d, s, 10_000) for d in days for s in _STOCKS],
        schema=["date", "stock_id", "volume"],
        orient="row",
    )


def _incremental(bt: pl.DataFrame, days: list[dt.date]) -> tuple[pl.DataFrame, pl.DataFrame]:
    """逐日：每天只看前一天與當天的明細，事件累加，當天排除已達門檻的分點。"""
    events: list[pl.DataFrame] = []
    nets: list[pl.DataFrame] = []
    for i, d in enumerate(days):
        today = bt.filter(pl.col("date") == d)
        if i > 0:
            events.append(mf.day_events(bt.filter(pl.col("date") == days[i - 1]), today, d))
        state = (
            pl.concat(events)
            .group_by("broker_id")
            .agg(pl.col("n_buys").sum(), pl.col("n_flip").sum())
            if events
            else pl.DataFrame(schema={"broker_id": pl.Utf8, "n_buys": pl.Int64, "n_flip": pl.Int64})
        )
        nets.append(mf.day_net(today, mf.flagged(state, 0.5, 4)))
    return pl.concat(events), pl.concat(nets)


def test_t425_ac1_events_match_batch() -> None:
    """逐日算的隔日沖事件 = factors/broker.py 一次算全期的事件。"""
    days = _days(30)
    bt = _panel(days)
    view = _View({"broker_trades": bt, "prices_daily": _prices(days)})
    batch = (
        daytrade_events(view)  # type: ignore[arg-type]
        .collect()
        .rename({"_b": "n_buys", "_f": "n_flip"})
        .with_columns(pl.col("n_buys").cast(pl.Int64), pl.col("n_flip").cast(pl.Int64))
        .select("broker_id", "resolve_date", "n_buys", "n_flip")
        .sort("broker_id", "resolve_date")
    )
    inc, _ = _incremental(bt, days)
    assert inc.sort("broker_id", "resolve_date").equals(batch)
    assert batch["n_flip"].sum() > 0


def test_t425_ac2_net_matches_clean_daily() -> None:
    """逐日的主力淨買超 = clean_daily（全歷史 expanding 隔日沖排除）；而且真的有分點被排除。"""
    days = _days(30)
    bt = _panel(days)
    view = _View({"broker_trades": bt, "prices_daily": _prices(days)})
    batch = (
        clean_daily(view, _PARAMS)  # type: ignore[arg-type]
        .collect()
        .select("date", "stock_id", pl.col("_net").cast(pl.Int64).alias("net_shares"))
        .sort("date", "stock_id")
    )
    _, inc = _incremental(bt, days)
    assert inc.select("date", "stock_id", "net_shares").sort("date", "stock_id").equals(batch)
    raw = bt.group_by("date", "stock_id").agg((pl.col("buy_shares") - pl.col("sell_shares")).sum())
    assert not raw.sort("date", "stock_id")["buy_shares"].equals(batch["net_shares"])


def test_t425_ac3_deterministic() -> None:
    days = _days(20)
    bt = _panel(days)
    a, b = _incremental(bt, days), _incremental(bt, days)
    assert a[0].equals(b[0]) and a[1].equals(b[1])


def test_t425_ac4_flagged_threshold() -> None:
    state = pl.DataFrame(
        {"broker_id": ["X", "Y", "Z"], "n_buys": [10, 3, 10], "n_flip": [5, 3, 4]},
        schema={"broker_id": pl.Utf8, "n_buys": pl.Int64, "n_flip": pl.Int64},
    )
    # X：10 次、一半倒 → 達標；Y：比率 1 但次數不夠；Z：比率 0.4 不夠
    assert mf.flagged(state, 0.5, 4).to_list() == ["X"]


def test_t425_ac5_tables_registered() -> None:
    assert {"broker_daytrade_events", "main_force_daily"} <= set(ALL_TABLES)
    assert spec_of("main_force_daily").pk == ("date", "stock_id")
    assert spec_of("broker_daytrade_events").pk == ("broker_id", "resolve_date")


def test_t425_ac6_naive_now_rejected() -> None:
    with pytest.raises(ValueError, match="時區"):
        mf.refresh_day(None, "serving", dt.date(2025, 3, 3), _PARAMS, dt.datetime(2025, 3, 3))
