"""策略執行：再平衡排程 + 目標權重產生（SPEC-003 §1/§5）。

build_targets 對單一 signal_date 產出 (date, stock_id, target_weight)：
宇宙過濾 → 因子計算（限宇宙）→ 組合 → 選股 → 權重 → overlay → 單股上限。
signal_dates 用交易日曆（T-003）把 weekly/monthly 排程對到真實開市日（AC-3）。
純運算：無網路、無寫檔。
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

import polars as pl

from twquant.strategy.combine import combined_score
from twquant.strategy.overlays import get_overlay
from twquant.strategy.universe import universe_ids

if TYPE_CHECKING:
    from twquant.data.calendar import TradingCalendar
    from twquant.data.warehouse import Warehouse
    from twquant.strategy.config import RebalanceConfig, StrategyConfig

_WEEKDAY = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4}

_OUT_SCHEMA = {"date": pl.Date, "stock_id": pl.String, "target_weight": pl.Float64}


def signal_dates(
    rb: RebalanceConfig, calendar: TradingCalendar, start: dt.date, end: dt.date
) -> list[dt.date]:
    """依排程回傳 [start, end] 內的訊號日（皆為開市日）。"""
    sessions = calendar.between(start, end)
    if rb.schedule == "daily":
        return sessions
    if rb.schedule == "weekly":
        target = _WEEKDAY[rb.day]
        picked: dict[tuple[int, int], dt.date] = {}
        for d in sessions:
            key = d.isocalendar()[:2]  # (ISO 年, ISO 週)
            if d.weekday() >= target and key not in picked:
                picked[key] = d  # 該週第一個「星期>=目標」的開市日（週一休市則順延）
        return sorted(picked.values())
    if rb.schedule == "monthly":
        dom = rb.day_of_month or 1
        picked_m: dict[tuple[int, int], dt.date] = {}
        for d in sessions:
            key = (d.year, d.month)
            if d.day >= dom and key not in picked_m:
                picked_m[key] = d
        return sorted(picked_m.values())
    raise ValueError(f"未知排程：{rb.schedule}")


def _empty() -> pl.DataFrame:
    return pl.DataFrame(schema=_OUT_SCHEMA)


def build_targets(cfg: StrategyConfig, wh: Warehouse, signal_date: dt.date) -> pl.DataFrame:
    """對 signal_date 產出目標權重 (date, stock_id, target_weight)。空手日回空表。"""
    import twquant.factors.builtins  # noqa: F401  # 匯入觸發全因子註冊
    from twquant.factors.compute import compute
    from twquant.factors.registry import get

    view = wh.view(signal_date)
    uni = universe_ids(view, signal_date, cfg.universe)
    if not uni:
        return _empty()

    names = [s.factor for s in cfg.signals]
    fdf = compute(names, view, dates=[signal_date]).filter(pl.col("stock_id").is_in(uni))
    if fdf.is_empty():
        return _empty()

    # 方向併入權重：direction=-1 的因子以負權重併入，使高分一致代表看多
    signed = {s.factor: s.weight * get(s.factor).direction for s in cfg.signals}
    score = combined_score(fdf, signed, cfg.combine).drop_nulls("score")
    if score.is_empty():
        return _empty()

    picked = _select(score, cfg)
    if picked.is_empty():
        return _empty()

    weights = _weight(picked, cfg)
    for name in cfg.overlays:
        weights = get_overlay(name)(signal_date, weights, view, {})

    weights = weights.with_columns(
        pl.min_horizontal(pl.col("target_weight"), pl.lit(cfg.max_weight_per_stock)).alias(
            "target_weight"
        )
    )
    return weights.select("date", "stock_id", "target_weight").sort("stock_id")


def _select(score: pl.DataFrame, cfg: StrategyConfig) -> pl.DataFrame:
    sel = cfg.selection
    if sel.method == "top_n":
        n = sel.top_n or 40
        # 次序鍵 stock_id：分數同分時決定性挑選（避免跨程序 group_by 順序影響選股）
        return score.sort(["score", "stock_id"], descending=[True, False]).head(n)
    if sel.method == "threshold":
        thr = sel.threshold if sel.threshold is not None else 0.0
        return score.filter(pl.col("score") >= thr)
    raise ValueError(f"未知選股方法：{sel.method}")


def _weight(picked: pl.DataFrame, cfg: StrategyConfig) -> pl.DataFrame:
    if cfg.weighting == "equal":
        w = 1.0 / picked.height
        return picked.with_columns(pl.lit(w).alias("target_weight"))
    raise NotImplementedError(
        f"weighting={cfg.weighting} 待接（score_prop/inv_vol，見 TASKS T-017 備註）"
    )
