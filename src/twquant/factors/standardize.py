"""標準化管線（SPEC-002 §3）。

順序固定：去極值（MAD winsorize）→ z-score，皆為橫斷面、逐日、逐因子。
產業/市值中性化需 stock_meta 與市值資料，待因子庫成熟後加入（此處先不做）。
輸入/輸出皆 long format (date, stock_id, factor, value)。

**MAD = 0 的橫斷面走排名標準化（T-107／T-108，依 D-052 建議甲）。**
稀疏因子（過半股票同值，籌碼類的常態）的 MAD 是 0，於是 `median ± k·MAD` 的上下界
同為 median，`clip` 把整個橫斷面壓成常數，z 全為 0——因子被標準化關掉了，而沒有任何
一關報錯。實測 2025-06-30 的 `chip.trust_streak`：1,775 列、16 個相異原始值、
**1 個相異 z**；D-074 稱為「先導最強的籌碼訊號」的那一欄，在模型裡是一排 0。

為什麼是排名而不是「MAD = 0 時跳過截尾」（D-051 的 option B）：後者在該日完全不截尾，
實測 |z| 中位約 10、p99 17~25——把一個衛生問題換成另一個。排名轉換給出**完全相同的
Spearman IC**（單調變換），輸出仍是均值 0、標準差 1，且有界。

MAD > 0 的橫斷面**逐位元不變**：走的是與先前完全相同的 winsorize → zscore 路徑。
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


def rank_zscore(df: pl.DataFrame) -> pl.DataFrame:
    """逐 (date, factor) 橫斷面**以名次**標準化：平均名次法處理同分，再對名次 z 標準化。

    給 MAD = 0 的稀疏因子用。輸出仍是均值 0、標準差 1，且**只取決於順序**
    ——這是它勝過「MAD = 0 時跳過截尾」（D-051 option B）的關鍵：後者把原始尺度
    直接餵給 z-score，一檔離群值有多離群就會改變當日每一個 z。單調變換 →
    Spearman IC 與原始值完全相同。

    同分很多時尾端仍可能偏大（上界是 √(n−1)，發生在「n−1 檔同分 ＋ 1 檔獨立」），
    但那幾檔正是當天唯一帶資訊的觀測，不是被放大的雜訊。

    非有限值（NaN/inf）不參與名次與 mean/std，且其自身輸出維持非有限（同 `zscore`）。
    """
    finite = pl.when(pl.col("value").is_finite()).then(pl.col("value"))
    ranked = finite.rank(method="average").over(["date", "factor"])
    mean = ranked.mean().over(["date", "factor"])
    std = ranked.std().over(["date", "factor"])
    z = (
        pl.when(~pl.col("value").is_finite())
        .then(pl.col("value"))
        .when(std > 0)
        .then((ranked - mean) / std)
        .otherwise(0.0)  # 全同值（相異值只有 1 個）：名次也全同，仍是 0
    )
    return df.with_columns(z.alias("value"))


def standardize(df: pl.DataFrame, winsor_k: float = 5.0) -> pl.DataFrame:
    """去極值 → z-score；**MAD = 0 的橫斷面改走排名標準化**（T-107／T-108）。

    路由條件用 MAD 而非「相異值個數」：MAD = 0 正是截尾把橫斷面壓成常數的**充要條件**
    ——上下界都等於 median 時，`clip` 的結果必然是常數。用它當開關，MAD > 0 的橫斷面
    就走與先前逐位元相同的路徑，不會有任何既有結果被順手改掉。
    """
    finite = pl.when(pl.col("value").is_finite()).then(pl.col("value"))
    med = finite.median().over(["date", "factor"])
    mad = (finite - med).abs().median().over(["date", "factor"])
    return (
        df.with_columns(
            _dense=zscore(winsorize_mad(df, k=winsor_k))["value"],
            _sparse=rank_zscore(df)["value"],
            _mad=mad,
        )
        .with_columns(
            pl.when(pl.col("_mad") > 0.0)
            .then(pl.col("_dense"))
            .otherwise(pl.col("_sparse"))
            .alias("value")
        )
        .drop("_dense", "_sparse", "_mad")
    )
