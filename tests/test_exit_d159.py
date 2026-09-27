"""T-314／D-159 §四：回測引擎的三種新出場——布林下軌停損、ATR 移動停利、均線鎖利。

D-159 的設計：下軌負責「賠的提早出場」，停利線只在**高於本輪均價時**生效，
負責「賺的抱久」。兩條各管一邊，所以停利線低於均價時不得觸發。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import polars as pl
import pytest

import twquant.factors.builtins  # noqa: F401
from tests.conftest import write_stock_meta
from twquant.backtest import costs as ct
from twquant.backtest.engine import _price_panel, run_backtest
from twquant.backtest.exits import (
    HeldPosition,
    atr_column,
    entry_blocked,
    exit_reasons,
    ma_column,
    sd_column,
)
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.factors.compute import compute
from twquant.reporting.holdout import config_fingerprint
from twquant.strategy.config import ExitRuleConfig, StrategyConfig

DAY = dt.date(2026, 5, 4)
BB = ExitRuleConfig(kind="bb_lower_stop", window=20, mult=2.0)
ATR = ExitRuleConfig(kind="atr_trailing", window=14, mult=3.0)
MA = ExitRuleConfig(kind="ma_lock", window=20)


def _held(avg_cost: float, peak: float = 0.0, dps: float = 0.0) -> dict[str, HeldPosition]:
    return {
        "2330": HeldPosition(
            shares=1000, avg_cost=avg_cost, dividends_per_share=dps, peak_close=peak
        )
    }


def _fire(
    rule: ExitRuleConfig, held: dict[str, HeldPosition], row: dict[str, object]
) -> str | None:
    return exit_reasons([rule], held, {"2330": row}, None, DAY).get("2330")  # type: ignore[arg-type]


# ── AC-314-1 配置 ─────────────────────────────────────────────────────────────


def test_ac1_config_accepts_the_registered_parameters() -> None:
    assert BB.mult == 2.0 and ATR.mult == 3.0 and MA.window == 20


@pytest.mark.parametrize(
    ("kwargs", "msg"),
    [
        ({"kind": "bb_lower_stop", "window": 20}, r"exit_rules\[bb_lower_stop\]"),  # 缺 mult
        ({"kind": "atr_trailing", "mult": 3.0}, r"exit_rules\[atr_trailing\]"),  # 缺 window
        ({"kind": "ma_lock", "window": 20, "mult": 1.0}, r"exit_rules\[ma_lock\]"),  # 多 mult
        ({"kind": "stop_loss", "pct": 0.1, "mult": 1.0}, r"exit_rules\[stop_loss\]"),
        ({"kind": "atr_trailing", "window": 14, "mult": 0.0}, "mult 須 > 0"),
    ],
)
def test_ac1_config_rejects_mismatched_params(kwargs: dict[str, object], msg: str) -> None:
    with pytest.raises(ValueError, match=msg):
        ExitRuleConfig.model_validate(kwargs)


# ── AC-314-2 布林下軌停損 ─────────────────────────────────────────────────────


def test_ac2_bb_lower_stop_fires_only_below_the_lower_band() -> None:
    row = {ma_column(20): 100.0, sd_column(20): 5.0}  # 下軌 = 90
    assert _fire(BB, _held(100.0), {**row, "close": 89.0}) == "bb_lower_stop_20x2.0"
    assert _fire(BB, _held(100.0), {**row, "close": 90.0}) is None  # 貼著下軌不算跌破
    assert _fire(BB, _held(100.0), {**row, "close": 95.0}) is None


def test_ac2_bb_lower_stop_without_band_values_does_not_fire() -> None:
    """窗未滿（均線或標準差是 null）→ 不判斷，不得當成跌破。"""
    assert _fire(BB, _held(100.0), {"close": 1.0, ma_column(20): None, sd_column(20): 5.0}) is None
    assert _fire(BB, _held(100.0), {"close": 1.0, ma_column(20): 100.0}) is None


# ── AC-314-3 ATR 移動停利 ─────────────────────────────────────────────────────


def test_ac3_atr_trailing_fires_below_the_line_when_it_locks_a_profit() -> None:
    # 線 = 150 − 3 × 10 = 120 > 均價 100 → 已生效；收盤 119 < 120 → 賣
    row = {atr_column(14): 10.0}
    assert _fire(ATR, _held(100.0, peak=150.0), {**row, "close": 119.0}) == "atr_trailing_14x3.0"
    assert _fire(ATR, _held(100.0, peak=150.0), {**row, "close": 121.0}) is None


def test_ac3_atr_trailing_is_inactive_while_the_line_is_below_cost() -> None:
    """線 = 110 − 30 = 80 < 均價 100：這段由下軌停損負責，停利線不得觸發。"""
    assert _fire(ATR, _held(100.0, peak=110.0), {"close": 70.0, atr_column(14): 10.0}) is None


def test_ac3_atr_trailing_counts_dividends_towards_the_lock() -> None:
    """D-085：線 98 + 已收股利 5 > 均價 100 → 已生效。沒攤回股利就會漏掉這次鎖利。"""
    held = _held(100.0, peak=128.0, dps=5.0)  # 線 = 128 − 30 = 98
    assert _fire(ATR, held, {"close": 97.0, atr_column(14): 10.0}) == "atr_trailing_14x3.0"


def test_ac3_atr_trailing_needs_a_peak_and_an_atr() -> None:
    assert _fire(ATR, _held(100.0, peak=0.0), {"close": 50.0, atr_column(14): 1.0}) is None
    assert _fire(ATR, _held(100.0, peak=200.0), {"close": 50.0, atr_column(14): None}) is None


# ── AC-314-4 均線鎖利 ─────────────────────────────────────────────────────────


def test_ac4_ma_lock_fires_below_the_ma_only_when_the_ma_is_above_cost() -> None:
    assert _fire(MA, _held(100.0), {"close": 109.0, ma_column(20): 110.0}) == "ma_lock_20"
    # 均線 95 低於均價 100：跌破均線不賣（此時由下軌停損負責）
    assert _fire(MA, _held(100.0), {"close": 90.0, ma_column(20): 95.0}) is None
    assert _fire(MA, _held(100.0), {"close": 111.0, ma_column(20): 110.0}) is None


def test_ac4_rules_compose_with_the_first_listed_reason() -> None:
    """D-159 的 T 臂：下軌停損排前面，兩條同時成立時記停損。"""
    row = {"close": 80.0, ma_column(20): 110.0, sd_column(20): 5.0}
    got = exit_reasons([BB, MA], _held(100.0), {"2330": row}, None, DAY)  # type: ignore[arg-type]
    assert got == {"2330": "bb_lower_stop_20x2.0"}


# ── AC-314-5 價格面板的欄位 ───────────────────────────────────────────────────

BASE = dt.date(2026, 3, 2)
N_DAYS = 70
PEAK = 45


def _close(sid: str, i: int) -> float:
    if sid == "2330":
        top = 100.0 + 2.0 * PEAK
        return 100.0 + 2.0 * i if i <= PEAK else top * (0.97 ** (i - PEAK))
    return 100.0 + {"1101": 0.5, "2454": 0.25}[sid] * i


def _wh(tmp_path: Path) -> Warehouse:
    rows = []
    for i in range(N_DAYS):
        d = BASE + dt.timedelta(days=i)
        for sid in ("1101", "2330", "2454"):
            c = _close(sid, i)
            wiggle = 1.0 + 0.01 * ((i * 7 + len(sid)) % 3)  # 讓 H−L 不為 0，ATR 才有內容
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": c,
                    "high": c * wiggle,
                    "low": c / wiggle,
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


def test_ac5_panel_sd_is_the_population_std_of_close(tmp_path: Path) -> None:
    end = BASE + dt.timedelta(days=N_DAYS - 1)
    panel = _price_panel(_wh(tmp_path), BASE, end, (20,), (20,), ())
    closes = np.array([_close("2330", i) for i in range(N_DAYS)])
    i = 40
    got = panel[BASE + dt.timedelta(days=i)]["2330"]
    assert got[sd_column(20)] == pytest.approx(closes[i - 19 : i + 1].std(ddof=0), rel=1e-12)
    assert got[ma_column(20)] == pytest.approx(closes[i - 19 : i + 1].mean(), rel=1e-12)
    assert panel[BASE + dt.timedelta(days=5)]["2330"][sd_column(20)] is None


def test_ac5_panel_atr_matches_the_atr_14_factor(tmp_path: Path) -> None:
    """同一公式、同一份資料：面板 ATR ÷ 收盤 必須等於 `price.atr_14`。"""
    wh = _wh(tmp_path)
    end = BASE + dt.timedelta(days=N_DAYS - 1)
    panel = _price_panel(wh, BASE, end, (), (), (14,))
    day = BASE + dt.timedelta(days=50)
    fac = compute(["price.atr_14"], wh.view(day), dates=[day])
    for r in fac.filter(pl.col("value").is_not_null()).to_dicts():
        row = panel[day][r["stock_id"]]
        assert row[atr_column(14)] / row["close"] == pytest.approx(r["value"], rel=1e-9)
    assert panel[BASE + dt.timedelta(days=5)]["2330"][atr_column(14)] is None  # 未滿 14 筆


# ── AC-314-6 引擎整合 ─────────────────────────────────────────────────────────


def _cfg(exit_rules: list[dict[str, object]]) -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            "name": "d159_bt",
            "signals": [{"factor": "price.mom_20d", "weight": 1.0}],
            "selection": {"method": "top_n", "top_n": 2},
            "weighting": "equal",
            "max_weight_per_stock": 0.6,
            "universe": {"min_adv_twd": None, "min_price": None, "max_jump_ratio": None},
            "rebalance": {"schedule": "monthly"},
            "overlays": [],
            "exit_rules": exit_rules,
        }
    )


def test_ac6_engine_atr_trailing_sells_after_the_peak_at_a_profit(tmp_path: Path) -> None:
    wh = _wh(tmp_path)
    start, end = BASE + dt.timedelta(days=25), BASE + dt.timedelta(days=N_DAYS - 1)
    res = run_backtest(
        _cfg([{"kind": "atr_trailing", "window": 14, "mult": 3.0}]),
        ct.CostConfig(),
        start,
        end,
        wh,
        initial_capital=10_000_000.0,
    )
    sells = [t for t in res.ledger if t.stock_id == "2330" and t.reason == "atr_trailing_14x3.0"]
    assert len(sells) == 1
    sell = sells[0]
    assert sell.date > BASE + dt.timedelta(days=PEAK)  # 漲的時候不賣，讓獲利奔跑
    buys = [
        t for t in res.ledger if t.stock_id == "2330" and t.side == "buy" and t.date < sell.date
    ]
    avg = sum(t.price * t.shares for t in buys) / sum(t.shares for t in buys)
    assert sell.price > avg  # 鎖到的是利潤
    # 觸發日（前一個交易日）收盤確實跌破 最高收盤 − 3 × ATR
    panel = _price_panel(wh, BASE, end, (), (), (14,))
    trigger = sell.date - dt.timedelta(days=1)
    row = panel[trigger]["2330"]
    peak = max(_close("2330", i) for i in range((buys[0].date - BASE).days, PEAK + 1))
    assert row["close"] < peak - 3.0 * row[atr_column(14)]
    prev = panel[trigger - dt.timedelta(days=1)]["2330"]
    assert prev["close"] >= peak - 3.0 * prev[atr_column(14)]  # 前一天還沒觸發


# ── AC-314-7 登記指紋不受新欄位影響 ───────────────────────────────────────────


def test_ac7_unset_mult_does_not_change_the_strategy_fingerprint() -> None:
    base = _cfg([{"kind": "stop_loss", "pct": 0.15}])
    payload = base.model_dump(mode="json")
    assert payload["exit_rules"][0]["mult"] is None  # 新欄位確實出現在 dump 裡
    legacy = dict(payload)
    legacy["exit_rules"] = [
        {k: v for k, v in r.items() if k != "mult"} for r in payload["exit_rules"]
    ]
    import hashlib
    import json

    want = hashlib.sha256(
        json.dumps(legacy, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    assert config_fingerprint(base) == want
    set_mult = _cfg([{"kind": "stop_loss", "pct": 0.15}, {"kind": "ma_lock", "window": 20}])
    assert config_fingerprint(set_mult) != config_fingerprint(base)
    with_bb = _cfg([{"kind": "bb_lower_stop", "window": 20, "mult": 2.0}])
    assert "mult" in json.dumps(with_bb.model_dump(mode="json")["exit_rules"][0])


# ── AC-314-8 進場不變 ─────────────────────────────────────────────────────────


def test_ac8_new_exits_never_block_entries() -> None:
    """D-159：各臂進場逐字相同。下軌規則不需部位也算得出來，但不得拿來擋進場。"""
    row = {"close": 1.0, ma_column(20): 100.0, sd_column(20): 5.0, atr_column(14): 1.0}
    got = entry_blocked([BB, ATR, MA], ["2330"], {"2330": row}, None, DAY)  # type: ignore[arg-type]
    assert got == set()
