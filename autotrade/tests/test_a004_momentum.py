"""A-004 動能順勢轉接的驗收測試（SPEC.md §4）。用 tests/fake_original.py 的假訊號，不執行第三方程式。"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from tools.parity_momentum import autotrade_rows, differences, reference_rows

from autotrade.broker.sim import SimBroker
from autotrade.config import Config, ConfigError, load_config, parse_config
from autotrade.engine import Engine
from autotrade.model import TAIPEI, Tick
from autotrade.strategy.momentum import MomentumStrategy, StrategyStateError, load_original, to_us
from fake_original import fake_original

CONFIG_PATH = Path(__file__).parents[1] / "configs" / "tmf_sim.toml"
OPEN = datetime(2026, 11, 13, 8, 45, 0, tzinfo=TAIPEI)


def cfg() -> Config:
    return load_config(CONFIG_PATH)


def day_ticks(seed: int, days: int = 0, until_hour: int = 13, until_minute: int = 20) -> list[Tick]:
    """一天的假行情：08:45 起到 13:20，間隔 0～3 秒，同一秒內每筆加 1 微秒（同重播行情）。"""
    rng = random.Random(seed)
    ts = OPEN + timedelta(days=days)
    end = ts.replace(hour=until_hour, minute=until_minute)
    price, same_second, out = 27000.0, 0, []
    while ts < end:
        step = rng.choice([0, 0, 1, 1, 2, 3])
        if step == 0:
            same_second += 1
        else:
            ts, same_second = ts + timedelta(seconds=step), 0
        price += rng.choice([-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0])
        out.append(
            Tick(
                ts=ts + timedelta(microseconds=same_second), price=price, qty=rng.choice([2, 4, 6])
            )
        )
    return out


def strategy_of(engine: Engine) -> MomentumStrategy:
    assert isinstance(engine.strategy, MomentumStrategy)
    return engine.strategy


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_ac1_trades_identical_to_reference_engine(seed: int) -> None:
    config, ticks = cfg(), day_ticks(seed)
    expected, exp_signals, exp_blocked = reference_rows(*fake_original(), config, "202611", ticks)
    got, got_signals, got_blocked, engine = autotrade_rows(*fake_original(), config, ticks)
    assert differences(expected, got) == []
    assert len(expected) >= 2 and exp_signals == got_signals and exp_blocked == got_blocked
    assert engine.ledger.realized_twd == sum(row[5] for row in expected)
    assert engine.finish()["fills"] == 2 * len(expected)
    assert not [e for e in engine.events if e.kind in ("reject", "halt", "cancel")]


def test_ac1_reference_exercises_every_rule() -> None:
    """假訊號要真的走到各條帳戶規則，否則上面的「一致」沒有說服力。"""
    blocked: set[str] = set()
    reasons: set[str] = set()
    for seed in range(1, 6):
        rows, _, day_blocked = reference_rows(*fake_original(), cfg(), "202611", day_ticks(seed))
        blocked |= {key.split(":", 1)[1] for key in day_blocked}
        reasons |= {row[4] for row in rows}
    assert blocked >= {
        "REENTRY_COOLDOWN",
        "STRUCTURAL_STOP_INVALID",
        "STRUCTURAL_STOP_TOO_WIDE",
        "CHASE_TOO_HIGH",
        "WAVE_TOO_EXTENDED",
        "REVALIDATION:MOMENTUM_FADED",
        "SYMBOL_ENTRY_LIMIT",
    }
    assert len(reasons) >= 2


def test_ac2_two_days_reset_state_and_match_reference() -> None:
    config = cfg()
    mods, params = fake_original()
    strategy = MomentumStrategy(mods, params, config.momentum, config.contract)  # type: ignore[arg-type]
    engine = Engine(config, strategy, SimBroker(config.sim))
    expected = []
    for days, seed in [(0, 7), (3, 8)]:
        ticks = day_ticks(seed, days=days)
        expected += reference_rows(*fake_original(), config, "202611", ticks)[0]
        for tick in ticks:
            engine.on_tick(tick)
    assert mods["intraday"].pruned == 2
    assert [t["net_pnl"] for t in strategy.trades] == [row[5] for row in expected]


def test_ac3_rejected_entry_releases_pending_and_strategy_retries() -> None:
    base = cfg()
    config = replace(base, risk=replace(base.risk, max_entries_per_day=1))
    ticks = day_ticks(1)
    _, _, _, engine = autotrade_rows(*fake_original(), config, ticks)
    strategy = strategy_of(engine)
    rejects = [e for e in engine.events if e.kind == "reject"]
    assert len(strategy.trades) == 1 and len(rejects) >= 1 and "達上限" in rejects[0].text
    assert strategy._pending is None and engine.ledger.position == 0


def test_ac4_system_flatten_is_reflected_in_strategy_state() -> None:
    config = cfg()
    mods, params = fake_original()
    strategy = MomentumStrategy(mods, params, config.momentum, config.contract)  # type: ignore[arg-type]
    engine = Engine(config, strategy, SimBroker(config.sim))
    ticks = iter(day_ticks(2))
    for tick in ticks:
        engine.on_tick(tick)
        if engine.ledger.position == 1 and strategy._pending is None:
            engine.kill(tick.ts)
            break
    for _, tick in zip(range(20), ticks, strict=False):
        engine.on_tick(tick)
    assert engine.ledger.position == 0 and strategy._position is None
    assert strategy.trades[-1]["exit_reason"] == "SYSTEM_FLATTEN"
    assert strategy.trades[-1]["net_pnl"] == engine.ledger.realized_twd


def test_ac5_position_mismatch_and_orphan_fill_raise() -> None:
    config = cfg()
    strategy = MomentumStrategy(*fake_original(), config.momentum, config.contract)  # type: ignore[arg-type]
    tick = day_ticks(1)[0]
    with pytest.raises(StrategyStateError, match="策略記的部位 0 口跟實際部位 1 口不同"):
        strategy.on_tick(tick, position=1)
    from autotrade.model import Fill, Side

    fill = Fill(ts=tick.ts, symbol="TMF", side=Side.BUY, qty=1, price=27000.0)
    with pytest.raises(StrategyStateError, match="沒有對應進場委託"):
        strategy.on_fill(fill)
    with pytest.raises(StrategyStateError, match="沒有部位卻收到賣出"):
        strategy.on_fill(replace(fill, side=Side.SELL))


def test_ac6_time_conversion_is_exact() -> None:
    ts = datetime(2026, 11, 13, 9, 0, 0, 123457, tzinfo=TAIPEI)
    assert to_us(ts) == 1_794_531_600_123_457
    assert to_us(ts + timedelta(microseconds=1)) - to_us(ts) == 1


ORIGINAL_STUB = """
class {name}:
    def validate(self):
        self.validated = True
"""


def test_ac7_load_original_picks_latest_versions(tmp_path: Path) -> None:
    repo = tmp_path / "ac7_fake_repo"
    repo.mkdir()
    classes = {
        "intraday": "IntradayParameters",
        "trading": "TradingParameters",
        "market": "MarketParameters",
    }
    for key in ("intraday", "market", "scanner", "trading", "api_gateway"):
        body = ORIGINAL_STUB.format(name=classes.get(key, "Unused"))
        (repo / f"{key}_v91_2.py").write_text("VERSION = 'old'\n" + body, encoding="utf-8")
        (repo / f"{key}_v91_10.py").write_text("VERSION = 'new'\n" + body, encoding="utf-8")
    mods, params = load_original(repo)
    assert {mods[k].VERSION for k in classes} == {"new"}
    assert all(params[k].validated for k in classes)
    with pytest.raises(FileNotFoundError, match="不存在"):
        load_original(tmp_path / "missing")
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError, match="找不到 intraday"):
        load_original(tmp_path / "empty")


def test_ac8_momentum_config_is_validated() -> None:
    import tomllib

    raw = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    raw["momentum"]["contracts"] = 2
    with pytest.raises(ConfigError, match=r"momentum\.contracts=2"):
        parse_config(raw)
    del raw["momentum"]
    assert parse_config(raw).momentum is None


def test_ac9_cli_builds_momentum_strategy_only_with_repo() -> None:
    from autotrade.cli import build_strategy

    with pytest.raises(ValueError, match="--ms-repo"):
        build_strategy("momentum", cfg(), None)
    with pytest.raises(ValueError, match="不認得的策略"):
        build_strategy("other", cfg(), None)
