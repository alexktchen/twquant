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


def _scores_for_model(
    view: PitView, params: dict[str, object], default_prefix: str
) -> pl.DataFrame:
    """從 PIT-safe ml_scores 取精確模型或同系列最新分數。"""
    prefix = str(params.get("model_prefix", default_prefix))
    model_id = params.get("model_id")
    scores = view.table("ml_scores")
    if model_id is not None:
        scores = scores.filter(pl.col("model_id") == str(model_id))
    else:
        scores = scores.filter(pl.col("model_id").str.starts_with(prefix))
    out = (
        scores.sort(["date", "stock_id", "model_id"], descending=[False, False, True])
        .unique(subset=["date", "stock_id"], keep="first", maintain_order=True)
        .select("date", "stock_id", pl.col("rank_pct").alias("value"))
        .collect()
        .sort(["date", "stock_id"])
    )
    raw = params.get("smooth_days", 0)
    smooth = int(raw) if isinstance(raw, int | float | str) else 0
    if smooth <= 0:
        return out
    # D-046 候選 3、4：對每檔自己的分數序列做 EMA（span=smooth_days、adjust=False）。
    # 列基而非日曆基——分數缺日既不補 0 也不插值，缺的那天不輸出；只用 ≤ 截點的列
    # （view 已保證），故 assert_no_lookahead 對 smooth_days>0 同樣成立。
    return (
        out.sort(["stock_id", "date"])
        .with_columns(pl.col("value").ewm_mean(span=smooth, adjust=False).over("stock_id"))
        .sort(["date", "stock_id"])
    )


@factor(
    name="ml.lgbm_rank_10d",
    category="ml",
    direction=1,
    tables=["ml_scores"],
    lookback_days=0,
    params={"model_prefix": "lgbm_h10_", "smooth_days": 0},
)
def lgbm_rank_10d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """LightGBM 10 日排序分數；model_id 可精確 pin，否則同系列取最新。

    model_id 格式 lgbm_h{h}_{train_end}_{hash}（SPEC-010 §4）——同前綴下字典序
    即新舊序，故以 model_id 降冪去重。前綴綁定 horizon，屬因子身分（如 mom_20d
    的 20），非調參對象。
    """
    return _scores_for_model(view, params, "lgbm_h10_")


@factor(
    name="ml.lgbm_net_20d",
    category="ml",
    direction=1,
    tables=["ml_scores"],
    lookback_days=0,
    params={"model_prefix": "lgbm_net_h20_", "smooth_days": 0},
)
def lgbm_net_20d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """LightGBM H20 成本感知連續報酬分數；只讀 PIT-safe ml_scores。"""
    return _scores_for_model(view, params, "lgbm_net_h20_")
