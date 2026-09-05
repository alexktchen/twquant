"""T-017：策略框架（配置驗證、排程、組合、overlay、目標權重不變量、決定性）。"""

from __future__ import annotations

import datetime as dt
from itertools import pairwise
from pathlib import Path

import polars as pl
import pytest

import twquant.factors.builtins  # noqa: F401  # 註冊因子
from tests.conftest import write_stock_meta
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.calendar import TradingCalendar
from twquant.data.warehouse import Warehouse
from twquant.errors import ConfigError
from twquant.strategy import build, combine
from twquant.strategy.config import (
    RebalanceConfig,
    SignalConfig,
    StrategyConfig,
    UniverseConfig,
    load_strategy,
)
from twquant.strategy.overlays import breadth_regime_filter, market_breadth


# ── 配置驗證（AC-1）────────────────────────────────────────────────
def test_weights_must_sum_to_one() -> None:
    with pytest.raises(ValueError, match="加總"):
        StrategyConfig(
            name="x",
            signals=[
                SignalConfig(factor="price.mom_20d", weight=0.5),
                SignalConfig(factor="chip.trust_streak", weight=0.3),
            ],
        )


def test_unknown_factor_raises_with_hint(tmp_path: Path) -> None:
    yaml = tmp_path / "s.yaml"
    yaml.write_text(
        "name: bad\nsignals:\n  - factor: fundamental.revenue_yay\n    weight: 1.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="revenue_yoy"):  # 最相近的註冊名
        load_strategy(yaml)


def test_sample_yaml_loads() -> None:
    cfg = load_strategy(Path("configs/strategies/sample_revenue_trust.yaml"))
    assert cfg.name == "sample_revenue_trust"
    assert abs(sum(s.weight for s in cfg.signals) - 1.0) < 1e-9
    assert cfg.rebalance.schedule == "weekly"


# ── 排程（AC-3）────────────────────────────────────────────────────
def test_signal_dates_weekly_mon_holiday() -> None:
    # 2026-06-15 是週一。做一個「該週一休市」的日曆 → 應順延到週二 6/16
    sessions = [
        dt.date(2026, 6, 16),  # 週二（週一 6/15 休市）
        dt.date(2026, 6, 17),
        dt.date(2026, 6, 22),  # 下週一
        dt.date(2026, 6, 23),
    ]
    cal = TradingCalendar(sessions)
    rb = RebalanceConfig(schedule="weekly", day="MON")
    got = build.signal_dates(rb, cal, dt.date(2026, 6, 15), dt.date(2026, 6, 26))
    assert got == [dt.date(2026, 6, 16), dt.date(2026, 6, 22)]  # 首週順延、次週正常週一


def test_signal_dates_monthly() -> None:
    sessions = [dt.date(2026, 5, d) for d in (1, 4, 5)] + [dt.date(2026, 6, d) for d in (1, 2)]
    cal = TradingCalendar(sessions)
    rb = RebalanceConfig(schedule="monthly", day_of_month=1)
    got = build.signal_dates(rb, cal, dt.date(2026, 5, 1), dt.date(2026, 6, 30))
    assert got == [dt.date(2026, 5, 1), dt.date(2026, 6, 1)]


# ── 雙週排程（T-089 / D-046 候選 2、4）──────────────────────────────
# 定義：ISO 偶數週的週訊號日。用「偶數週」而非「從 start 起每隔一週」，是為了讓不同
# 起日的回測落在同一套再平衡日曆上——否則兩次回測的差異會混進日曆相位。
def _weekday_sessions(start: dt.date, end: dt.date) -> list[dt.date]:
    d, out = start, []
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def test_ac1_biweekly_is_every_other_weekly_signal_date() -> None:
    """AC-1：biweekly 是 weekly 訊號日的嚴格子集，且相鄰兩日恰隔兩週。"""
    cal = TradingCalendar(_weekday_sessions(dt.date(2025, 3, 3), dt.date(2025, 5, 30)))
    weekly = build.signal_dates(
        RebalanceConfig(schedule="weekly", day="MON"),
        cal,
        dt.date(2025, 3, 3),
        dt.date(2025, 5, 30),
    )
    bi = build.signal_dates(
        RebalanceConfig(schedule="biweekly", day="MON"),
        cal,
        dt.date(2025, 3, 3),
        dt.date(2025, 5, 30),
    )
    assert set(bi) < set(weekly), "biweekly 必須是 weekly 的嚴格子集"
    assert all((b - a).days == 14 for a, b in pairwise(bi)), "相鄰訊號日須恰隔 14 天"
    # 2025-03-03 是 ISO W10；偶數週 W10/12/14/16/18/20/22 共 7 個，週訊號日 13 個
    assert len(bi) == 7 and len(weekly) == 13


def test_ac2_biweekly_iso_week_parity_is_continuous_across_new_year() -> None:
    """AC-2：跨年不重不漏——2024-12-30（週一）是 ISO 2025-W01（奇數），W52 與 W02 才是偶數週。"""
    cal = TradingCalendar(_weekday_sessions(dt.date(2024, 12, 16), dt.date(2025, 1, 17)))
    got = build.signal_dates(
        RebalanceConfig(schedule="biweekly", day="MON"),
        cal,
        dt.date(2024, 12, 16),
        dt.date(2025, 1, 17),
    )
    assert got == [dt.date(2024, 12, 23), dt.date(2025, 1, 6)], (
        "應取 W52 與 W02，跳過 W51／2025-W01／W03"
    )


def test_ac2_biweekly_monday_holiday_rolls_forward_like_weekly() -> None:
    """偶數週的週一休市 → 順延到該週第一個開市日（與 weekly 同規則）。"""
    sessions = _weekday_sessions(dt.date(2025, 3, 3), dt.date(2025, 3, 28))
    sessions.remove(dt.date(2025, 3, 10))  # 2025-W11 週一休市（W11 為奇數；W10、W12 偶數）
    sessions.remove(dt.date(2025, 3, 17))  # 2025-W12 週一休市 → 應順延到 3/18
    cal = TradingCalendar(sessions)
    got = build.signal_dates(
        RebalanceConfig(schedule="biweekly", day="MON"),
        cal,
        dt.date(2025, 3, 3),
        dt.date(2025, 3, 28),
    )
    assert got == [dt.date(2025, 3, 3), dt.date(2025, 3, 18)]


def test_ac3_existing_schedules_unchanged_by_biweekly() -> None:
    """AC-3：加入 biweekly 不得改變 daily / weekly / monthly 的輸出。"""
    sessions = _weekday_sessions(dt.date(2025, 3, 3), dt.date(2025, 4, 30))
    cal = TradingCalendar(sessions)
    lo, hi = dt.date(2025, 3, 3), dt.date(2025, 4, 30)
    assert build.signal_dates(RebalanceConfig(schedule="daily"), cal, lo, hi) == sessions
    assert build.signal_dates(RebalanceConfig(schedule="weekly", day="MON"), cal, lo, hi) == [
        d for d in sessions if d.weekday() == 0
    ]
    assert build.signal_dates(RebalanceConfig(schedule="monthly", day_of_month=1), cal, lo, hi) == [
        dt.date(2025, 3, 3),
        dt.date(2025, 4, 1),
    ]


# ── 組合 ───────────────────────────────────────────────────────────
def test_zscore_weighted_orientation() -> None:
    # 兩因子、一天、三檔：a 兩因子都最高 → 分數最高
    df = pl.DataFrame(
        {
            "date": [dt.date(2026, 6, 30)] * 6,
            "stock_id": ["a", "b", "c"] * 2,
            "factor": ["f1"] * 3 + ["f2"] * 3,
            "value": [3.0, 2.0, 1.0, 30.0, 20.0, 10.0],
        }
    )
    score = combine.combined_score(df, {"f1": 0.5, "f2": 0.5}, "zscore_weighted")
    top = score.sort("score", descending=True)["stock_id"].to_list()
    assert top[0] == "a" and top[-1] == "c"


# ── 目標權重端到端（AC-5、AC-7）+ overlay（AC-6）+ 宇宙 ─────────────
def _synthetic_wh(tmp_path: Path, *, amounts: dict[str, float] | None = None) -> Warehouse:
    """25 個連續日、三檔不同動能；amount 可個別設定（測 ADV 過濾）。"""
    base = dt.date(2026, 6, 1)
    slopes = {"1101": 1.0, "2330": 2.0, "2454": 0.5}  # 動能 2330 > 1101 > 2454
    amt = amounts or dict.fromkeys(slopes, 1.0e8)
    rows = []
    for i in range(25):
        d = base + dt.timedelta(days=i)
        for sid, slope in slopes.items():
            c = 100.0 + slope * i
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": c,
                    "high": c,
                    "low": c,
                    "close": c,
                    "volume": 1000,
                    "amount": amt[sid],
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


def _mom_cfg(**over: object) -> StrategyConfig:
    base: dict[str, object] = {
        "name": "t",
        "signals": [SignalConfig(factor="price.mom_20d", weight=1.0)],
        "selection": {"method": "top_n", "top_n": 2},
        "weighting": "equal",
        "max_weight_per_stock": 0.6,
        "universe": {"min_adv_twd": 30_000_000, "min_price": None},
        "overlays": [],
    }
    base.update(over)
    return StrategyConfig.model_validate(base)


def test_build_targets_invariants_and_determinism(tmp_path: Path) -> None:
    wh = _synthetic_wh(tmp_path)
    cfg = _mom_cfg()
    d = dt.date(2026, 6, 25)
    out = build.build_targets(cfg, wh, d)
    assert out.columns == ["date", "stock_id", "target_weight"]
    assert set(out["stock_id"].to_list()) == {"1101", "2330"}  # 動能最高兩檔（排除 2454）
    w = out["target_weight"].to_list()
    assert sum(w) <= 1.0 + 1e-9
    assert max(w) <= 0.6 + 1e-9
    assert min(w) >= 0.0
    assert out.equals(build.build_targets(cfg, wh, d))  # AC-7 決定性


def test_universe_adv_filter_excludes_illiquid(tmp_path: Path) -> None:
    # 2454 成交值遠低於門檻 → 被宇宙淘汰，即使動能不差
    wh = _synthetic_wh(tmp_path, amounts={"1101": 1e8, "2330": 1e8, "2454": 1e6})
    cfg = _mom_cfg(selection={"method": "top_n", "top_n": 5})
    out = build.build_targets(cfg, wh, dt.date(2026, 6, 25))
    assert "2454" not in out["stock_id"].to_list()


def test_breadth_overlay_bear_floor(tmp_path: Path) -> None:
    # 全部下跌 → 站上均線比例 0 → 曝險應被壓到 exposure_floor
    base = dt.date(2026, 6, 1)
    rows = []
    for i in range(25):
        d = base + dt.timedelta(days=i)
        for sid in ("1101", "2330"):
            c = 200.0 - i  # 一路下跌
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": c,
                    "high": c,
                    "low": c,
                    "close": c,
                    "volume": 1000,
                    "amount": 1e8,
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
    d = dt.date(2026, 6, 25)
    view = wh.view(d)
    assert market_breadth(view, d, 10) == pytest.approx(0.0)
    weights = pl.DataFrame({"date": [d], "stock_id": ["1101"], "target_weight": [0.5]})
    out = breadth_regime_filter(d, weights, view, {"ma_window": 10, "exposure_floor": 0.5})
    assert out["target_weight"][0] == pytest.approx(0.25)  # 0.5 原曝險 × floor 0.5


# ── D-037：衛生濾網缺口判準改用交易日（T-071）──────────────────────────
def _holiday_wh(
    tmp_path: Path, missing: dict[str, set[dt.date]] | None = None
) -> tuple[Warehouse, list[dt.date]]:
    """農曆年封關情境：1/06~1/22 開市、1/23~2/02 全市場休市、2/03~2/14 復市。

    `missing` 指定個股缺席的開市日（模擬個股停牌），未指定者全數present。
    """
    sessions = [dt.date(2025, 1, d) for d in (6, 7, 8, 9, 10, 13, 14, 15, 16, 17, 20, 21, 22)]
    sessions += [dt.date(2025, 2, d) for d in (3, 4, 5, 6, 7, 10, 11, 12, 13, 14)]
    skip = missing or {}
    rows = []
    for i, d in enumerate(sessions):
        for sid, slope in {"1101": 1.0, "2330": 2.0, "2454": 0.5}.items():
            if d in skip.get(sid, set()):
                continue
            c = 100.0 + slope * i
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": c,
                    "high": c,
                    "low": c,
                    "close": c,
                    "volume": 1000,
                    "amount": 1e8,
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
    return wh, sessions


def test_ac1_market_wide_holiday_is_not_a_gap(tmp_path: Path) -> None:
    # D-037：全市場休市（1/22 → 2/03 隔 12 曆日、0 個漏掉的開市日）不得判為資料缺口
    from twquant.strategy.universe import universe_ids

    wh, _ = _holiday_wh(tmp_path)
    d = dt.date(2025, 2, 14)
    ids = universe_ids(wh.view(d), d, UniverseConfig(min_adv_twd=None, min_price=None))
    assert ids == ["1101", "2330", "2454"]


def test_ac2_single_stock_suspension_still_excluded(tmp_path: Path) -> None:
    # D-037 修正不得放寬個股偵測力：2454 停牌 7 個開市日（> max_gap_sessions）→ 排除
    from twquant.strategy.universe import universe_ids

    halted = {dt.date(2025, 2, d) for d in (4, 5, 6, 7, 10, 11, 12)}
    wh, _ = _holiday_wh(tmp_path, missing={"2454": halted})
    d = dt.date(2025, 2, 14)
    ids = universe_ids(wh.view(d), d, UniverseConfig(min_adv_twd=None, min_price=None))
    assert "2454" not in ids
    assert ids == ["1101", "2330"]


def test_ac3_empty_universe_raises_instead_of_silent_zero(tmp_path: Path) -> None:
    # D-037/黃金規則 5：宇宙全空必須 raise（含訊號日上下文），不得靜默回空表
    from twquant.errors import DataError

    wh, _ = _holiday_wh(tmp_path)
    cfg = _mom_cfg(universe={"min_adv_twd": 1e12, "min_price": None})
    with pytest.raises(DataError, match="2025-02-14"):
        build.build_targets(cfg, wh, dt.date(2025, 2, 14))


# ── T-123／D-061：加權契約的整合驗收（單元測試在 test_weighting.py）──────
#
# 這裡驗的是 `build_targets` 的**順序**契約，那是單元測試看不到的：
# 截頂與重分配必須在 `_weight` 內完成，overlay 之後不得再歸一。


def _falling_wh(tmp_path: Path) -> Warehouse:
    """全市場一路下跌 → market_breadth = 0 → breadth overlay 會把曝險壓到 floor。

    80 天不是隨意取的：`build_targets` 呼叫 overlay 時傳空參數，故用預設 `ma_window=60`；
    資料不足 60 天時算不出均線，breadth 不會是 0，overlay 乘數變成 1.0 而測不到東西。
    """
    base = dt.date(2026, 6, 1)
    rows = []
    for i in range(80):
        d = base + dt.timedelta(days=i)
        for sid, slope in {"1101": 1.0, "2330": 2.0, "2454": 0.5}.items():
            c = 200.0 - slope * i
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": c,
                    "high": c,
                    "low": c,
                    "close": c,
                    "volume": 1000,
                    "amount": 1e8,
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


def test_ac7_overlay_exposure_is_not_renormalized_back_to_one(tmp_path: Path) -> None:
    """D-061／R4：截頂重分配在 overlay **之前**，故 overlay 壓低的曝險必須留住。

    先前的順序是 `_weight` → overlay → 截頂；若在最後把總權重歸一到 1，
    breadth overlay 刻意降到 50% 的曝險會被悄悄補回去，策略行為被改變而無人察覺。
    """
    wh = _falling_wh(tmp_path)
    d = dt.date(2026, 8, 15)
    assert market_breadth(wh.view(d), d, 60) == pytest.approx(0.0), "fixture 應為全空頭"
    cfg = _mom_cfg(
        selection={"method": "top_n", "top_n": 2},
        overlays=["breadth_regime_filter"],
        max_weight_per_stock=0.6,
    )
    out = build.build_targets(cfg, wh, d)
    total = out["target_weight"].sum()
    assert total == pytest.approx(0.5, abs=1e-9), (
        f"breadth floor 0.5 應讓總曝險維持 0.5，得到 {total}——歸一被錯誤地放在 overlay 之後"
    )


def test_ac7_without_overlay_total_exposure_is_one(tmp_path: Path) -> None:
    """反向確認：沒有 overlay 時總曝險仍是 1（證明上一條測到的是 overlay 而非 bug）。"""
    wh = _falling_wh(tmp_path)
    cfg = _mom_cfg(selection={"method": "top_n", "top_n": 2}, max_weight_per_stock=0.6)
    out = build.build_targets(cfg, wh, dt.date(2026, 8, 15))
    assert out["target_weight"].sum() == pytest.approx(1.0, abs=1e-9)


def test_ac14_infeasible_cap_raises_on_actual_picked_count(tmp_path: Path) -> None:
    """設定 top_n=40 但宇宙只有 3 檔；判準須用**實際入選檔數**。

    以 `top_n × cap` 判斷會算出 40 × 0.2 = 8 ≥ 1 而放行，然後產出總曝險 0.6 的組合。
    """
    wh = _falling_wh(tmp_path)
    cfg = _mom_cfg(selection={"method": "top_n", "top_n": 40}, max_weight_per_stock=0.2)
    with pytest.raises(ConfigError, match="實際入選"):
        build.build_targets(cfg, wh, dt.date(2026, 8, 15))


def test_r12_require_all_factors_drops_stocks_missing_a_factor(tmp_path: Path) -> None:
    """`rank_avg` 對存在的列求和 → 缺負向因子的股票拿不到負分、分數虛高。

    2454 只有 20 日行情不足以算 60 日動能（缺 `price.mom_60d`）。關閉時它仍會被
    計分（只用另一個因子），開啟後必須被剔除。
    """
    base = dt.date(2026, 6, 1)
    rows = []
    for i in range(80):
        d = base + dt.timedelta(days=i)
        for sid in ("1101", "2330", "2454"):
            if sid == "2454" and i < 45:  # 2454 有 35 天：夠算 mom_20d，不夠算 mom_60d
                continue
            c = 100.0 + i
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": c,
                    "high": c,
                    "low": c,
                    "close": c,
                    "volume": 1000,
                    "amount": 1e8,
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
    d = base + dt.timedelta(days=79)
    two = {
        "signals": [
            SignalConfig(factor="price.mom_20d", weight=0.5),
            SignalConfig(factor="price.mom_60d", weight=0.5),
        ],
        "combine": "rank_avg",
        "selection": {"method": "top_n", "top_n": 5},
        "max_weight_per_stock": 0.5,
        "universe": {"min_adv_twd": None, "min_price": None, "max_gap_sessions": None},
    }
    off = build.build_targets(StrategyConfig.model_validate({"name": "t", **two}), wh, d)
    on = build.build_targets(
        StrategyConfig.model_validate({"name": "t", "require_all_factors": True, **two}), wh, d
    )
    assert "2454" in off["stock_id"].to_list(), "關閉時應仍被計分（這正是問題所在）"
    assert "2454" not in on["stock_id"].to_list(), "開啟時因子不齊者須被剔除"
    assert on["target_weight"].sum() == pytest.approx(1.0, abs=1e-9)
