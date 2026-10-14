"""模型對每一列的特徵貢獻（D-180「為什麼選它」）：LightGBM 內建的 pred_contrib（TreeSHAP）。

每一列的貢獻加上常數項＝模型的原始分數（二元模型是對數勝算，sigmoid 之後才是機率）。
呼叫端拿同一個 booster、同一組特徵欄位與推論時一樣的輸入；這裡順便算出預測值，讓呼叫端核對
「重建的輸入算出來的分數」與當初公布的分數一樣——對不上就是輸入變了，理由不能用。
"""

from __future__ import annotations

from dataclasses import dataclass

import lightgbm as lgb
import numpy as np
import polars as pl


@dataclass(frozen=True)
class Contributions:
    """每一列（stock_id）的特徵貢獻、常數項與模型輸出（與 booster.predict 相同的尺度）。"""

    stock_id: str
    contribs: dict[str, float]
    bias: float
    prediction: float


def contributions(
    booster: lgb.Booster, frame: pl.DataFrame, features: list[str]
) -> list[Contributions]:
    """frame 每一列的貢獻。`features` 的順序要與訓練時相同（booster.feature_name()），不同即 raise。"""
    names = booster.feature_name()
    if names != features:
        raise ValueError(f"特徵順序與模型不同：模型 {names[:5]}…，給的 {features[:5]}…")
    if frame.is_empty():
        return []
    x = frame.select(features).to_numpy().astype(float)
    contrib = np.asarray(booster.predict(x, pred_contrib=True))
    pred = np.asarray(booster.predict(x))
    if contrib.shape != (frame.height, len(features) + 1):
        raise ValueError(f"pred_contrib 形狀不對：{contrib.shape}")
    sids = frame.get_column("stock_id").to_list()
    return [
        Contributions(
            stock_id=str(sids[i]),
            contribs={f: float(contrib[i, j]) for j, f in enumerate(features)},
            bias=float(contrib[i, -1]),
            prediction=float(pred[i]),
        )
        for i in range(frame.height)
    ]
