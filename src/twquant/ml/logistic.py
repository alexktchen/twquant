"""邏輯斯迴歸（T-254／D-122 §四）：大跌預警用。純 numpy 的 IRLS，帶 L2。

刻意不用樹模型：12 年只有 9 個獨立的大跌事件，LightGBM 一定背答案；
5 個事先指定的特徵配一條線性邊界，是這個樣本量撐得起的上限。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class LogisticModel:
    mean: np.ndarray
    std: np.ndarray
    intercept: float
    coef: np.ndarray  # 對**標準化後**的特徵


def fit_logistic(x: np.ndarray, y: np.ndarray, *, l2: float, max_iter: int) -> LogisticModel:
    """特徵先以訓練樣本的均值／標準差標準化，再以 IRLS 求解。截距不罰。"""
    if np.isnan(x).any() or np.isnan(y).any():
        raise ValueError("fit_logistic 的輸入含 NaN——暖機期的列要先濾掉，不得補 0")
    if len(np.unique(y)) < 2:
        raise ValueError("fit_logistic 需要兩類都出現；訓練期內沒有事件就不能訓練")
    mean, std = x.mean(axis=0), x.std(axis=0)
    std = np.where(std > 0, std, 1.0)
    z = np.column_stack([np.ones(len(x)), (x - mean) / std])
    beta = np.zeros(z.shape[1])
    penalty = np.eye(z.shape[1]) * l2
    penalty[0, 0] = 0.0
    for _ in range(max_iter):
        p = 1.0 / (1.0 + np.exp(-np.clip(z @ beta, -30, 30)))
        w = np.clip(p * (1 - p), 1e-9, None)
        grad = z.T @ (y - p) - penalty @ beta
        hess = (z * w[:, None]).T @ z + penalty
        step = np.linalg.solve(hess, grad)
        beta = beta + step
        if np.abs(step).max() < 1e-8:
            break
    return LogisticModel(mean=mean, std=std, intercept=float(beta[0]), coef=beta[1:])


def predict_logistic(model: LogisticModel, x: np.ndarray) -> np.ndarray:
    z = (x - model.mean) / model.std
    return 1.0 / (1.0 + np.exp(-np.clip(model.intercept + z @ model.coef, -30, 30)))
