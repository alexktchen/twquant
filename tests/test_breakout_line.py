"""T-440／D-215：第四條線「帶量突破」——兩個新因子、寫死的規則、上架的每一處都接上。"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence
from pathlib import Path

import polars as pl
import pytest
import yaml

import twquant.factors.builtins  # noqa: F401
from twquant.backtest.costs import CostConfig
from twquant.config import load_config
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.factors.compute import compute
from twquant.factors.testing import assert_no_lookahead
from twquant.reporting.holdout import HoldoutRegistration, config_fingerprint, refuse_before_opening
from twquant.reporting.pick_board import PickBoardConfig
from twquant.reporting.pick_reasons import PickReasonsConfig
from twquant.strategy.config import load_strategy

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ROOT / "configs"
BASE = dt.date(2026, 1, 5)


def _day(i: int) -> dt.date:
    return BASE + dt.timedelta(days=i)


def _wh(
    tmp_path: Path,
    bars: Mapping[str, Sequence[tuple[float, float, float] | None]],
) -> Warehouse:
    """每檔每天 (high, close, volume)；None＝停牌那天沒有列。"""
    rows = []
    for sid, seq in bars.items():
        for i, bar in enumerate(seq):
            if bar is None:
                continue
            high, close, volume = bar
            d = _day(i)
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": close,
                    "high": high,
                    "low": min(close, high) * 0.98,
                    "close": close,
                    "volume": volume,
                    "amount": close * volume,
                    "limit_up_price": None,
                    "limit_down_price": None,
                    "available_at": dt.datetime(d.year, d.month, d.day, 18, tzinfo=TZ),
                }
            )
    wh = Warehouse(tmp_path)
    wh.write(
        "clean",
        "prices_daily",
        pl.DataFrame(rows).with_columns(
            pl.col("available_at").cast(schemas.TW_DT),
            pl.col("volume").cast(pl.Int64),
            pl.col("limit_up_price").cast(pl.Float64),
            pl.col("limit_down_price").cast(pl.Float64),
        ),
        source="twse",
    )
    return wh


def _val(wh: Warehouse, name: str, day: int, sid: str) -> float | None:
    d = _day(day)
    out = compute([name], wh.view(d), dates=[d]).filter(pl.col("stock_id") == sid)
    return None if out.is_empty() else float(out["value"][0])


# ---------- 因子 ----------


def test_ac1_breakout_is_close_over_prior_high_excluding_today(tmp_path: Path) -> None:
    flat = [(100.0, 98.0, 1000.0)] * 60
    wh = _wh(
        tmp_path,
        {
            "4577": [*flat, (103.0, 102.0, 4000.0)],  # 收 102 > 前高 100：+2%
            "1101": [*flat, (105.0, 99.0, 4000.0)],  # 當天衝到 105 但收 99：前高不含當天，仍在之下
            "2330": [*flat, (100.0, 100.0, 1000.0)],  # 剛好等於前高：0
        },
    )
    assert _val(wh, "price.breakout_high_60d", 60, "4577") == pytest.approx(0.02)
    assert _val(wh, "price.breakout_high_60d", 60, "1101") == pytest.approx(-0.01)
    assert _val(wh, "price.breakout_high_60d", 60, "2330") == pytest.approx(0.0)


def test_ac1b_prior_high_uses_high_not_close_and_needs_full_window(tmp_path: Path) -> None:
    flat = [(100.0, 98.0, 1000.0)] * 60
    wh = _wh(
        tmp_path,
        {
            # 第 10 天上影線到 120（收 98）：之後 60 天內都以 120 當前高
            "4577": [*flat[:10], (120.0, 98.0, 1000.0), *flat[:49], (110.0, 110.0, 1000.0)],
            "1101": [*flat[:30], (103.0, 102.0, 4000.0)],  # 只有 30 天：不給值
            "2330": [*flat, None, None, (103.0, 102.0, 4000.0)],  # 停牌兩天：比的是有交易的 60 天
        },
    )
    assert _val(wh, "price.breakout_high_60d", 60, "4577") == pytest.approx(110.0 / 120.0 - 1.0)
    assert _val(wh, "price.breakout_high_60d", 30, "1101") is None
    assert _val(wh, "price.breakout_high_60d", 62, "2330") == pytest.approx(0.02)


def test_ac2_vol_ratio_20d_is_volume_over_prior_20_day_mean(tmp_path: Path) -> None:
    flat = [(100.0, 98.0, 1000.0)] * 20
    wh = _wh(tmp_path, {"4577": [*flat, (103.0, 102.0, 4400.0), (104.0, 103.0, 1000.0)]})
    assert _val(wh, "volume.vol_ratio_20d", 20, "4577") == pytest.approx(4.4)
    # 隔天：前 20 天平均含昨天的 4,400 → (19×1000＋4400)/20＝1170
    assert _val(wh, "volume.vol_ratio_20d", 21, "4577") == pytest.approx(1000.0 / 1170.0)
    # 與五日量比同一個算法、只差窗口
    five = compute(
        ["volume.vol_ratio_5d"],
        wh.view(_day(20)),
        dates=[_day(20)],
        params={"volume.vol_ratio_5d": {"window": 20}},
    )
    assert float(five["value"][0]) == pytest.approx(4.4)


def test_ac3_no_lookahead_and_deterministic(tmp_path: Path) -> None:
    bars = {
        sid: [
            (
                100.0 + 10.0 * math.sin(i / (3.0 + k)) + 1.0,
                100.0 + 10.0 * math.sin(i / (3.0 + k)),
                1000.0 + 500.0 * (i % 7),
            )
            for i in range(90)
        ]
        for k, sid in enumerate(("1101", "2330", "4577"))
    }
    wh = _wh(tmp_path, bars)
    for name in ("price.breakout_high_60d", "volume.vol_ratio_20d"):
        assert_no_lookahead(name, wh, [_day(i) for i in (65, 70, 80, 89)])
        a = compute([name], wh.view(_day(89)), dates=[_day(89)])
        b = compute([name], wh.view(_day(89)), dates=[_day(89)])
        assert a.equals(b)


# ---------- 規則與登記 ----------


def _reg() -> HoldoutRegistration:
    return load_config(CONFIGS / "holdout2_breakout.yaml", HoldoutRegistration)


def test_ac4_rules_are_exactly_the_registered_ones() -> None:
    cfg = load_strategy(str(CONFIGS / "strategies" / "breakout_v20.yaml"))
    assert cfg.slots is not None
    assert [(s.factor, s.weight, s.params) for s in cfg.signals] == [
        ("volume.vol_ratio_20d", 1.0, {})
    ]
    assert cfg.slots.n == 15 and cfg.slots.entry_threshold == 2.0
    assert [(r.factor, r.min_value, r.params) for r in cfg.slots.require] == [
        ("price.breakout_high_60d", 0.005, {})
    ]
    assert cfg.slots.exclude == [] and cfg.prefilter == []
    assert [(e.kind, e.sessions) for e in cfg.exit_rules] == [("time_stop", 20)]
    # 母體：成交金額中位數 2,000 萬以上（絕對下限，不是前 30%——群組的例子達航會被前 30% 擋掉）
    assert (cfg.universe.min_adv_twd, cfg.universe.min_adv_pctl, cfg.universe.min_price) == (
        20_000_000,
        None,
        10.0,
    )
    quiet = load_strategy(str(CONFIGS / "strategies" / "quiet_s0.yaml"))
    assert cfg.universe.markets == quiet.universe.markets


def test_ac4b_registration_fingerprints_and_no_model() -> None:
    reg = _reg()
    assert reg.registration == "D-215" and reg.opens_on == dt.date(2027, 3, 1)
    assert reg.holdout_start == dt.date(2026, 9, 9)
    (entry,) = reg.entries
    assert (
        entry.strategy == "breakout_v20" and entry.model_id is None and entry.model_sha256 is None
    )
    cfg = load_strategy(str(CONFIGS / "strategies" / f"{entry.strategy}.yaml"))
    assert config_fingerprint(cfg) == entry.strategy_fingerprint, (
        "configs/strategies/breakout_v20.yaml 在登記（D-215）之後被改動了"
    )
    assert entry.costs_fingerprint == config_fingerprint(
        load_config(CONFIGS / "costs.yaml", CostConfig)
    )
    with pytest.raises(PermissionError):
        refuse_before_opening(reg, dt.date(2026, 10, 23), 20)


def test_ac4c_model_fields_must_come_together() -> None:
    from twquant.reporting.holdout import HoldoutEntry

    base = {
        "code": "X",
        "strategy": "breakout_v20",
        "strategy_fingerprint": "0" * 64,
        "costs_fingerprint": "0" * 64,
        "capital_twd": 1.0,
    }
    HoldoutEntry.model_validate(base)
    HoldoutEntry.model_validate({**base, "model_id": "m", "model_sha256": "1" * 64})
    with pytest.raises(ValueError, match="一起給"):
        HoldoutEntry.model_validate({**base, "model_id": "m"})


# ---------- 上架的每一處 ----------


def test_ac5_board_line_is_fourth_and_allows_empty() -> None:
    board = load_config(CONFIGS / "pick_board.yaml", PickBoardConfig)
    line = board.strategies[-1]
    assert (line.code, line.name, line.config, line.allow_empty, line.watch) == (
        "BREAKOUT",
        "帶量突破",
        "breakout_v20",
        True,
        True,
    )
    assert "2 倍" in line.summary and "60 個交易日最高價" in line.summary
    system = yaml.safe_load((CONFIGS / "system.yaml").read_text(encoding="utf-8"))
    assert system["morning_brief"]["line_names"]["BREAKOUT"] == "帶量突破"
    assert "BREAKOUT" in system["pick_blurbs"]["lines"]
    assert "BREAKOUT" not in system["pick_review"]["lines"]  # AI 評分不列（同高分爆量）


def test_ac6_reasons_for_the_rule_line_are_its_two_conditions() -> None:
    from twquant.data.serving.pick_reasons_store import DayPick
    from twquant.reporting.pick_reasons import rule_rows

    cfg = load_config(CONFIGS / "pick_reasons.yaml", PickReasonsConfig)
    assert set(cfg.rule_signals("BREAKOUT")) == {"volume.vol_ratio_20d", "price.breakout_high_60d"}
    assert cfg.rule_signals("CORE") == cfg.core_signals  # 大盤精選不動
    assert set(cfg.rule_factors()) >= set(cfg.core_signals) | set(cfg.rule_signals("BREAKOUT"))
    pick = DayPick("BREAKOUT", 1, "4577", 4.4, None)
    raw = {
        ("4577", "volume.vol_ratio_20d"): (4.4, 0.99),
        ("4577", "price.breakout_high_60d"): (0.037, 0.98),
        ("4577", "price.high_52w"): (0.99, 0.95),  # 大盤精選的因子不該被拿來當這條線的理由
    }
    (row,) = rule_rows([pick], raw, {}, cfg)
    assert row[:3] == ("BREAKOUT", "4577", None)
    assert row[3] == [
        {"f": "volume.vol_ratio_20d", "t": "成交量是前 20 日平均的 4.4 倍，全市場前 1%", "s": 1},
        {
            "f": "price.breakout_high_60d",
            "t": "收盤站上前 60 日最高價（高出 3.7%），全市場前 2%",
            "s": 1,
        },
    ]


def test_ac7_website_lists_the_line() -> None:
    picks_ts = (ROOT / "frontend" / "functions" / "_lib" / "picks.ts").read_text(encoding="utf-8")
    assert "code: 'BREAKOUT'" in picks_ts and "name: '帶量突破'" in picks_ts
    auth_ts = (ROOT / "frontend" / "src" / "cloud" / "auth.ts").read_text(encoding="utf-8")
    assert "code: 'BREAKOUT'" in auth_ts
