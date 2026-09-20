"""大盤狀態特徵（T-248／D-120 §三）：每個交易日一列，join 到全部個股上。

為什麼需要：個股層級的特徵全是橫斷面相對值，模型看不到「整個市場在跌」。
買進模型要學的是**絕對**機率——沒有大盤狀態，它學不會「現在不是時候」。

指數在這裡是**輸入特徵**，不是比較基準（D-114「驗收不出現指數」不因此牴觸）。
純函數；暖機期（不足 `window` 日）一律 null，不補 0。
"""

from __future__ import annotations

import polars as pl

CONTEXT_FEATURES: tuple[str, ...] = ("ctx.taiex_ma20_dev", "ctx.taiex_vol20", "ctx.breadth_ma20")


def market_state_features(
    index_close: pl.DataFrame, prices: pl.DataFrame, *, window: int
) -> pl.DataFrame:
    """回傳 (date, *CONTEXT_FEATURES)。

    `index_close`：(date, close) 的單一指數序列；`prices`：(date, stock_id, close) 的個股收盤。
    每一列只用到該日（含）以前的資料——滾動視窗皆向後看。
    """
    if window < 2:
        raise ValueError(f"window 須 ≥ 2，得到 {window}")
    idx = index_close.sort("date").with_columns(
        _ma=pl.col("close").rolling_mean(window, min_samples=window),
        _ret=pl.col("close") / pl.col("close").shift(1) - 1.0,
    )
    idx = idx.with_columns(
        (pl.col("close") / pl.col("_ma") - 1.0).alias("ctx.taiex_ma20_dev"),
        pl.col("_ret").rolling_std(window, min_samples=window).alias("ctx.taiex_vol20"),
    )
    breadth = (
        prices.sort("stock_id", "date")
        .with_columns(_ma=pl.col("close").rolling_mean(window, min_samples=window).over("stock_id"))
        .filter(pl.col("_ma").is_not_null())
        .group_by("date")
        .agg((pl.col("close") > pl.col("_ma")).mean().alias("ctx.breadth_ma20"))
    )
    return (
        idx.select("date", "ctx.taiex_ma20_dev", "ctx.taiex_vol20")
        .join(breadth, on="date", how="left")
        .sort("date")
    )
