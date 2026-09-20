"""市場狀態分類（T-255／D-124 §三）：一般／緩跌／恐慌。規則、不是模型。

D-121 的證據：危險的不是波動大，是**緩跌**（2022-06 模型挑中者成功率 13.9%）；
恐慌底部反而近八成（2020-04）。兩者的波動度都偏高，個股模型分不出來——所以另立這一層。
12 年裡只有幾段緩跌，拿去訓練只會背答案，故用事前寫死的規則。純函數。
"""

from __future__ import annotations

import polars as pl

CALM = "calm"
GRIND = "grind"  # 指數在均線下、波動沒有放大到恐慌等級：緩跌，不開新倉
PANIC = "panic"  # 指數在均線下、波動達過去一年的高百分位：恐慌，照常可買


def market_regime(
    index_close: pl.DataFrame,
    *,
    ma_window: int,
    vol_window: int,
    vol_lookback: int,
    vol_pctl: float,
) -> pl.DataFrame:
    """回傳 (date, regime)。每一列只用該日（含）以前的指數收盤。

    暖機期（均線或波動百分位還算不出來）`regime` 為 null——不知道就是不知道，
    不得當成 calm（那等於在最沒把握的時候放行）。
    """
    frame = index_close.sort("date").with_columns(
        _ma=pl.col("close").rolling_mean(ma_window, min_samples=ma_window),
        _ret=pl.col("close") / pl.col("close").shift(1) - 1.0,
    )
    frame = frame.with_columns(_vol=pl.col("_ret").rolling_std(vol_window, min_samples=vol_window))
    frame = frame.with_columns(
        _cut=pl.col("_vol").rolling_quantile(
            vol_pctl, interpolation="linear", window_size=vol_lookback, min_samples=vol_lookback
        )
    )
    weak = pl.col("close") < pl.col("_ma")
    known = pl.col("_ma").is_not_null() & pl.col("_cut").is_not_null()
    regime = (
        pl.when(~known)
        .then(None)
        .when(~weak)
        .then(pl.lit(CALM))
        .when(pl.col("_vol") >= pl.col("_cut"))
        .then(pl.lit(PANIC))
        .otherwise(pl.lit(GRIND))
    )
    return frame.select("date", regime.alias("regime"))
