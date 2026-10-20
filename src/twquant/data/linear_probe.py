"""線性探針（softmax 迴歸，numpy）：在 bge-m3 向量上學分類標籤。情緒（T-413）與新聞類型（T-421）共用。

全批次梯度下降＋L2、類別反頻率加權；固定 seed → 決定性。沒有外部套件（factors/ml 也不會 import 這裡以外的東西）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Softmax:
    w: np.ndarray  # (d, k)
    b: np.ndarray  # (k,)

    def proba(self, x: np.ndarray) -> np.ndarray:
        z = x @ self.w + self.b
        z = z - z.max(axis=1, keepdims=True)
        e = np.exp(z)
        return np.asarray(e / e.sum(axis=1, keepdims=True))


def train_softmax(
    x: np.ndarray,
    yi: np.ndarray,
    k: int,
    *,
    l2: float = 1e-3,
    epochs: int = 300,
    lr: float = 0.5,
    seed: int = 7,
) -> Softmax:
    """x (n, d)、yi (n,) 類別索引 0..k-1。"""
    if len(x) != len(yi) or len(x) == 0:
        raise ValueError("x、yi 長度要一樣且不為空")
    if yi.min() < 0 or yi.max() >= k:
        raise ValueError(f"類別索引要在 0..{k - 1}")
    n, d = x.shape
    counts = np.bincount(yi, minlength=k).astype(float)
    cw = (n / (k * np.maximum(counts, 1)))[yi]
    rng = np.random.default_rng(seed)
    w = rng.normal(0, 0.01, (d, k))
    b = np.zeros(k)
    onehot = np.eye(k)[yi]
    for _ in range(epochs):
        z = x @ w + b
        z -= z.max(axis=1, keepdims=True)
        p = np.exp(z)
        p /= p.sum(axis=1, keepdims=True)
        g = (p - onehot) * cw[:, None] / n
        w -= lr * (x.T @ g + l2 * w)
        b -= lr * g.sum(axis=0)
    return Softmax(w=w, b=b)


def macro_f1(y_true: np.ndarray, y_pred: np.ndarray, k: int) -> float:
    """各類 F1 的平均；真實與預測都沒出現的類別不算。"""
    f1s = []
    for c in range(k):
        tp = float(((y_true == c) & (y_pred == c)).sum())
        fp = float(((y_true != c) & (y_pred == c)).sum())
        fn = float(((y_true == c) & (y_pred != c)).sum())
        if tp + fp + fn == 0:
            continue
        f1s.append(0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))
    return float(np.mean(f1s)) if f1s else 0.0


def split(n: int, valid_share: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """固定 seed 的訓練／驗證切分（索引）。"""
    rng = np.random.default_rng(seed)
    idx = rng.permutation(n)
    k = round(n * valid_share)
    return idx[k:], idx[:k]
