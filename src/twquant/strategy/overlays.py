"""Overlay 掛點（SPEC-003 §4）。

Overlay 在 selection 之後、輸出之前依序套用，調整整體曝險（縮放 target_weight）。
註冊函數簽名：(signal_date, weights, view, params) -> weights。

第一版：breadth_regime_filter（市場寬度 = 站上 N 日均線個股比例，可由 prices 計算）。
index_trend_filter 需加權指數序列（尚未接），待補。完整三態 regime 狀態機（含跨期
遲滯狀態）屬投組層（SPEC-005 / T-020）；此處為單日無狀態版：低於下閾值→exposure_floor、
高於上閾值→1.0、之間線性內插。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from twquant.data.warehouse import PitView

OverlayFn = Callable[[dt.date, pl.DataFrame, "PitView", dict[str, object]], pl.DataFrame]

_OVERLAYS: dict[str, OverlayFn] = {}


def overlay(name: str) -> Callable[[OverlayFn], OverlayFn]:
    def deco(fn: OverlayFn) -> OverlayFn:
        _OVERLAYS[name] = fn
        return fn

    return deco


def get_overlay(name: str) -> OverlayFn:
    try:
        return _OVERLAYS[name]
    except KeyError as exc:
        raise ValueError(f"未註冊 overlay：{name}") from exc


def _scale(weights: pl.DataFrame, mult: float) -> pl.DataFrame:
    return weights.with_columns((pl.col("target_weight") * mult).alias("target_weight"))


def _as_float(value: object, default: float) -> float:
    return float(value) if isinstance(value, int | float | str) else default


def _as_int(value: object, default: int) -> int:
    return int(value) if isinstance(value, int | float | str) else default


def market_breadth(view: PitView, signal_date: dt.date, ma_window: int) -> float:
    """站上 ma_window 日均線的（四碼普通股）比例，作為市場寬度。無資料回 1.0（不過濾）。"""
    lf = (
        view.table("prices_daily")
        .select("date", "stock_id", "close")
        .filter(
            (pl.col("date") <= signal_date) & pl.col("stock_id").str.contains(r"^[1-9][0-9]{3}$")
        )
        .sort(["stock_id", "date"])
        .with_columns(_ma=pl.col("close").rolling_mean(ma_window).over("stock_id"))
        .filter(pl.col("date") == signal_date)
        .drop_nulls("_ma")
    )
    df = lf.select((pl.col("close") > pl.col("_ma")).mean().alias("b")).collect()
    if df.height == 0 or df["b"][0] is None:
        return 1.0
    return float(df["b"][0])


@overlay("breadth_regime_filter")
def breadth_regime_filter(
    signal_date: dt.date, weights: pl.DataFrame, view: PitView, params: dict[str, object]
) -> pl.DataFrame:
    """市場寬度低 → 縮減整體曝險（雙閾值；本版單日無狀態、之間線性內插）。"""
    ma_window = _as_int(params.get("ma_window", 60), 60)
    lower = _as_float(params.get("lower", 0.4), 0.4)
    upper = _as_float(params.get("upper", 0.6), 0.6)
    floor = _as_float(params.get("exposure_floor", 0.5), 0.5)

    breadth = market_breadth(view, signal_date, ma_window)
    if breadth <= lower:
        mult = floor
    elif breadth >= upper:
        mult = 1.0
    else:
        mult = floor + (1.0 - floor) * (breadth - lower) / (upper - lower)
    return _scale(weights, mult)
