"""綜合分計算（SPEC-009 §3；D-020）：實證有效子集的 signed_z 均值 + 宇宙百分位。

單一真相源——`materialize.py`（寫 PG `composite_daily`）與 `monitoring.snapshot`
（live 現算個股體檢卡）共用此純函數，確保兩路徑演算法一致（T-032）。

D-020 裁決：綜合分只納入 `configs/serving.yaml.composite_factors` 列出的實證有效
子集（短反轉＋產業殘差反轉＋月營收 YoY），而非全部註冊因子等權——等權會把在台股
實證反向的動能/量能因子（signed IC<0）與反轉/營收因子（signed IC>0）互相抵銷，
綜合分樣本外無預測力。純函數、無 IO。
"""

from __future__ import annotations

import polars as pl


def composite_from_signed(
    df: pl.DataFrame,
    subset: list[str],
    signed_col: str = "signed_z",
) -> pl.DataFrame:
    """由 (date, stock_id, factor, <signed_col>) 算綜合分。

    只取 subset 內的因子，逐 (date, stock_id) 取 signed_z 均值為 `signed_z_mean`，
    再逐 date 取宇宙百分位排名 `pct`（0..1，越大越站多方）。回
    (date, stock_id, signed_z_mean, pct)。subset 內某因子在某日缺席時，該日該股的
    均值只對出席的子集因子計算（不補零、不視為 0）。
    """
    sub = df.filter(pl.col("factor").is_in(subset))
    comp = sub.group_by(["date", "stock_id"]).agg(pl.col(signed_col).mean().alias("signed_z_mean"))
    cnt = pl.col("signed_z_mean").count().over("date")
    return comp.with_columns(
        (
            (pl.col("signed_z_mean").rank().over("date") - 1)
            / pl.max_horizontal(cnt - 1, pl.lit(1))
        ).alias("pct")
    )
