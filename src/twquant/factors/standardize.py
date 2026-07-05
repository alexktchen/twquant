"""標準化管線（SPEC-002 §3）。

順序固定：去極值（MAD winsorize）→ z-score，皆為橫斷面、逐日、逐因子。
產業/市值中性化需 stock_meta 與市值資料，待因子庫成熟後加入（此處先不做）。
輸入/輸出皆 long format (date, stock_id, factor, value)。
"""

from __future__ import annotations

import polars as pl

_MAD_SCALE = 1.4826  # 使 MAD 對常態分布無偏估計標準差


def winsorize_mad(df: pl.DataFrame, k: float = 5.0) -> pl.DataFrame:
    """逐 (date, factor) 橫斷面以 median ± k·1.4826·MAD 截尾。"""
    med = pl.col("value").median().over(["date", "factor"])
    mad = (pl.col("value") - med).abs().median().over(["date", "factor"])
    lo = med - k * _MAD_SCALE * mad
    hi = med + k * _MAD_SCALE * mad
    return df.with_columns(pl.col("value").clip(lo, hi).alias("value"))


def zscore(df: pl.DataFrame) -> pl.DataFrame:
    """逐 (date, factor) 橫斷面 z-score。全常數/單一樣本日 → 0（std 為 0 時）。"""
    mean = pl.col("value").mean().over(["date", "factor"])
    std = pl.col("value").std().over(["date", "factor"])
    z = pl.when(std > 0).then((pl.col("value") - mean) / std).otherwise(0.0)
    return df.with_columns(z.alias("value"))


def standardize(df: pl.DataFrame, winsor_k: float = 5.0) -> pl.DataFrame:
    """去極值 → z-score。"""
    return zscore(winsorize_mad(df, k=winsor_k))
