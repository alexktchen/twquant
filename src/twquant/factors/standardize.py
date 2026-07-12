"""標準化管線（SPEC-002 §3）。

順序固定：去極值（MAD winsorize）→ z-score，皆為橫斷面、逐日、逐因子。
產業/市值中性化需 stock_meta 與市值資料，待因子庫成熟後加入（此處先不做）。
輸入/輸出皆 long format (date, stock_id, factor, value)。
"""

from __future__ import annotations

import polars as pl

_MAD_SCALE = 1.4826  # 使 MAD 對常態分布無偏估計標準差


def winsorize_mad(df: pl.DataFrame, k: float = 5.0) -> pl.DataFrame:
    """逐 (date, factor) 橫斷面以 median ± k·1.4826·MAD 截尾。

    群內非有限值（NaN/inf）不參與 median/MAD 估計，避免單一退化股扭曲截尾界限
    （polars mean/std 對群內任一 NaN 會整群傳染成 NaN；median 則把 NaN 排到尾端仍偏移）。
    """
    finite = pl.when(pl.col("value").is_finite()).then(pl.col("value"))
    med = finite.median().over(["date", "factor"])
    mad = (finite - med).abs().median().over(["date", "factor"])
    lo = med - k * _MAD_SCALE * mad
    hi = med + k * _MAD_SCALE * mad
    return df.with_columns(pl.col("value").clip(lo, hi).alias("value"))


def zscore(df: pl.DataFrame) -> pl.DataFrame:
    """逐 (date, factor) 橫斷面 z-score。全常數/單一樣本日 → 0（std 為 0 時）。

    群內非有限值不參與 mean/std 估計，且其自身 z 維持非有限——如此單一退化股只會被
    下游 filter(is_finite) 剔除，而非讓整個 (date, factor) 橫斷面 z 皆成 NaN 被整因子丟棄
    （D-020：一檔壞值曾使某因子在約半數交易日消失）。
    """
    finite = pl.when(pl.col("value").is_finite()).then(pl.col("value"))
    mean = finite.mean().over(["date", "factor"])
    std = finite.std().over(["date", "factor"])
    z = (
        pl.when(~pl.col("value").is_finite())
        .then(pl.col("value"))  # 非有限輸入原樣保留（NaN/inf）→ 下游過濾
        .when(std > 0)
        .then((pl.col("value") - mean) / std)
        .otherwise(0.0)
    )
    return df.with_columns(z.alias("value"))


def standardize(df: pl.DataFrame, winsor_k: float = 5.0) -> pl.DataFrame:
    """去極值 → z-score。"""
    return zscore(winsorize_mad(df, k=winsor_k))
