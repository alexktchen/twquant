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


# ── C4：Barroso–Santa-Clara 波動率縮放 overlay ────────────────────────────


def _vol_wh(tmp_path: Path, daily_ret: float, n: int = 80) -> tuple[Warehouse, dt.date]:
    """建一個「每日固定報酬 ±daily_ret 交替」的資料湖，已實現波動率因此可手算。

    交替漲跌讓價格幾乎不漂移，故縮放倍數只由波動率決定，不被趨勢干擾。
    """
    days = [dt.date(2026, 3, 2) + dt.timedelta(days=i) for i in range(n * 2)]
    days = [d for d in days if d.weekday() < 5][:n]
    rows: list[dict[str, object]] = []
    for sid in ("1101", "2330"):
        c = 100.0
        for i, d in enumerate(days):
            c *= 1.0 + (daily_ret if i % 2 == 0 else -daily_ret)
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
    return wh, days[-1]


def _w(d: dt.date) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [d, d],
            "stock_id": ["1101", "2330"],
            "target_weight": [0.5, 0.5],
        }
    )


def test_ac1_c4_high_volatility_scales_exposure_down() -> None:
    """已實現波動率高於目標 → 縮減曝險（Barroso–Santa-Clara 2015）。

    這是 C4 存在的理由：動能策略的病不是報酬不夠，是崩起來太深
    （實測 A 軌 MDD −38.5%、ML −46.1%）。
    """
    import tempfile

    from twquant.strategy.overlays import vol_target_scaling

    with tempfile.TemporaryDirectory() as td:
        wh, d = _vol_wh(Path(td), daily_ret=0.04)  # 年化約 63%
        out = vol_target_scaling(d, _w(d), wh.view(d), {"target_vol": 0.20, "window": 20})
    assert out["target_weight"].sum() < 1.0 - 1e-9, "高波動時必須減碼"


def test_ac2_c4_low_volatility_is_capped_at_one() -> None:
    """低波動時**不得加槓桿**：倍數上限 1.0。

    Barroso–Santa-Clara 原文允許槓桿，但本專案的回測引擎沒有融資模型，
    放大到 >1 會產生一個引擎無法成交的目標權重——那是靜默的錯數字。
    """
    import tempfile

    from twquant.strategy.overlays import vol_target_scaling

    with tempfile.TemporaryDirectory() as td:
        wh, d = _vol_wh(Path(td), daily_ret=0.001)  # 年化約 1.6%，遠低於目標
        out = vol_target_scaling(d, _w(d), wh.view(d), {"target_vol": 0.20, "window": 20})
    assert out["target_weight"].sum() == pytest.approx(1.0), "低波動時倍數封頂於 1.0，不得加槓桿"


def test_ac3_c4_insufficient_history_does_not_silently_pass_through() -> None:
    """歷史不足以估波動率時 raise，不得靜默回傳原權重。

    靜默放行會讓 overlay 在資料不足時「成功地什麼都沒做」——正是黃金規則 5 要防的
    失敗模式，而且它會發生在回測**最前面那段**，剛好是曝險最該被管的時候。
    """
    import tempfile

    from twquant.errors import DataError
    from twquant.strategy.overlays import vol_target_scaling

    with tempfile.TemporaryDirectory() as td:
        wh, d = _vol_wh(Path(td), daily_ret=0.02, n=5)  # 只有 5 天，不足 20 日窗
        with pytest.raises(DataError, match="波動率"):
            vol_target_scaling(d, _w(d), wh.view(d), {"target_vol": 0.20, "window": 20})


def test_ac4_c4_scaling_is_proportional_and_preserves_relative_weights() -> None:
    """縮放只改整體曝險，**不得改變個股之間的相對比例**——那是選股層的事。"""
    import tempfile

    from twquant.strategy.overlays import vol_target_scaling

    with tempfile.TemporaryDirectory() as td:
        wh, d = _vol_wh(Path(td), daily_ret=0.04)
        w = pl.DataFrame(
            {"date": [d, d], "stock_id": ["1101", "2330"], "target_weight": [0.7, 0.3]}
        )
        out = vol_target_scaling(d, w, wh.view(d), {"target_vol": 0.20, "window": 20})
    a, b = out["target_weight"].to_list()
    assert a / b == pytest.approx(0.7 / 0.3), "相對比例必須不變"


def test_ac5_c4_overlay_params_reach_the_overlay() -> None:
    """`overlay_params` 必須真的傳到 overlay——否則它是宣告了卻不生效的 no-op。

    `build.py` 原本寫死傳 `{}`，而 `StrategyConfig` 沒有這個欄位；那樣的話
    配置檔寫 `target_vol: 0.05` 也不會有任何效果，且**不會有任何錯誤訊息**。
    """
    from twquant.strategy.overlays import get_overlay, overlay

    seen: dict[str, object] = {}

    @overlay("_probe_params")
    def _probe(  # type: ignore[unused-ignore]
        signal_date: dt.date, weights: pl.DataFrame, view: object, params: dict[str, object]
    ) -> pl.DataFrame:
        seen.update(params)
        return weights

    assert get_overlay("_probe_params") is _probe

    from twquant.strategy.config import StrategyConfig

    cfg = StrategyConfig.model_validate(
        {
            "name": "t",
            "signals": [{"factor": "price.mom_20d", "weight": 1.0}],
            "selection": {"method": "top_n", "top_n": 2},
            "weighting": "equal",
            "rebalance": {"schedule": "monthly", "day": "MON", "execution": "next_open"},
            "overlays": ["_probe_params"],
            "overlay_params": {"_probe_params": {"target_vol": 0.05}},
        }
    )
    assert cfg.overlay_params["_probe_params"]["target_vol"] == 0.05


# ── 串接閘門：先用一個因子縮小池子，再用另一組因子在池內排序 ────────────────


def _cascade_wh(tmp_path: Path) -> tuple[Warehouse, dt.date, list[str]]:
    """10 檔股票、價格單調遞增且各不相同——動能排序因此可手算。"""
    days = [dt.date(2026, 3, 2) + dt.timedelta(days=i) for i in range(40)]
    days = [d for d in days if d.weekday() < 5][:30]
    sids = [f"{1100 + i}" for i in range(10)]
    rows: list[dict[str, object]] = []
    for k, sid in enumerate(sids):
        c = 100.0
        for d in days:
            c *= 1.0 + 0.001 * (k + 1)  # k 越大漲越快 → 動能排序 = sids 反序
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": c,
                    "high": c,
                    "low": c,
                    "close": c,
                    "volume": 100000,
                    "amount": 1e9,
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
    return wh, days[-1], sids


def test_ac1_prefilter_narrows_the_pool_before_ranking(tmp_path: Path) -> None:
    """`prefilter` 以某因子的橫斷面分位縮小宇宙，**在訊號因子計算之前**。

    這是「先用 ML 挑、再用技術面篩」的機制：ML 的長處是排序整個橫斷面
    （OOS RankIC +0.045、16 窗有 15 個為正），短處是挑不出最強的 20 檔
    （零成本下對四因子只贏 +1.24pp、t +0.33）。串接消費的是排序而非尾巴。
    """
    from twquant.strategy.build import build_targets
    from twquant.strategy.config import StrategyConfig

    wh, d, _sids = _cascade_wh(tmp_path)
    base = {
        "name": "t",
        "universe": {"min_adv_twd": None, "max_jump_ratio": None, "max_gap_sessions": None},
        "signals": [{"factor": "price.mom_20d", "weight": 1.0}],
        "selection": {"method": "top_n", "top_n": 3},
        "weighting": "equal",
        "rebalance": {"schedule": "monthly", "day": "MON", "execution": "next_open"},
        "max_weight_per_stock": 0.5,
    }
    # 不設閘門：10 檔全入池，選前 3 名（漲最快的 1109/1108/1107）
    full = build_targets(StrategyConfig.model_validate(base), wh, d)
    assert set(full["stock_id"].to_list()) == {"1109", "1108", "1107"}

    # 閘門留動能後 50%（漲最慢的一半）→ 只能在 1100~1104 裡挑
    gated = StrategyConfig.model_validate(
        {**base, "prefilter": [{"factor": "price.mom_20d", "keep_pct": 0.5, "side": "bottom"}]}
    )
    out = build_targets(gated, wh, d)
    assert set(out["stock_id"].to_list()) <= {"1100", "1101", "1102", "1103", "1104"}
    assert out.height == 3


def test_ac2_prefilter_keeps_the_top_fraction_by_default(tmp_path: Path) -> None:
    """預設 `side: top`：留分位最高的一段（照因子的 direction 校正方向）。"""
    from twquant.strategy.build import build_targets
    from twquant.strategy.config import StrategyConfig

    wh, d, _ = _cascade_wh(tmp_path)
    cfg = StrategyConfig.model_validate(
        {
            "name": "t",
            "universe": {"min_adv_twd": None, "max_jump_ratio": None, "max_gap_sessions": None},
            "prefilter": [{"factor": "price.mom_20d", "keep_pct": 0.3}],
            "signals": [{"factor": "price.mom_20d", "weight": 1.0}],
            "selection": {"method": "top_n", "top_n": 2},
            "weighting": "equal",
            "rebalance": {"schedule": "monthly", "day": "MON", "execution": "next_open"},
            "max_weight_per_stock": 0.5,
        }
    )
    out = build_targets(cfg, wh, d)
    assert set(out["stock_id"].to_list()) <= {"1109", "1108", "1107"}


def test_ac3_prefilter_excludes_null_values(tmp_path: Path) -> None:
    """閘門因子為 null 者一律排除——「不知道分數」不等於「分數高」。

    與 min_roe／市值排名同一條規則（黃金規則 5 的精神）。
    """
    from twquant.strategy.build import _prefilter_ids

    frame = pl.DataFrame(
        {"stock_id": ["a", "b", "c", "d"], "value": [3.0, 1.0, None, 2.0]},
        schema={"stock_id": pl.String, "value": pl.Float64},
    )
    kept = _prefilter_ids(frame, keep_pct=0.75, side="top", direction=1)
    assert "c" not in kept, "null 必須被排除，不得當成高分"


def test_ac4_prefilter_rejects_an_empty_result(tmp_path: Path) -> None:
    """閘門把池子清空 → raise，不得靜默變成空手日。

    宇宙全空是配置問題（比照 build_targets 對 universe_ids 的處理）；
    靜默回空表會讓回測輸出 0 成交卻 exit 0，還把無效試驗寫進 research_log。
    """
    from twquant.errors import ConfigError
    from twquant.strategy.build import _prefilter_ids

    with pytest.raises(ConfigError, match="keep_pct"):
        _prefilter_ids(
            pl.DataFrame({"stock_id": ["a"], "value": [1.0]}), keep_pct=0.0, side="top", direction=1
        )
