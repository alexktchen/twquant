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
import math
from collections.abc import Callable
from typing import TYPE_CHECKING

import polars as pl

from twquant.errors import ConfigError, DataError

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


_TRADING_DAYS_PER_YEAR = 252  # 年化基數；與 backtest/metrics.py 一致，非魔術數字


@overlay("vol_target_scaling")
def vol_target_scaling(
    signal_date: dt.date, weights: pl.DataFrame, view: PitView, params: dict[str, object]
) -> pl.DataFrame:
    """Barroso & Santa-Clara (2015, JFE)「Momentum Has Its Moments」：以已實現波動率縮放曝險。

    動能策略的病不是報酬不夠，是崩起來太深——實測本專案 A 軌 MDD −38.5%、ML −46.1%，
    而 Daniel & Moskowitz (2016, JFE)「Momentum Crashes」指出動能在空頭＋高波動時崩得最慘。
    原文的做法是用**近期已實現波動率**縮放部位，把組合的事後波動率壓到一個常數目標，
    實證上大致讓 Sharpe 翻倍、極端左尾大幅收斂。

    本版的實作與原文的三點差異，都是刻意的：

    1. **不加槓桿**（倍數封頂 1.0）。原文允許 >1，但本專案的回測引擎沒有融資模型，
       放大到 >1 會產生引擎無法成交的目標權重——那是一個靜默的錯數字。
    2. **用等權組合報酬的波動率**，而非個股波動率的加權平均：後者忽略相關性，
       在個股齊漲齊跌時會嚴重低估組合波動。
    3. **歷史不足時 raise 而非放行**。靜默放行會讓 overlay 在資料不足時「成功地什麼都沒做」，
       而那剛好發生在回測最前面那段——曝險最該被管的時候。

    參數：`target_vol`（年化，預設 0.20）、`window`（估計窗，交易日，預設 60）、
    `min_mult`（倍數下限，預設 0.2；避免極端行情下曝險歸零而完全踏空）。
    """
    target = _as_float(params.get("target_vol", 0.20), 0.20)
    window = _as_int(params.get("window", 60), 60)
    min_mult = _as_float(params.get("min_mult", 0.2), 0.2)
    if target <= 0.0:
        raise ConfigError(f"target_vol 須 > 0，得到 {target}")
    if window < 2:
        raise ConfigError(f"window 須 ≥ 2，得到 {window}")

    held = weights["stock_id"].unique().to_list()
    px = (
        view.table("prices_daily")
        .select("date", "stock_id", "close")
        .filter(pl.col("stock_id").is_in(held))
        .collect()
        .sort(["stock_id", "date"])
    )
    if px.height == 0:
        raise DataError(
            "prices_daily",
            signal_date,
            f"{signal_date} 的持股 {held[:5]} 在 PIT 視圖中沒有任何行情，無法估波動率。",
        )
    # 等權組合的日報酬：先算個股報酬，再對同日取平均（自然涵蓋相關性）
    rets = (
        px.with_columns(
            (pl.col("close") / pl.col("close").shift(1).over("stock_id") - 1.0).alias("r")
        )
        .drop_nulls("r")
        .group_by("date")
        .agg(pl.col("r").mean().alias("r"))
        .sort("date")
        .tail(window)
    )
    if rets.height < window:
        raise DataError(
            "prices_daily",
            signal_date,
            f"估波動率需要 {window} 個交易日，只有 {rets.height} 個。"
            "不得靜默回傳原權重——那會讓 overlay 在資料不足時「成功地什麼都沒做」，"
            "而那剛好是回測最前段、曝險最該被管的時候。",
        )
    # polars 的 Series.std 型別是 float | timedelta（同一個方法也服務時間欄），
    # 這裡的欄位是報酬率，故明確收斂到 float 而不是讓型別檢查器猜。
    raw_sd = rets["r"].std(ddof=1)
    sd = float(raw_sd) if isinstance(raw_sd, (int, float)) else None
    if sd is None or sd <= 0.0:
        raise DataError(
            "prices_daily",
            signal_date,
            f"近 {window} 日組合報酬的標準差為 {raw_sd!r}——零波動代表價格未動或資料有誤，"
            "此時縮放倍數無定義。",
        )
    realized = sd * math.sqrt(_TRADING_DAYS_PER_YEAR)
    mult = min(1.0, target / realized)  # 封頂 1.0：不加槓桿
    mult = max(min_mult, mult)
    return _scale(weights, mult)
