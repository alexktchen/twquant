"""資料集組裝（SPEC-010 §2）：前向報酬標籤與特徵×標籤對齊。純函數、無 IO。

標籤只存在於訓練組裝路徑（研究軌）；推論路徑（ml score，T-067）不得 import 本模組
的標籤函數——PIT 上「特徵在 t、標籤看 t+1 之後」的不對稱由這條邊界維持。
"""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl


def forward_open_returns(prices: pl.DataFrame, horizon: int) -> pl.DataFrame:
    """open(t+1) → open(t+1+h) 前向報酬。輸入需含 (date, stock_id, open)。

    「t+1」是各股票自身的下一個有行情列——停牌期間不可成交，進出場順延（與回測
    引擎的可成交語意一致）。未來列不足者 fwd_ret 為 null（保留列，不靜默丟棄）。
    """
    if horizon < 1:
        raise ValueError(f"horizon 須 ≥ 1，得到 {horizon}")
    return (
        prices.sort("stock_id", "date")
        .with_columns(
            (
                pl.col("open").shift(-(1 + horizon)).over("stock_id")
                / pl.col("open").shift(-1).over("stock_id")
                - 1.0
            ).alias("fwd_ret")
        )
        .select("date", "stock_id", "fwd_ret")
        .sort("date", "stock_id")
    )


def excess_rank_labels(fwd: pl.DataFrame) -> pl.DataFrame:
    """同日超額（減宇宙均值）→ 橫斷面百分位 label ∈ (0, 1]；fwd_ret null → label null。

    rank 轉換下超額化不改變順位；保留此步是讓 label.transform（SPEC-010 §1）
    日後可換成非 rank 目標時語意不變。
    """
    return (
        fwd.with_columns(
            (pl.col("fwd_ret") - pl.col("fwd_ret").mean().over("date")).alias("_excess")
        )
        .with_columns(
            (
                pl.col("_excess").rank(method="average").over("date")
                / pl.col("_excess").count().over("date")
            ).alias("label")
        )
        .select("date", "stock_id", "label")
        .sort("date", "stock_id")
    )


def cross_rank_pct(frame: pl.DataFrame, value_col: str, out_col: str) -> pl.DataFrame:
    """同日橫斷面百分位 (0,1]；null 傳遞、不丟列（`ml score` 寫 rank_pct 用，SPEC-010 §5）。"""
    return frame.with_columns(
        (
            pl.col(value_col).rank(method="average").over("date")
            / pl.col(value_col).count().over("date")
        ).alias(out_col)
    )


def assemble(features: Mapping[str, pl.DataFrame], labels: pl.DataFrame) -> pl.DataFrame:
    """特徵長表（各為 (date, stock_id, value)）outer join 成寬表，label 以 left join 掛上。

    缺值一律保留 null（LightGBM 原生處理；黃金規則 5 禁止填 0）。
    輸出欄位 (date, stock_id, <特徵名...>, label)，依 (date, stock_id) 排序。
    """
    if not features:
        raise ValueError("features 為空：至少需要一個特徵")
    wide: pl.DataFrame | None = None
    for name, df in features.items():
        part = df.select("date", "stock_id", pl.col("value").alias(name))
        wide = (
            part
            if wide is None
            else wide.join(part, on=["date", "stock_id"], how="full", coalesce=True)
        )
    assert wide is not None  # features 非空已檢查
    return wide.join(
        labels.select("date", "stock_id", "label"), on=["date", "stock_id"], how="left"
    ).sort("date", "stock_id")
