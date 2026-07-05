"""因子組合（SPEC-003 §3）。

把多個因子的橫斷面值組成單一分數（越大越看多）。傳入的 signed_weights 已含因子方向
（direction）：direction=-1 的因子（如散戶密集度）以負權重併入，使高分一致代表看多。

第一版：zscore_weighted、rank_avg。ic_weighted（權重 ∝ 過去 250 日 PIT RankIC）
需 IC 歷史重估與反前視測試（AC-4），列為後續（見 TASKS）。
"""

from __future__ import annotations

import polars as pl

from twquant.factors.standardize import standardize


def _weights_frame(signed_weights: dict[str, float]) -> pl.DataFrame:
    return pl.DataFrame(
        {"factor": list(signed_weights), "_w": list(signed_weights.values())},
        schema={"factor": pl.String, "_w": pl.Float64},
    )


def zscore_weighted(factor_df: pl.DataFrame, signed_weights: dict[str, float]) -> pl.DataFrame:
    """Σ w_i × zscore(factor_i)。回傳 (date, stock_id, score)。"""
    std = standardize(factor_df)  # 逐日橫斷面 MAD 去極值 + z-score
    return (
        std.join(_weights_frame(signed_weights), on="factor", how="inner")
        .group_by(["date", "stock_id"])
        .agg((pl.col("value") * pl.col("_w")).sum().alias("score"))
    )


def rank_avg(factor_df: pl.DataFrame, signed_weights: dict[str, float]) -> pl.DataFrame:
    """Σ w_i × 橫斷面百分位排名(factor_i)。回傳 (date, stock_id, score)。"""
    n = pl.len().over(["date", "factor"])
    pct = (pl.col("value").rank().over(["date", "factor"]) - 1) / pl.max_horizontal(
        n - 1, pl.lit(1)
    )
    ranked = factor_df.with_columns(_pct=pct)
    return (
        ranked.join(_weights_frame(signed_weights), on="factor", how="inner")
        .group_by(["date", "stock_id"])
        .agg((pl.col("_pct") * pl.col("_w")).sum().alias("score"))
    )


def combined_score(
    factor_df: pl.DataFrame, signed_weights: dict[str, float], method: str
) -> pl.DataFrame:
    """依 method 組合因子分數。回傳 (date, stock_id, score)。"""
    if method == "zscore_weighted":
        return zscore_weighted(factor_df, signed_weights)
    if method == "rank_avg":
        return rank_avg(factor_df, signed_weights)
    if method == "ic_weighted":
        raise NotImplementedError("ic_weighted 待接 PIT IC 重估（AC-4），見 TASKS T-017 備註")
    raise ValueError(f"未知組合方法：{method}")
