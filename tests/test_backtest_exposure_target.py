"""T-286／D-145 §二：每日補位模式的每日曝險目標。

V1 的 `vol_target_scaling` overlay 只在月頻再平衡路徑生效；每日補位模式從不呼叫它，
每格固定 `pv / n`。這裡讓 slots 模式吃同一個倍數：新進場每格 `pv × mult / n`，
現有持股在曝險超過目標一格（1/n）以上時等比減碼。
"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import polars as pl
import pytest

from tests.conftest import write_stock_meta
from twquant.backtest.costs import CostConfig
from twquant.backtest.engine import BacktestResult, run_backtest
from twquant.config import load_config
from twquant.conventions import TZ
from twquant.data.warehouse import Warehouse
from twquant.strategy.config import ExposureTargetConfig, StrategyConfig

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"
_STOCKS = ("1101", "2330", "2454")
_CAPITAL = 30_000_000.0
_WARMUP = 40  # 回測起點前的歷史，讓波動估計窗（20）算得出來


def _sessions(n: int, start: dt.date = dt.date(2025, 1, 6)) -> list[dt.date]:
    out: list[dt.date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _wh(
    tmp_path: Path, days: list[dt.date], *, swing: float, phases: tuple[int, ...] = (0, 0, 0)
) -> Warehouse:
    """三檔股票，價格每天交替 ±swing。`phases` 讓各檔錯開相位（預設同向齊漲齊跌，
    組合波動＝個股波動；錯開後個股出場才會不同步——ac4 需要這一點）。"""
    rows = []
    for i, d in enumerate(days):
        for sid, ph in zip(_STOCKS, phases, strict=True):
            c = 100.0 * (1.0 + swing if (i + ph) % 2 else 1.0 - swing)
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": c,
                    "high": c * 1.001,
                    "low": c * 0.999,
                    "close": c,
                    "volume": 50_000_000,
                    "amount": c * 50_000_000,
                    "limit_up_price": None,
                    "limit_down_price": None,
                    "available_at": dt.datetime(d.year, d.month, d.day, 18, 0, tzinfo=TZ),
                }
            )
    px = pl.DataFrame(rows).with_columns(
        pl.col("volume").cast(pl.Int64),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", px, source="twse")
    write_stock_meta(wh, px)
    return wh


def _cfg(
    target: ExposureTargetConfig | None, exit_rules: list[dict[str, object]] | None = None
) -> StrategyConfig:
    slots: dict[str, object] = {"n": 3, "entry_threshold": 0.0}
    if target is not None:
        slots["exposure_target"] = target.model_dump()
    return StrategyConfig.model_validate(
        {
            "name": "expo_test",
            "description": "每日曝險目標測試",
            "trial_scope": "test",
            "universe": {"markets": ["TWSE"], "min_adv_twd": None, "min_price": None},
            "signals": [{"factor": "volume.adv_20d", "weight": 1.0}],
            "slots": slots,
            "exit_rules": exit_rules or [{"kind": "time_stop", "sessions": 200}],
        }
    )


def _run(
    wh: Warehouse,
    days: list[dt.date],
    target: ExposureTargetConfig | None,
    exit_rules: list[dict[str, object]] | None = None,
) -> BacktestResult:
    return run_backtest(
        _cfg(target, exit_rules),
        load_config(_CONFIG_DIR / "costs.yaml", CostConfig),
        days[_WARMUP],
        days[-1],
        wh,
        initial_capital=_CAPITAL,
    )


def _exposure(res: BacktestResult) -> list[float]:
    return [p.positions_value / p.equity for p in res.equity if p.equity > 0]


def test_ac1_multiplier_one_is_bit_identical_to_no_target(tmp_path: Path) -> None:
    """目標波動遠高於實際 → 倍數恆為 1.0 → 與沒有 exposure_target 的回測逐位元相同。"""
    days = _sessions(100)
    wh = _wh(tmp_path, days, swing=0.01)
    base = _run(wh, days, None)
    same = _run(wh, days, ExposureTargetConfig(target_vol=10.0, window=20, min_mult=0.1))
    assert [p.equity for p in same.equity] == [p.equity for p in base.equity]
    assert len(same.ledger) == len(base.ledger)
    assert not [t for t in same.ledger if t.reason == "exposure_target"]


def test_ac2_high_volatility_cuts_exposure_and_records_trims(tmp_path: Path) -> None:
    """個股每天 ±5% 交替（年化約 80%），目標 16% → 倍數壓到下限 0.10 附近，
    曝險必須明顯低於滿倉，而且減碼賣出要以 `exposure_target` 進 ledger。"""
    days = _sessions(100)
    wh = _wh(tmp_path, days, swing=0.05)
    full = _run(wh, days, None)
    cut = _run(wh, days, ExposureTargetConfig(target_vol=0.16, window=20, min_mult=0.10))
    e_full, e_cut = _exposure(full), _exposure(cut)
    assert sum(e_full) / len(e_full) > 0.9, "對照組應接近滿倉"
    assert sum(e_cut) / len(e_cut) < 0.5, f"減碼後平均曝險仍有 {sum(e_cut) / len(e_cut):.0%}"
    trims = [t for t in cut.ledger if t.reason == "exposure_target"]
    assert trims, "沒有任何減碼賣出進 ledger"
    assert all(t.side == "sell" for t in trims)


def test_ac3_exposure_stays_within_one_slot_of_the_target(tmp_path: Path) -> None:
    """減碼後的曝險不得超過目標一格（1/n）以上——超過就該再減。"""
    days = _sessions(100)
    wh = _wh(tmp_path, days, swing=0.05)
    n = 3
    cut = _run(wh, days, ExposureTargetConfig(target_vol=0.16, window=20, min_mult=0.10))
    # 減碼隔日執行，故從第三個交易日起檢查；允許整張進位的一格餘裕
    for p in cut.equity[3:]:
        assert p.positions_value / p.equity <= 0.10 + 1.0 / n + 0.02, (
            f"{p.date} 曝險 {p.positions_value / p.equity:.0%} 超過目標一格以上"
        )


def test_ac4_new_entries_are_sized_by_the_multiplier(tmp_path: Path) -> None:
    """減碼狀態下的新進場每格是 pv × mult / n，不是 pv / n。

    三檔錯開相位＋停損 3%：買在高點的那檔隔天 −5% 觸發停損、再隔天補位回來——
    此時其餘兩檔仍在手上，倍數已壓到 0.10，補位的那格必須明顯小於第一天的格子。
    （若三檔同步出場，持股會清空、倍數回到 1.0，就測不到這件事。）
    """
    days = _sessions(100)
    wh = _wh(tmp_path, days, swing=0.05, phases=(0, 1, 1))
    cut = _run(
        wh,
        days,
        ExposureTargetConfig(target_vol=0.16, window=20, min_mult=0.10),
        exit_rules=[{"kind": "stop_loss", "pct": 0.03}, {"kind": "time_stop", "sessions": 200}],
    )
    buys = [t for t in cut.ledger if t.side == "buy"]
    assert buys
    first_day = buys[0].date
    later = [t for t in buys if t.date > first_day]
    assert later, "後續沒有再進場（停損後應補位）"
    # 第一天持股為空 → 倍數 1.0 → 每格 pv/3；之後倍數壓到 0.10 → 每格明顯縮小
    first_notional = max(t.shares * t.price for t in buys if t.date == first_day)
    later_notional = max(t.shares * t.price for t in later)
    assert later_notional < first_notional * 0.5


def test_ac5_config_validation() -> None:
    with pytest.raises(ValueError):
        ExposureTargetConfig(target_vol=0.0, window=20, min_mult=0.1)
    with pytest.raises(ValueError):
        ExposureTargetConfig(target_vol=0.16, window=1, min_mult=0.1)
    with pytest.raises(ValueError):
        ExposureTargetConfig(target_vol=0.16, window=20, min_mult=1.5)
    ok = ExposureTargetConfig(target_vol=0.16, window=60, min_mult=0.10)
    assert math.isclose(ok.target_vol, 0.16)
