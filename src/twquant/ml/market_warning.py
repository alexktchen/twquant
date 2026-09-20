"""大跌預警（T-254／D-122 §四）：walk-forward 邏輯斯迴歸，5 個事先指定的大盤特徵。

**硬限制寫在最前面**：12 年裡 TAIEX「未來 20 日最大跌幅 ≤ −10%」的獨立事件只有 9 次。
這個樣本量不足以在統計上證明任何預警模型有效——診斷最多只能說「沒有被推翻」。

純函數：輸入指數收盤與個股收盤，輸出逐日機率與預警旗標。診斷腳本與回測引擎呼叫同一支，
兩邊不可能算出不同的預警。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import polars as pl

from twquant.ml.logistic import fit_logistic, predict_logistic

FEATURES: tuple[str, ...] = (
    "idx_ma_dev",  # TAIEX 對長均線的乖離
    "idx_runup",  # TAIEX 長視窗漲幅（大跌多半跟在大漲後面）
    "idx_vol_chg",  # 短視窗波動度的變化
    "breadth",  # 全市場站上短均線的比例
    "breadth_chg",  # 該比例的變化（指數在高檔而廣度轉弱＝背離）
)


@dataclass(frozen=True)
class WarningConfig:
    """D-122 §四 登記的數字；全部由呼叫端傳入（黃金規則 8）。"""

    horizon: int  # 標籤：未來幾個交易日
    drop: float  # 標籤：最大跌幅門檻（正數，0.10＝−10%）
    long_window: int  # 長均線／漲幅視窗
    short_window: int  # 波動度與廣度的視窗
    change_window: int  # 「變化」看幾天
    min_events: int  # 起訓前至少要有幾個獨立事件
    refit_sessions: int  # 每隔幾個交易日重訓一次
    alarm_pctl: float  # 預警門檻＝訓練期預測值的第幾百分位
    l2: float
    max_iter: int


def warning_features(
    index_close: pl.DataFrame, prices: pl.DataFrame, cfg: WarningConfig
) -> pl.DataFrame:
    """(date, close, *FEATURES, label)。特徵只用當日（含）以前；label 用未來，期末不足者為 null。"""
    lw, sw, cw = cfg.long_window, cfg.short_window, cfg.change_window
    idx = index_close.sort("date").with_columns(
        _ret=pl.col("close") / pl.col("close").shift(1) - 1.0,
        _ma=pl.col("close").rolling_mean(lw, min_samples=lw),
    )
    idx = idx.with_columns(_vol=pl.col("_ret").rolling_std(sw, min_samples=sw))
    breadth = (
        prices.sort("stock_id", "date")
        .with_columns(_m=pl.col("close").rolling_mean(sw, min_samples=sw).over("stock_id"))
        .filter(pl.col("_m").is_not_null())
        .group_by("date")
        .agg((pl.col("close") > pl.col("_m")).mean().alias("breadth"))
    )
    fwd_min = pl.col("close").rolling_min(cfg.horizon, min_samples=cfg.horizon).shift(-cfg.horizon)
    return (
        idx.join(breadth, on="date", how="left")
        .sort("date")
        .with_columns(
            (pl.col("close") / pl.col("_ma") - 1.0).alias("idx_ma_dev"),
            (pl.col("close") / pl.col("close").shift(lw) - 1.0).alias("idx_runup"),
            (pl.col("_vol") - pl.col("_vol").shift(cw)).alias("idx_vol_chg"),
            (pl.col("breadth") - pl.col("breadth").shift(cw)).alias("breadth_chg"),
            ((fwd_min / pl.col("close") - 1.0) <= -cfg.drop).cast(pl.Float64).alias("label"),
        )
        .with_columns(
            pl.when(fwd_min.is_null()).then(None).otherwise(pl.col("label")).alias("label")
        )
        .select("date", "close", *FEATURES, "label")
    )


def event_spans(
    dates: list[dt.date], labels: list[float | None], gap: int
) -> list[tuple[int, int]]:
    """把連續（間隔 ≤ gap 列）的 label=1 日併成獨立事件，回傳 (起, 迄) 的列索引。"""
    spans: list[list[int]] = []
    for i, y in enumerate(labels):
        if y != 1.0:
            continue
        if spans and i - spans[-1][1] <= gap:
            spans[-1][1] = i
        else:
            spans.append([i, i])
    return [(a, b) for a, b in spans]


def walk_forward_warning(frame: pl.DataFrame, cfg: WarningConfig) -> pl.DataFrame:
    """逐段重訓：第 i 列的機率只由「標籤在 i 之前已揭曉」的列訓練出來的模型給出。

    回傳 (date, p, alarm)；尚未累積 `min_events` 個事件的前段為 null。
    """
    dates = frame["date"].to_list()
    labels = frame["label"].to_list()
    x_all = frame.select(FEATURES).to_numpy()
    usable = ~np.isnan(x_all).any(axis=1)
    n = len(dates)
    p_out: list[float | None] = [None] * n
    alarm: list[bool | None] = [None] * n
    start = 0
    while start < n:
        stop = min(start + cfg.refit_sessions, n)
        known = start - cfg.horizon  # 標籤用到未來 horizon 列：這之前的才算已揭曉
        if known > 0:
            rows = [i for i in range(known) if usable[i] and labels[i] is not None]
            y = np.array([labels[i] for i in rows], dtype=float)
            n_events = len(event_spans(dates[:known], labels[:known], cfg.horizon))
            if n_events >= cfg.min_events and 0.0 < y.mean() < 1.0:
                model = fit_logistic(x_all[rows], y, l2=cfg.l2, max_iter=cfg.max_iter)
                cut = float(np.quantile(predict_logistic(model, x_all[rows]), cfg.alarm_pctl))
                for i in range(start, stop):
                    if usable[i]:
                        p = float(predict_logistic(model, x_all[i : i + 1])[0])
                        p_out[i], alarm[i] = p, p >= cut
        start = stop
    return pl.DataFrame(
        {"date": dates, "p": p_out, "alarm": alarm},
        schema={"date": pl.Date, "p": pl.Float64, "alarm": pl.Boolean},
    )
