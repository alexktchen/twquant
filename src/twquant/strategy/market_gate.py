"""大盤緩跌閘門（D-184）：某天的市場狀態。純運算；只透過 PitView 讀資料。"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

import polars as pl

from twquant.errors import DataError
from twquant.ml.regime import GRIND, market_regime

if TYPE_CHECKING:
    from twquant.data.warehouse import PitView
    from twquant.strategy.config import MarketGateConfig


def gate_regime(view: PitView, day: dt.date, cfg: MarketGateConfig) -> str:
    """`day` 收盤後的市場狀態（calm／grind／panic）。只讀 `day`（含）以前的指數收盤。

    只取算得出最後一列所需的長度：均線要 `ma_window` 列；波動分位要 `vol_window` 個報酬
    （`vol_window`＋1 列）滾 `vol_lookback` 次——滾動只往回看，所以與全歷史算出的最後一列相同
    （tests/test_market_gate.py 守著）。缺當天收盤或暖機不足 → raise：不知道就是不知道，
    當成一般日放行等於在最沒把握的時候開新倉。
    """
    need = max(cfg.ma_window, cfg.vol_window + cfg.vol_lookback)
    hist = (
        view.table("index_daily")
        .filter((pl.col("index_id") == cfg.index_id) & (pl.col("date") <= day))
        .select("date", "close")
        .sort("date")
        .tail(need)
        .collect()
    )
    if hist.height == 0 or hist["date"][-1] != day:
        raise DataError("index_daily", day, f"{cfg.index_id} 沒有 {day} 的收盤，無法判斷市場狀態")
    regime = market_regime(
        hist,
        ma_window=cfg.ma_window,
        vol_window=cfg.vol_window,
        vol_lookback=cfg.vol_lookback,
        vol_pctl=cfg.vol_pctl,
    )["regime"][-1]
    if regime is None:
        raise DataError(
            "index_daily",
            day,
            f"{cfg.index_id} 到 {day} 只有 {hist.height} 天收盤，市場狀態要 {need} 天才算得出來",
        )
    return str(regime)


def blocks_new_positions(view: PitView, day: dt.date, cfg: MarketGateConfig) -> bool:
    """`day` 收盤後是否不得開新倉（緩跌）。"""
    return gate_regime(view, day, cfg) == GRIND
