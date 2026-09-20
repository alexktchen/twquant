"""機率校準（T-253／D-124 §一）：isotonic 迴歸，walk-forward、只用當時已揭曉的標籤。

為什麼需要：D-121 量到買進模型在 0.5 以上全部高估（說 65%、實際 40%）。排序還是對的，
但機率數字不能照字面用——而「機率 ≥ 70% 才買」這種門檻必須照字面用。

純 numpy、純函數。沒有 scikit-learn 依賴：PAV 演算法本身只有十幾行。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import polars as pl


@dataclass(frozen=True)
class IsotonicModel:
    """單調不減的分段線性對應：`x` 遞增、`y` 不減。"""

    x: np.ndarray
    y: np.ndarray


def isotonic_fit(scores: np.ndarray, labels: np.ndarray) -> IsotonicModel:
    """Pool-adjacent-violators：把違反單調的相鄰區塊併成加權平均。"""
    if scores.size == 0 or scores.size != labels.size:
        raise ValueError(f"isotonic_fit 需要等長且非空的輸入，得到 {scores.size}／{labels.size}")
    order = np.argsort(scores, kind="stable")
    xs, ys = scores[order].astype(float), labels[order].astype(float)
    # 同一個分數先併成一點，否則 np.interp 的橫軸會有重複值
    ux, inv = np.unique(xs, return_inverse=True)
    w = np.bincount(inv).astype(float)
    m = np.bincount(inv, weights=ys) / w
    vals: list[float] = []
    wts: list[float] = []
    cnt: list[int] = []  # 每個區塊涵蓋幾個 ux
    for value, weight in zip(m, w, strict=True):
        vals.append(float(value))
        wts.append(float(weight))
        cnt.append(1)
        while len(vals) > 1 and vals[-2] > vals[-1]:
            tw = wts[-2] + wts[-1]
            vals[-2] = (vals[-2] * wts[-2] + vals[-1] * wts[-1]) / tw
            wts[-2] = tw
            cnt[-2] += cnt[-1]
            vals.pop()
            wts.pop()
            cnt.pop()
    return IsotonicModel(x=ux, y=np.repeat(np.array(vals), cnt))


def isotonic_predict(model: IsotonicModel, scores: np.ndarray) -> np.ndarray:
    """線性內插；超出訓練範圍者夾在兩端（不外插——外插會吐出 [0,1] 以外的機率）。"""
    return np.asarray(np.interp(scores, model.x, model.y), dtype=float)


def walk_forward_calibrate(
    oos: pl.DataFrame,
    windows: Sequence[tuple[dt.date, dt.date]],
    sessions: Sequence[dt.date],
    *,
    embargo: int,
    min_windows: int,
) -> pl.DataFrame:
    """對每個測試窗，用**該窗開始前標籤已揭曉**的樣本外預測擬合校準曲線，再套到該窗。

    `oos`：(date, stock_id, score, label)；`windows`：各測試窗的 (起, 迄)，依時間排序；
    `sessions`：完整交易日曆（用來把「往前隔離 `embargo` 個交易日」換算成日期）。
    回傳原表加一欄 `p_cal`；前 `min_windows` 個窗沒有足夠歷史，`p_cal` 為 null
    ——**不是**沿用未校準的分數：那正是 D-121 證明不能用的東西。
    """
    index = {d: i for i, d in enumerate(sessions)}
    parts: list[pl.DataFrame] = []
    for k, (start, end) in enumerate(windows):
        test = oos.filter(pl.col("date").is_between(start, end))
        if test.is_empty():
            continue
        if k < min_windows or start not in index or index[start] - embargo <= 0:
            parts.append(test.with_columns(p_cal=pl.lit(None, dtype=pl.Float64)))
            continue
        cutoff = sessions[index[start] - embargo - 1]  # 這一天（含）以前的標籤在窗開始時已全部揭曉
        past = oos.filter((pl.col("date") <= cutoff) & pl.col("label").is_not_null())
        if past.is_empty():
            parts.append(test.with_columns(p_cal=pl.lit(None, dtype=pl.Float64)))
            continue
        model = isotonic_fit(past["score"].to_numpy(), past["label"].to_numpy())
        parts.append(
            test.with_columns(
                p_cal=pl.Series(isotonic_predict(model, test["score"].to_numpy()), dtype=pl.Float64)
            )
        )
    if not parts:
        return oos.with_columns(p_cal=pl.lit(None, dtype=pl.Float64)).head(0)
    return pl.concat(parts).sort("date", "stock_id")
