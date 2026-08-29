"""ML 排序層薄因子（SPEC-010 §6）：讀 ml_scores 表，不做任何新計算。純函數、無 IO。

分數由研究軌訓練的模型於 `ml score`（邊界層）落地成 ml_scores（含 available_at），
本模組只透過 `view.table("ml_scores")` 讀取——黃金規則 1 與 IO 靜態閘門不變。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from twquant.factors.registry import factor

if TYPE_CHECKING:
    from twquant.data.warehouse import PitView


@factor(
    name="ml.lgbm_rank_10d",
    category="ml",
    direction=1,
    tables=["ml_scores"],
    lookback_days=0,
    params={"model_prefix": "lgbm_h10_"},
)
def lgbm_rank_10d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """LightGBM 10 日排序分數（rank_pct）。同 (date, stock) 多模型並存時取最新。

    model_id 格式 lgbm_h{h}_{train_end}_{hash}（SPEC-010 §4）——同前綴下字典序
    即新舊序，故以 model_id 降冪去重。前綴綁定 horizon，屬因子身分（如 mom_20d
    的 20），非調參對象。
    """
    prefix = str(params.get("model_prefix", "lgbm_h10_"))
    return (
        view.table("ml_scores")
        .filter(pl.col("model_id").str.starts_with(prefix))
        .sort(["date", "stock_id", "model_id"], descending=[False, False, True])
        .unique(subset=["date", "stock_id"], keep="first", maintain_order=True)
        .select("date", "stock_id", pl.col("rank_pct").alias("value"))
        .collect()
        .sort(["date", "stock_id"])
    )
