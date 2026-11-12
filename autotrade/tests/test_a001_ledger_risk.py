"""A-001 帳務與風控的驗收測試（SPEC.md §4）。"""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest

from autotrade.config import ConfigError, ContractConfig, RiskConfig, load_config, parse_config
from autotrade.ledger import Ledger, ReconcileError
from autotrade.model import TAIPEI, Fill, OrderRequest, Side, Tick
from autotrade.risk import HALT_DAILY_LOSS, HALT_KILL, RiskManager

SYMBOL = "TMF"
CONTRACT = ContractConfig(symbol=SYMBOL, point_value=10.0, commission_twd=10.0, tax_rate=0.00002)
T0 = datetime(2026, 11, 13, 9, 0, 0, tzinfo=TAIPEI)


def risk_cfg(limit: float | None = 1000.0) -> RiskConfig:
    return RiskConfig(
        min_position=0,
        max_position=1,
        max_order_qty=1,
        max_entries_per_day=2,
        entry_start=time(8, 45),
        entry_end=time(13, 0),
        max_quote_age_s=5.0,
        daily_loss_limit_twd=limit,
    )


def fill(side: Side, price: float, ts: datetime = T0, qty: int = 1) -> Fill:
    return Fill(ts=ts, symbol=SYMBOL, side=side, qty=qty, price=price)


def order(side: Side, ts: datetime = T0, qty: int = 1) -> OrderRequest:
    return OrderRequest(ts=ts, symbol=SYMBOL, side=side, qty=qty)


def ready(limit: float | None = 1000.0) -> RiskManager:
    rm = RiskManager(risk_cfg(limit))
    rm.on_quote(T0)
    return rm


def test_ac1_round_trip_pnl_and_costs() -> None:
    led = Ledger(CONTRACT)
    led.apply(fill(Side.BUY, 27000.0))
    assert led.position == 1 and led.avg_price == 27000.0
    assert led.open_pnl(27030.0) == 300.0
    led.apply(fill(Side.SELL, 27050.0))
    tax_buy = round(27000.0 * 10.0 * 0.00002)  # 5
    tax_sell = round(27050.0 * 10.0 * 0.00002)  # 5
    costs = 10.0 * 2 + tax_buy + tax_sell
    assert led.position == 0 and led.avg_price == 0.0
    assert led.costs_twd == costs
    assert led.realized_twd == 50 * 10.0 - costs


def test_ac1_wrong_symbol_raises() -> None:
    led = Ledger(CONTRACT)
    with pytest.raises(ValueError, match="MXF"):
        led.apply(Fill(ts=T0, symbol="MXF", side=Side.BUY, qty=1, price=27000.0))


def test_ac2_reconcile_mismatch_raises_with_both_numbers() -> None:
    led = Ledger(CONTRACT)
    led.apply(fill(Side.BUY, 27000.0))
    led.reconcile(1)
    with pytest.raises(ReconcileError, match=r"帳上 1 口.*券商回報 0 口"):
        led.reconcile(0)


def test_ac3_position_limits() -> None:
    rm = ready()
    assert rm.check(order(Side.BUY), position=0, now=T0).allowed
    over = rm.check(order(Side.BUY), position=1, now=T0)
    assert not over.allowed and "超出範圍" in over.reason
    short = rm.check(order(Side.SELL), position=0, now=T0)
    assert not short.allowed and "超出範圍" in short.reason
    big = rm.check(order(Side.BUY, qty=2), position=0, now=T0)
    assert not big.allowed and "單筆" in big.reason


def test_ac3_invariant_position_stays_within_limits() -> None:
    rng = random.Random(20261112)
    cfg = risk_cfg(limit=None)
    rm = RiskManager(cfg)
    position = 0
    for step in range(2000):
        now = T0 + timedelta(seconds=step)
        rm.on_quote(now)
        req = order(rng.choice([Side.BUY, Side.SELL]), ts=now, qty=rng.choice([1, 2]))
        if rm.check(req, position, now).allowed:
            position += req.signed_qty
        assert cfg.min_position <= position <= cfg.max_position


def test_ac4_daily_loss_limit_halts_new_entries() -> None:
    rm = ready(limit=1000.0)
    rm.update_pnl(-999.0, T0)
    assert rm.halt is None
    rm.update_pnl(-1000.0, T0)
    assert rm.halt == HALT_DAILY_LOSS
    blocked = rm.check(order(Side.BUY), position=0, now=T0)
    assert not blocked.allowed and "停手" in blocked.reason


def test_ac5_exits_always_allowed() -> None:
    late = T0.replace(hour=13, minute=20)
    rm = RiskManager(risk_cfg())  # 沒有行情、過了進場時段
    rm.kill()
    rm.entries_today = 99
    assert rm.check(order(Side.SELL, ts=late), position=1, now=late).allowed


def test_ac6_entry_window_and_count() -> None:
    rm = ready()
    early = T0.replace(hour=8, minute=44)
    rm.on_quote(early)
    assert "進場時段" in rm.check(order(Side.BUY, ts=early), 0, early).reason
    rm.on_quote(T0)
    rm.on_entry_sent()
    rm.on_entry_sent()
    assert "達上限" in rm.check(order(Side.BUY), 0, T0).reason


def test_ac7_stale_quote_blocks_entries() -> None:
    rm = RiskManager(risk_cfg())
    assert "還沒收到行情" in rm.check(order(Side.BUY), 0, T0).reason
    rm.on_quote(T0)
    later = T0 + timedelta(seconds=6)
    stale = rm.check(order(Side.BUY, ts=later), 0, later)
    assert not stale.allowed and "沒更新" in stale.reason


def raw_config(mode: str) -> dict[str, object]:
    return {
        "mode": mode,
        "contract": {
            "symbol": SYMBOL,
            "point_value": 10.0,
            "commission_twd": 10.0,
            "tax_rate": 0.00002,
        },
        "risk": {
            "min_position": 0,
            "max_position": 1,
            "max_order_qty": 1,
            "max_entries_per_day": 6,
            "entry_start": "08:45:00",
            "entry_end": "13:00:00",
            "max_quote_age_s": 5.0,
        },
    }


def test_ac8_live_mode_requires_daily_loss_limit() -> None:
    assert parse_config(raw_config("sim")).risk.daily_loss_limit_twd is None
    with pytest.raises(ConfigError, match="daily_loss_limit_twd"):
        parse_config(raw_config("live"))


def test_ac8_shipped_sim_config_loads() -> None:
    cfg = load_config(Path(__file__).parents[1] / "configs" / "tmf_sim.toml")
    assert cfg.mode == "sim" and cfg.contract.symbol == SYMBOL and cfg.risk.max_position == 1


def test_ac9_naive_datetime_raises() -> None:
    naive = datetime(2026, 11, 13, 9, 0, 0)
    with pytest.raises(ValueError, match="沒有時區"):
        Tick(ts=naive, price=27000.0, qty=1)
    with pytest.raises(ValueError, match="沒有時區"):
        Fill(ts=naive, symbol=SYMBOL, side=Side.BUY, qty=1, price=27000.0)
    with pytest.raises(ValueError, match="沒有時區"):
        ready().check(order(Side.BUY), 0, naive)


def test_ac10_day_roll_resets_counters_but_not_kill() -> None:
    rm = ready(limit=1000.0)
    rm.check(order(Side.BUY), 0, T0)
    rm.on_entry_sent()
    rm.update_pnl(-2000.0, T0)
    assert rm.halt == HALT_DAILY_LOSS
    tomorrow = T0 + timedelta(days=1)
    rm.roll_day(tomorrow)
    assert rm.entries_today == 0 and rm.halt is None
    rm.kill()
    rm.roll_day(tomorrow + timedelta(days=1))
    assert rm.halt == HALT_KILL
    rm.release_kill()
    assert rm.halt is None
