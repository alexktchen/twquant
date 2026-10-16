"""T-366／D-184：大盤緩跌閘門——分類（沿用 ml.regime）、引擎接線（補位不開新倉、換股日只賣不買）。"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import polars as pl
import pytest

import twquant.factors.builtins  # noqa: F401
from tests.conftest import write_stock_meta
from twquant.backtest import costs as ct
from twquant.backtest.engine import BacktestResult, run_backtest
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.errors import DataError
from twquant.ml.regime import CALM, GRIND, PANIC, market_regime
from twquant.strategy.config import MarketGateConfig, StrategyConfig
from twquant.strategy.market_gate import blocks_new_positions, gate_regime

BASE = dt.date(2026, 3, 2)
D124 = MarketGateConfig(ma_window=60, vol_window=20, vol_lookback=250, vol_pctl=0.90)
SMALL = MarketGateConfig(ma_window=5, vol_window=3, vol_lookback=20, vol_pctl=0.90)


def _day(i: int) -> dt.date:
    return BASE + dt.timedelta(days=i)


def _at(d: dt.date) -> dt.datetime:
    return dt.datetime(d.year, d.month, d.day, 18, tzinfo=TZ)


def _index_frame(closes: list[float]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [_day(i) for i in range(len(closes))],
            "index_id": ["TAIEX"] * len(closes),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "available_at": [_at(_day(i)) for i in range(len(closes))],
        }
    ).with_columns(pl.col("available_at").cast(schemas.TW_DT))


def _wiggly(n: int) -> list[float]:
    """有漲有跌、波動時大時小的指數：三種狀態都會出現。"""
    c = [10000.0]
    for i in range(1, n):
        amp = 0.004 + 0.02 * (0.5 + 0.5 * math.sin(i / 23.0)) ** 3
        r = amp * math.sin(i * 1.7) + 0.003 * math.sin(i / 41.0)
        if 330 <= i < 346:  # 急跌：大跌大彈、波動放大 → 恐慌
            r = -0.05 if i % 2 else 0.02
        c.append(c[-1] * (1.0 + r))
    return c


def test_ac1_same_as_classifying_the_whole_history(tmp_path: Path) -> None:
    closes = _wiggly(420)
    wh = Warehouse(tmp_path)
    wh.write("clean", "index_daily", _index_frame(closes))
    full = market_regime(
        _index_frame(closes).select("date", "close"),
        ma_window=60,
        vol_window=20,
        vol_lookback=250,
        vol_pctl=0.90,
    )
    want = dict(zip(full["date"], full["regime"], strict=True))
    seen = set()
    for i in range(270, 420):
        d = _day(i)
        got = gate_regime(wh.view(d), d, D124)
        assert got == want[d], d
        assert blocks_new_positions(wh.view(d), d, D124) == (got == GRIND)
        seen.add(got)
    assert seen == {CALM, GRIND, PANIC}, f"測試資料要涵蓋三種狀態，只有 {seen}"


def test_ac2_unknown_is_an_error_not_a_calm_day(tmp_path: Path) -> None:
    wh = Warehouse(tmp_path)
    wh.write("clean", "index_daily", _index_frame(_wiggly(100)))
    with pytest.raises(DataError, match="270"):
        gate_regime(wh.view(_day(99)), _day(99), D124)  # 暖機不足
    with pytest.raises(DataError, match="沒有"):
        gate_regime(wh.view(_day(150)), _day(150), SMALL)  # 那天沒有收盤
    other = SMALL.model_copy(update={"index_id": "TPEX"})
    with pytest.raises(DataError, match="TPEX"):
        gate_regime(wh.view(_day(99)), _day(99), other)


# ── 引擎：三檔股票都一路上漲（20 日動能永遠過門檻），只有指數決定能不能買 ──
N_DAYS = 90
CALM_UNTIL = 40  # 這天以前指數大漲小跌
GRIND_UNTIL = 62  # 這段指數小跌、波動小（緩跌）；之後大漲回到均線上


def _index_closes() -> list[float]:
    c = [10000.0]
    for i in range(1, N_DAYS):
        if i < CALM_UNTIL:
            r = 0.02 if i % 2 else -0.01
        elif i < GRIND_UNTIL:
            r = -0.002 if i % 2 else -0.006
        else:
            r = 0.03 if i % 2 else -0.005
        c.append(c[-1] * (1.0 + r))
    return c


TURN = 50  # 2603 漲到這天，之後每天跌 2%：緩跌期間前 1 名會換人


def _close(sid: str, i: int) -> float:
    if sid == "2603":
        return 100.0 * 1.01**i if i <= TURN else 100.0 * 1.01**TURN * 0.98 ** (i - TURN)
    k = {"1101": 1.0, "2330": 1.5, "2454": 2.0}[sid]
    return 100.0 * (1.0 + 0.004 * k) ** i


def _wh(tmp_path: Path) -> Warehouse:
    rows = []
    for i in range(N_DAYS):
        d = _day(i)
        for sid in ("1101", "2330", "2454", "2603"):
            c = _close(sid, i)
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": c,
                    "high": c,
                    "low": c,
                    "close": c,
                    "volume": 100_000,
                    "amount": 1.0e9,
                    "limit_up_price": None,
                    "limit_down_price": None,
                    "available_at": _at(d),
                }
            )
    df = pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(schemas.TW_DT),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", df, source="twse")
    wh.write("clean", "index_daily", _index_frame(_index_closes()))
    write_stock_meta(wh, df)
    return wh


def _regimes() -> dict[dt.date, str | None]:
    r = market_regime(
        _index_frame(_index_closes()).select("date", "close"),
        ma_window=SMALL.ma_window,
        vol_window=SMALL.vol_window,
        vol_lookback=SMALL.vol_lookback,
        vol_pctl=SMALL.vol_pctl,
    )
    return dict(zip(r["date"], r["regime"], strict=True))


def _slots(gate: bool) -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            "name": "gate_slots",
            "signals": [{"factor": "price.mom_20d", "weight": 1.0}],
            "universe": {"min_adv_twd": None, "min_price": None, "max_jump_ratio": None},
            "exit_rules": [{"kind": "time_stop", "sessions": 5}],
            "slots": {"n": 2, "entry_threshold": 0.0},
            **({"market_gate": SMALL.model_dump()} if gate else {}),
        }
    )


def _run(tmp_path: Path, cfg: StrategyConfig, start: int = 30) -> BacktestResult:
    return run_backtest(
        cfg, ct.CostConfig(), _day(start), _day(N_DAYS - 1), _wh(tmp_path), initial_capital=1.0e7
    )


def _prev(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=1)


def test_ac3_slots_open_nothing_on_grind_nights_and_resume_after(tmp_path: Path) -> None:
    reg = _regimes()
    window = [_day(i) for i in range(30, N_DAYS - 1)]
    grind = [d for d in window if reg[d] == GRIND]
    assert len(grind) >= 10 and any(reg[d] == CALM for d in window), "測試資料要有緩跌與一般日"
    free = _run(tmp_path / "free", _slots(gate=False))
    gated = _run(tmp_path / "gated", _slots(gate=True))
    free_buys = [t for t in free.ledger if t.side == "buy"]
    gated_buys = [t for t in gated.ledger if t.side == "buy"]
    assert any(reg[_prev(t.date)] == GRIND for t in free_buys), "沒有閘門時，緩跌夜也會補位"
    assert gated_buys and all(reg[_prev(t.date)] != GRIND for t in gated_buys)
    assert any(t.date > _day(GRIND_UNTIL) for t in gated_buys), "緩跌結束後要恢復補位"
    # 持股照出場規則走：緩跌期間還是會賣（抱滿 5 天）
    assert any(t.side == "sell" and reg[_prev(t.date)] == GRIND for t in gated.ledger)


def test_ac4_slots_without_gate_are_unchanged(tmp_path: Path) -> None:
    a = _run(tmp_path / "a", _slots(gate=False))
    same = StrategyConfig.model_validate({**_slots(False).model_dump(), "market_gate": None})
    b = _run(tmp_path / "b", same)
    assert [(t.date, t.stock_id, t.side, t.shares) for t in a.ledger] == [
        (t.date, t.stock_id, t.side, t.shares) for t in b.ledger
    ]


def _weekly(gate: bool, top_n: int) -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            "name": "gate_weekly",
            "signals": [{"factor": "price.mom_20d", "weight": 1.0}],
            "universe": {"min_adv_twd": None, "min_price": None, "max_jump_ratio": None},
            "selection": {"method": "top_n", "top_n": top_n},
            "weighting": "equal",
            "max_weight_per_stock": 1.0,
            "rebalance": {"schedule": "weekly", "day": "MON", "execution": "next_open"},
            **({"market_gate": SMALL.model_dump()} if gate else {}),
        }
    )


def test_ac5_rebalance_on_a_grind_night_only_sells(tmp_path: Path) -> None:
    reg = _regimes()
    # 從緩跌當中開始：第一次換股夜是緩跌 → 一張都不買；之後的一般日照常
    start = next(i for i in range(CALM_UNTIL + 5, GRIND_UNTIL) if reg[_day(i)] == GRIND)
    gated = _run(tmp_path / "g", _weekly(True, 2), start=start)
    free = _run(tmp_path / "f", _weekly(False, 2), start=start)
    sig_nights = sorted({_prev(t.date) for t in free.ledger})
    assert any(reg[d] == GRIND for d in sig_nights) and any(reg[d] == CALM for d in sig_nights)
    for t in gated.ledger:
        if t.side == "buy":
            assert reg[_prev(t.date)] != GRIND, t
    assert any(reg[_prev(t.date)] == GRIND for t in free.ledger if t.side == "buy")
    assert any(t.side == "buy" for t in gated.ledger), "一般日的換股照常買"


def test_ac6_rebalance_grind_night_still_sells_what_dropped_out(tmp_path: Path) -> None:
    """一般日買進前 1 名；緩跌的換股夜前 1 名換人 → 隔天只賣舊的、不買新的，留現金到下一個換股日。"""
    reg = _regimes()
    free = _run(tmp_path / "f", _weekly(False, 1))
    gated = _run(tmp_path / "g", _weekly(True, 1))
    switch = [t.date for t in free.ledger if t.side == "sell" and t.stock_id == "2603"]
    assert switch, "測試資料要讓前 1 名在緩跌期間換人"
    d = switch[0]
    assert reg[_prev(d)] == GRIND
    assert any(t.side == "buy" and t.date == d for t in free.ledger), "沒有閘門：賣舊買新"
    assert [(t.stock_id, t.side) for t in gated.ledger if t.date == d] == [("2603", "sell")]
    after = [e for e in gated.equity if d <= e.date < d + dt.timedelta(days=7)]
    assert after and all(e.positions_value == pytest.approx(0.0) for e in after)
