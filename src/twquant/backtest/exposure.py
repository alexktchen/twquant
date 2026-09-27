"""每日補位的條件式減碼與反波動部位（D-149）。純運算；只透過 PitView 讀資料。

估計器、觸發與倍數都在 `portfolio.core`（D-004 的單一實作）；這裡只負責把引擎當晚
能看到的資料整理成那幾個函數要的報酬序列。**時點與持股判斷相同**：每日補位在 t 日
收盤後決定、t+1 開盤執行，所以兩條序列都含 t 日收盤。
"""

from __future__ import annotations

import bisect
import datetime as dt
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import polars as pl

from twquant.backtest.metrics import _TRADING_DAYS
from twquant.errors import DataError
from twquant.portfolio.core import ewma_vol_series, vol_context, vol_multiplier, vol_triggered

if TYPE_CHECKING:
    from twquant.data.warehouse import PitView
    from twquant.strategy.config import ConditionalVolConfig, InverseVolSizingConfig


@dataclass(frozen=True)
class ExposureDay:
    """一晚的條件式減碼判斷（D-149）。逐日記下，才算得出觸發日佔比、也才能事後核對。"""

    date: dt.date
    mult: float  # 明天起新進場每格乘的倍數；也是減碼的目標曝險
    triggered: bool
    trigger_vol: float | None  # 觸發序列今天的年化波動（空手時沒有估計）
    trigger_pct: float | None  # 它在自身百分位窗裡的百分位（0~100）
    held_vol: float | None  # 持股等權組合的年化波動（倍數的分母）


@dataclass(frozen=True)
class EntrySize:
    """一筆新進場的格子倍數（D-149 F3）。`sigma is None`＝缺 σ、以下限計（§三 補充）。

    `date` 在算出來時是訊號日；引擎成交後改記成交日，與 ledger 對得上。
    """

    date: dt.date
    stock_id: str
    mult: float
    sigma: float | None


def _window(sessions: list[dt.date], day: dt.date, n_returns: int) -> list[dt.date]:
    """`day`（含）往前 n_returns + 1 個交易日：第一天只當第一個報酬的前收。"""
    i = bisect.bisect_right(sessions, day) - 1
    if i < 0 or sessions[i] != day:
        raise DataError("prices_daily", day, f"{day} 不在交易日曆裡，無法往前數交易日")
    if i < n_returns:
        raise DataError(
            "prices_daily",
            day,
            f"條件式減碼需要 {day} 之前 {n_returns} 個報酬，交易日曆只有 {i} 個；"
            "請把回測起點往後移或補齊更早的行情（不得靜默給 1 倍）",
        )
    return sessions[i - n_returns : i + 1]


def held_basket_returns(
    view: PitView, day: dt.date, held: list[str], sessions: list[dt.date], n_returns: int
) -> np.ndarray:
    """目前持股**等權組合**最近 n_returns 個交易日的日報酬（除權息／分割還原的總報酬）。

    與 `strategy.overlays._realized_vol` 同一個組合定義（看「這一籃股票」本身，忽略權重漂移），
    但改用還原價：分割日 −95% 的價格跳動不是報酬（T-168），而引擎的持股會計本來就把股利入帳、
    分割調股數。收盤價 ≤ 0 的列在本專案的契約裡是「當日無成交」（D-040：2021-01~02 共 33 日），
    與停牌同樣處理——那天沒有報酬，下一個有成交的日子的報酬跨過缺口。
    某個交易日沒有任何一檔持股有報酬 → raise，不跳過。
    """
    # 函式內匯入：`ml.dataset` 在模組層匯入 `backtest.costs`，會觸發 `backtest/__init__` →
    # engine → 本模組，放在模組層就是循環匯入（先 import ml.dataset 的程序——如 `ml train`——會炸）
    from twquant.ml.dataset import attach_adjustment

    days = _window(sessions, day, n_returns)
    px = (
        view.table("prices_daily")
        .select("date", "stock_id", "close")
        .filter(
            pl.col("stock_id").is_in(held)
            & pl.col("date").is_between(days[0], day)
            & (pl.col("close") > 0.0)
        )
        .collect()
    )
    events = (
        view.table("corporate_events")
        .select("date", "stock_id", "adj_factor")
        .filter(pl.col("date") <= day)
        .collect()
    )
    adj_close = pl.col("close") / pl.col("adj")
    rets = (
        attach_adjustment(px, events)
        .sort(["stock_id", "date"])
        .with_columns(r=adj_close / adj_close.shift(1).over("stock_id") - 1.0)
        .drop_nulls("r")
        .group_by("date")
        .agg(pl.col("r").mean())
    )
    got = dict(zip(rets["date"].to_list(), rets["r"].to_list(), strict=True))
    missing = [d for d in days[1:] if d not in got]
    if missing:
        raise DataError(
            "prices_daily",
            day,
            f"持股 {held[:5]} 在 {len(missing)} 個交易日沒有任何一檔有報酬"
            f"（最早 {missing[0]}），等權組合的報酬序列不完整",
        )
    return np.array([got[d] for d in days[1:]], dtype=float)


def index_returns(
    view: PitView, day: dt.date, index_id: str, sessions: list[dt.date], n_returns: int
) -> np.ndarray:
    """指數最近 n_returns 個交易日的日報酬（價格指數，含 `day` 當日收盤）。缺任何一天即 raise。"""
    days = _window(sessions, day, n_returns)
    closes = (
        view.table("index_daily")
        .filter((pl.col("index_id") == index_id) & pl.col("date").is_between(days[0], day))
        .select("date", "close")
        .sort("date")
        .collect()
    )
    if closes["date"].to_list() != days:
        have = set(closes["date"].to_list())
        gap = [d for d in days if d not in have]
        raise DataError(
            "index_daily",
            day,
            f"{index_id} 在 {days[0]}~{day} 缺 {len(gap)} 個交易日（最早 {gap[:3]}）；"
            "觸發序列不完整就不能算百分位",
        )
    c = closes["close"].to_numpy().astype(float)
    if not (c > 0.0).all():
        raise DataError("index_daily", day, f"{index_id} 在 {days[0]}~{day} 有非正收盤")
    return np.asarray(c[1:] / c[:-1] - 1.0, dtype=float)


def conditional_exposure(
    view: PitView,
    day: dt.date,
    held: list[str],
    sessions: list[dt.date],
    cfg: ConditionalVolConfig,
) -> ExposureDay:
    """D-149 F1／F2：今晚的曝險倍數。空手時沒有持股可估 → 1.0（新進場全額，與 T-286 同）。"""
    if not held:
        return ExposureDay(day, 1.0, False, None, None, None)
    vt = cfg.vol_target
    est = vt.estimator
    need = est.window_days + vt.conditional.percentile_window_days - 1
    if cfg.trigger == "holdings":
        rets = held_basket_returns(view, day, held, sessions, need)
        trig = vol_context(rets, vt, _TRADING_DAYS)
        held_vol = trig.realized_vol
    else:
        if cfg.index_id is None:  # 設定驗證已擋；這裡讓型別檢查器知道
            raise DataError("index_daily", day, "trigger=index 卻沒有 index_id")
        trig = vol_context(
            index_returns(view, day, cfg.index_id, sessions, need), vt, _TRADING_DAYS
        )
        # 倍數只要持股**今天**的波動，不需要它的百分位歷史
        held_rets = held_basket_returns(view, day, held, sessions, est.window_days)
        held_vol = float(ewma_vol_series(held_rets, est, _TRADING_DAYS)[-1])
    return ExposureDay(
        date=day,
        mult=vol_multiplier(trig, held_vol, vt),
        triggered=vol_triggered(trig, vt),
        trigger_vol=trig.realized_vol,
        trigger_pct=trig.vol_percentile,
        held_vol=held_vol,
    )


def inverse_vol_sizes(
    view: PitView,
    day: dt.date,
    top_ids: list[str],
    entries: list[str],
    cfg: InverseVolSizingConfig,
) -> dict[str, EntrySize]:
    """D-149 F3：每筆新進場的格子倍數 clip(σ_ref ÷ σ_i, floor, 1.0)。

    σ 取訊號日的 `cfg.factor`；σ_ref＝當晚候選前 n 名（`top_ids`）中**有 σ 者**的中位數。
    缺 σ（新上市恰好第 20 個交易日：有 ADV、還沒有 20 日波動）→ 以下限計（D-149 §三 補充）；
    前 n 名全都缺 σ → raise：連參考水準都沒有，不猜。
    """
    import twquant.factors.builtins  # noqa: F401  # 匯入觸發全因子註冊
    from twquant.factors.compute import compute

    ids = sorted({*top_ids, *entries})
    vals = compute([cfg.factor], view, dates=[day]).filter(pl.col("stock_id").is_in(ids))
    sigma: dict[str, float] = {}
    for r in vals.to_dicts():
        v = r["value"]
        if isinstance(v, float) and np.isfinite(v) and v >= 0.0:
            sigma[str(r["stock_id"])] = v
    ref_pool = [sigma[s] for s in top_ids if s in sigma]
    if not ref_pool:
        raise DataError(
            "prices_daily",
            day,
            f"當晚候選前 {len(top_ids)} 名全都沒有 {cfg.factor}，反波動部位沒有參考水準",
        )
    ref = float(np.median(ref_pool))
    out: dict[str, EntrySize] = {}
    for s in entries:
        sd = sigma.get(s)
        if sd is None:
            mult = cfg.floor
        elif sd == 0.0:
            mult = 1.0  # 零波動：ref ÷ 0 → 無限大，截在上限
        else:
            mult = min(1.0, max(cfg.floor, ref / sd))
        out[s] = EntrySize(date=day, stock_id=s, mult=mult, sigma=sd)
    return out
