"""T-247／D-120 §五：回測引擎的「每日補位」模式。

每天收盤：先判賣出 → 再看空位 → 過絕對門檻、未持有、自身賣出規則未觸發的候選依分數補位，
隔日開盤成交，每格＝當時權益 ÷ slots。沒有月底強制換股；沒有人過門檻就空手。
"""

from __future__ import annotations

import datetime as dt
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
from twquant.reporting.research_log import backtest_params_hash
from twquant.strategy.config import StrategyConfig

BASE = dt.date(2026, 3, 2)
N_DAYS = 70
START = 25
PEAK = 45  # 2330 漲到第 45 天，之後每天跌 3%
LATE = 40  # 2603 第 40 天起才開始漲


def _close(sid: str, i: int) -> float:
    if sid == "1101":
        return 100.0 * 1.01**i  # 穩定上漲：20 日動能恆約 +22%
    if sid == "2330":
        return 100.0 + 2.0 * i if i <= PEAK else (100.0 + 2.0 * PEAK) * 0.97 ** (i - PEAK)
    if sid == "2454":
        return 100.0  # 永遠不過門檻
    return 100.0 if i <= LATE else 100.0 * 1.02 ** (i - LATE)  # 2603


def _wh(tmp_path: Path) -> Warehouse:
    rows = []
    for i in range(N_DAYS):
        d = BASE + dt.timedelta(days=i)
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
                    "available_at": dt.datetime(d.year, d.month, d.day, 18, tzinfo=TZ),
                }
            )
    df = pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(schemas.TW_DT),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", df, source="twse")
    write_stock_meta(wh, df)
    return wh


def _cfg(
    exit_rules: list[dict[str, object]] | None = None, *, n: int = 2, threshold: float = 0.05
) -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            "name": "slot_bt",
            "signals": [{"factor": "price.mom_20d", "weight": 1.0}],
            "universe": {"min_adv_twd": None, "min_price": None, "max_jump_ratio": None},
            "overlays": [],
            "exit_rules": exit_rules or [],
            "slots": {"n": n, "entry_threshold": threshold},
        }
    )


def _run(tmp_path: Path, cfg: StrategyConfig) -> BacktestResult:
    return run_backtest(
        cfg,
        ct.CostConfig(),
        BASE + dt.timedelta(days=START),
        BASE + dt.timedelta(days=N_DAYS - 1),
        _wh(tmp_path),
        initial_capital=10_000_000.0,
    )


def _day(i: int) -> dt.date:
    return BASE + dt.timedelta(days=i)


def test_ac1_fills_slots_by_score_then_stops_buying(tmp_path: Path) -> None:
    res = _run(tmp_path, _cfg())
    buys = [t for t in res.ledger if t.side == "buy"]
    assert [(t.stock_id, t.date, t.reason) for t in buys] == [
        ("2330", _day(START + 1), "entry"),  # 分數較高者先買
        ("1101", _day(START + 1), "entry"),
    ]
    # 2603 第 40 天後也過門檻，但兩格已滿——不買、也不為了它賣掉別人
    assert [t for t in res.ledger if t.side == "sell"] == []


def test_ac2_nothing_passes_the_bar_means_cash_not_error(tmp_path: Path) -> None:
    res = _run(tmp_path, _cfg(threshold=10.0))
    assert res.ledger == []
    assert all(e.positions_value == 0.0 for e in res.equity)
    assert res.final_equity == pytest.approx(10_000_000.0)


def test_ac3_each_slot_is_equity_over_n(tmp_path: Path) -> None:
    res = _run(tmp_path, _cfg())
    first = next(t for t in res.ledger if t.stock_id == "2330")
    price = _close("2330", START + 1)
    assert first.price == pytest.approx(price)  # 隔日開盤成交
    assert first.shares == int((10_000_000.0 / 2) // (price * 1000)) * 1000


def test_ac4_freed_slot_is_refilled_only_after_the_sale_settles(tmp_path: Path) -> None:
    res = _run(tmp_path, _cfg([{"kind": "below_ma", "window": 5}]))
    sell = next(t for t in res.ledger if t.side == "sell" and t.stock_id == "2330")
    assert sell.date == _day(PEAK + 2)
    refill = [t for t in res.ledger if t.side == "buy" and t.date > sell.date]
    assert [(t.stock_id, t.date) for t in refill] == [("2603", _day(PEAK + 3))]


def test_ac5_a_name_whose_own_sell_rule_fires_is_not_bought(tmp_path: Path) -> None:
    """2330 賣出當下 20 日動能仍過門檻；但它在 5 日線下，買了隔天就會被賣——不買。"""
    res = _run(tmp_path, _cfg([{"kind": "below_ma", "window": 5}]))
    sell = next(t for t in res.ledger if t.side == "sell" and t.stock_id == "2330")
    i = (sell.date - BASE).days
    assert _close("2330", i) / _close("2330", i - 20) - 1.0 >= 0.05  # 分數上它還是候選
    assert [t for t in res.ledger if t.stock_id == "2330" and t.date > sell.date] == []


def test_ac6_trailing_ma_only_arms_after_the_peak_gain(tmp_path: Path) -> None:
    armed = _run(tmp_path / "a", _cfg([{"kind": "trailing_ma", "window": 5, "min_peak_gain": 0.2}]))
    sells = [t for t in armed.ledger if t.side == "sell"]
    assert [(t.stock_id, t.reason) for t in sells] == [("2330", "trailing_ma_5")]
    unarmed = _run(
        tmp_path / "b", _cfg([{"kind": "trailing_ma", "window": 5, "min_peak_gain": 0.5}])
    )
    assert [t for t in unarmed.ledger if t.side == "sell"] == []  # 最高只到 +25%，規則從未上膛


def test_ac7_no_lookahead_entry_uses_the_close_before_the_fill(tmp_path: Path) -> None:
    res = _run(tmp_path, _cfg([{"kind": "below_ma", "window": 5}]))
    late = next(t for t in res.ledger if t.stock_id == "2603")
    i = (late.date - BASE).days - 1  # 判斷日
    assert _close("2603", i) / _close("2603", i - 20) - 1.0 >= 0.05
    assert late.price == pytest.approx(_close("2603", i + 1))


def test_ac8_deterministic_and_hash_rules() -> None:
    def _hash(cfg: StrategyConfig) -> str:
        return backtest_params_hash(
            strategy=cfg,
            cost=ct.CostConfig(),
            start=BASE,
            end=BASE + dt.timedelta(days=10),
            initial_capital=1.0e7,
            max_participation=0.05,
            source="lake",
        )

    assert _hash(_cfg(n=2)) != _hash(_cfg(n=3))
    assert _hash(_cfg(threshold=0.05)) != _hash(_cfg(threshold=0.06))


def test_ac9_slot_config_is_validated() -> None:
    base = _cfg().model_dump()
    with pytest.raises(ValueError, match="恰好一個"):
        StrategyConfig.model_validate(
            base
            | {
                "signals": [
                    {"factor": "price.mom_20d", "weight": 0.5},
                    {"factor": "price.mom_60d", "weight": 0.5},
                ]
            }
        )
    with pytest.raises(ValueError):
        StrategyConfig.model_validate(base | {"slots": {"n": 0, "entry_threshold": 0.5}})
