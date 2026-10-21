"""雲端可用的新聞情緒小模型（T-424／D-205）：新聞向量＋「股名：證據片段」向量 → 一層 MLP → 好／中性／壞。

設定凍結在 D-205 §二（configs/news.yaml hist.sentiment.mlp 只是把那些數字搬進設定，改了等於違反事前登記）。
numpy 實作、決定性（固定 seed、全批次排序固定）；推論公式簡單到雲端 TypeScript 照抄：relu(x·W1+b1)·W2+b2 → softmax。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

CLASSES = (-1, 0, 1)  # 壞、中性、好（索引 0、1、2）


def pair_text(stock_name: str, evidence: str | None, title: str) -> str:
    """v_pair 的文字：「股名：證據片段」；沒有證據片段（來源自帶的股票標籤）→「股名：標題」。雲端同一條規則。"""
    body = (evidence or "").strip() or title.strip()
    return f"{stock_name}：{body}"


def features(v_news: np.ndarray, v_pair: np.ndarray) -> np.ndarray:
    """x ＝ [v_news, v_pair, v_news ⊙ v_pair]（D-205 §二）。輸入要已正規化。"""
    if v_news.shape != v_pair.shape:
        raise ValueError(f"v_news {v_news.shape} 跟 v_pair {v_pair.shape} 形狀要一樣")
    return np.concatenate([v_news, v_pair, v_news * v_pair], axis=1).astype(np.float32)


def in_valid(item_id: str, share: float) -> bool:
    """驗證集按新聞切：md5(item_id) 的前 8 位當 0～1 的數，小於 share 就是驗證集（同一則新聞的所有配對同一邊）。"""
    h = int(hashlib.md5(item_id.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF
    return h < share


@dataclass(frozen=True)
class Mlp:
    w1: np.ndarray  # (d, h)
    b1: np.ndarray  # (h,)
    w2: np.ndarray  # (h, k)
    b2: np.ndarray  # (k,)

    def proba(self, x: np.ndarray) -> np.ndarray:
        h = np.maximum(x @ self.w1 + self.b1, 0.0)
        z = h @ self.w2 + self.b2
        z = z - z.max(axis=1, keepdims=True)
        e = np.exp(z)
        return np.asarray(e / e.sum(axis=1, keepdims=True))


def train_mlp(
    v_news: np.ndarray,
    v_pair: np.ndarray,
    yi: np.ndarray,
    k: int,
    *,
    hidden: int,
    l2: float,
    lr: float,
    batch: int,
    epochs: int,
    seed: int,
) -> Mlp:
    """類別反頻率加權交叉熵、L2、Adam。yi 是 0..k-1。
    特徵每一小批才組（features）：100 萬對 × 3,072 維一次展開要 12 GB，向量用半精度存只要 4 GB。"""
    n = len(yi)
    if not (len(v_news) == len(v_pair) == n) or n == 0:
        raise ValueError("v_news、v_pair、yi 長度要一樣且不為空")
    rng = np.random.default_rng(seed)
    d = v_news.shape[1] * 3
    counts = np.bincount(yi, minlength=k).astype(np.float64)
    cw = (n / (k * np.maximum(counts, 1))).astype(np.float32)
    params = [
        (rng.standard_normal((d, hidden)) * np.sqrt(2.0 / d)).astype(np.float32),
        np.zeros(hidden, np.float32),
        (rng.standard_normal((hidden, k)) * np.sqrt(2.0 / hidden)).astype(np.float32),
        np.zeros(k, np.float32),
    ]
    m = [np.zeros_like(q) for q in params]
    v = [np.zeros_like(q) for q in params]
    beta1, beta2, eps = 0.9, 0.999, 1e-8
    step = 0
    onehot = np.eye(k, dtype=np.float32)
    for _ in range(epochs):
        order = rng.permutation(n)
        for s in range(0, n, batch):
            idx = np.sort(order[s : s + batch])
            xb = features(v_news[idx].astype(np.float32), v_pair[idx].astype(np.float32))
            yb = yi[idx]
            w1, b1, w2, b2 = params
            pre = xb @ w1 + b1
            h = np.maximum(pre, 0.0)
            z = h @ w2 + b2
            z -= z.max(axis=1, keepdims=True)
            p = np.exp(z)
            p /= p.sum(axis=1, keepdims=True)
            wgt = cw[yb][:, None]
            g = (p - onehot[yb]) * wgt / wgt.sum()
            grads = (
                xb.T @ ((g @ w2.T) * (pre > 0)) + l2 * w1,
                ((g @ w2.T) * (pre > 0)).sum(axis=0),
                h.T @ g + l2 * w2,
                g.sum(axis=0),
            )
            step += 1
            for i, gr in enumerate(grads):
                m[i] = beta1 * m[i] + (1 - beta1) * gr
                v[i] = beta2 * v[i] + (1 - beta2) * gr * gr
                mh = m[i] / (1 - beta1**step)
                vh = v[i] / (1 - beta2**step)
                params[i] = (params[i] - lr * mh / (np.sqrt(vh) + eps)).astype(np.float32)
    return Mlp(*params)


def predict_proba(model: Mlp, v_news: np.ndarray, v_pair: np.ndarray, batch: int) -> np.ndarray:
    """分批推論（同樣不一次展開全部特徵）。"""
    out = [
        model.proba(
            features(
                v_news[s : s + batch].astype(np.float32), v_pair[s : s + batch].astype(np.float32)
            )
        )
        for s in range(0, len(v_news), batch)
    ]
    return np.concatenate(out) if out else np.zeros((0, len(CLASSES)), np.float32)


def export_weights(
    model: Mlp, *, version: str, metrics: dict[str, Any], decimals: int
) -> dict[str, Any]:
    """給雲端新聞服務的 JSON：w1 是 (d × h)，按列存；四捨五入到 decimals 位。"""

    def rnd(a: np.ndarray) -> Any:
        return np.round(a.astype(np.float64), decimals).tolist()

    return {
        "version": version,
        "model": "BAAI/bge-m3",
        "classes": ["neg", "neu", "pos"],
        "features": "concat(v_news, v_pair, v_news*v_pair)",
        "metrics": metrics,
        "w1": rnd(model.w1),
        "b1": rnd(model.b1),
        "w2": rnd(model.w2),
        "b2": rnd(model.b2),
    }


def confusion(y_true: Sequence[int], y_pred: Sequence[int]) -> list[list[int]]:
    """3×3 混淆矩陣，列＝真實（壞、中性、好）、欄＝預測。"""
    pos = {c: i for i, c in enumerate(CLASSES)}
    out = [[0, 0, 0] for _ in CLASSES]
    for t, p in zip(y_true, y_pred, strict=True):
        out[pos[int(t)]][pos[int(p)]] += 1
    return out
