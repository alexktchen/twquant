"""D-153 §一 補充二：零股回測——`run_backtest(lot_size=1)`。

整張（預設）行為逐位元不變；零股讓「一格買不起一張」的高價股也買得到、股數精確到股；
結果包記 `lot_size`。散戶會買零股，整張進位是引擎的限制不是投資人的限制。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

import twquant.factors.builtins  # noqa: F401
from tests.conftest import write_stock_meta
from twquant.backtest import costs as ct
from twquant.backtest.engine import DEFAULT_LOT_SIZE, BacktestResult, run_backtest
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.reporting.result_bundle import bundle_backtest
from twquant.strategy.config import StrategyConfig

BASE = dt.date(2026, 3, 2)
N_DAYS = 40
START = 25
CAPITAL = 1_000_000.0  # 兩格各 50 萬：700 元的股票一張 70 萬整張買不起、零股買得起
PRICES = {"1101": 100.0, "2330": 700.0}


def _close(sid: str, i: int) -> float:
    return PRICES[sid] * 1.01**i  # 兩檔都穩定上漲，20 日動能都過門檻


def _wh(tmp_path: Path) -> Warehouse:
    rows = []
    for i in range(N_DAYS):
        d = BASE + dt.timedelta(days=i)
        for sid in PRICES:
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


_CFG = StrategyConfig.model_validate(
    {
        "name": "lot_bt",
        "signals": [{"factor": "price.mom_20d", "weight": 1.0}],
        "universe": {"min_adv_twd": None, "min_price": None, "max_jump_ratio": None},
        "overlays": [],
        "exit_rules": [],
        "slots": {"n": 2, "entry_threshold": 0.05},
    }
)
_START, _END = BASE + dt.timedelta(days=START), BASE + dt.timedelta(days=N_DAYS - 1)


def _run(tmp_path: Path, lot_size: int | None = None) -> BacktestResult:
    """`lot_size=None`＝不傳，走 `run_backtest` 自己的預設（ac3 要驗的就是那個預設）。"""
    args = (_CFG, ct.CostConfig(), _START, _END, _wh(tmp_path))
    if lot_size is None:
        return run_backtest(*args, initial_capital=CAPITAL)
    return run_backtest(*args, initial_capital=CAPITAL, lot_size=lot_size)


def _buys(res: BacktestResult) -> dict[str, int]:
    return {t.stock_id: t.shares for t in res.ledger if t.side == "buy"}


def test_ac1_whole_lots_by_default_skip_a_name_one_lot_cannot_afford(tmp_path: Path) -> None:
    res = _run(tmp_path)
    buys = _buys(res)
    assert "2330" not in buys, "一格 50 萬買不起 700 元一張（70 萬）——整張模式應跳過"
    assert any(u.stock_id == "2330" and u.reason == "lot" for u in res.unfilled)
    assert buys["1101"] % DEFAULT_LOT_SIZE == 0


def test_ac2_odd_lot_buys_the_exact_affordable_share_count(tmp_path: Path) -> None:
    res = _run(tmp_path, lot_size=1)
    buys = _buys(res)
    price = _close("2330", START + 1)  # 隔日開盤成交
    want = int((CAPITAL / 2) // price)  # 一格 50 萬 ÷ 開盤價＝551 股
    # 1101 先成交、把它那一格用滿，剩下的現金要扣手續費——差個位數的股數是現金約束，不是進位
    assert want - 5 <= buys["2330"] <= want
    assert buys["2330"] % DEFAULT_LOT_SIZE != 0
    assert not any(u.reason == "lot" for u in res.unfilled)


def test_ac3_explicit_whole_lot_is_bit_identical_to_the_default(tmp_path: Path) -> None:
    a = _run(tmp_path / "a")
    b = _run(tmp_path / "b", lot_size=DEFAULT_LOT_SIZE)
    assert a.ledger == b.ledger
    assert a.final_equity == b.final_equity


def test_ac4_lot_size_below_one_share_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="lot_size"):
        _run(tmp_path, lot_size=0)


def test_ac5_bundle_records_lot_size_and_defaults_to_whole_lots(tmp_path: Path) -> None:
    res = _run(tmp_path, lot_size=1)
    common = {
        "strategy": _CFG,
        "cost": ct.CostConfig(),
        "start": _START,
        "end": _END,
        "initial_capital": CAPITAL,
        "max_participation": 0.05,
        "source": "lake",
        "data_fingerprint": "0" * 32,
    }
    assert bundle_backtest(res, lot_size=1, **common)["lot_size"] == 1  # type: ignore[arg-type]
    assert bundle_backtest(res, **common)["lot_size"] == DEFAULT_LOT_SIZE  # type: ignore[arg-type]
