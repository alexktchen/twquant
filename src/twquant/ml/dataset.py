"""資料集組裝（SPEC-010 §2）：前向報酬標籤與特徵×標籤對齊。純函數、無 IO。

標籤只存在於訓練組裝路徑（研究軌）；推論路徑（ml score，T-067）不得 import 本模組
的標籤函數——PIT 上「特徵在 t、標籤看 t+1 之後」的不對稱由這條邊界維持。
"""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl

from twquant.backtest.costs import CostConfig, trade_cost


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


def cost_adjusted_return_labels(
    prices: pl.DataFrame,
    *,
    horizon: int,
    adv_window_sessions: int,
    position_notional_twd: float,
    downside_penalty: float,
    cost_cfg: CostConfig,
) -> pl.DataFrame:
    """建立 H 日成本後超額報酬標籤；ADV 僅含訊號日及以前資料。"""
    required = {"date", "stock_id", "open", "amount"}
    missing = sorted(required - set(prices.columns))
    if missing:
        raise ValueError(f"成本感知標籤缺行情欄位：{', '.join(missing)}")
    if horizon < 1:
        raise ValueError(f"horizon 須 >= 1，得到 {horizon}")
    if adv_window_sessions < 1:
        raise ValueError(f"adv_window_sessions 須 >= 1，得到 {adv_window_sessions}")
    if position_notional_twd <= 0:
        raise ValueError(f"position_notional_twd 須 > 0，得到 {position_notional_twd}")
    if downside_penalty < 0:
        raise ValueError(f"downside_penalty 須 >= 0，得到 {downside_penalty}")

    def estimated_cost_rate(row: dict[str, float | None]) -> float | None:
        entry_raw, exit_raw, adv_raw = row["_entry"], row["_exit"], row["_adv"]
        if entry_raw is None or exit_raw is None or adv_raw is None:
            return None
        entry, exit_, adv = float(entry_raw), float(exit_raw), float(adv_raw)
        if entry <= 0.0 or exit_ <= 0.0 or adv <= 0.0:
            return None
        shares = int(position_notional_twd // entry)
        if shares < 1:
            return None
        participation = position_notional_twd / adv
        buy = trade_cost(
            side="buy", price=entry, shares=shares, participation=participation, cfg=cost_cfg
        )
        sell = trade_cost(
            side="sell", price=exit_, shares=shares, participation=participation, cfg=cost_cfg
        )
        return (buy.total + sell.total) / (entry * shares)

    prepared = (
        prices.sort("stock_id", "date")
        .with_columns(
            pl.col("open").shift(-1).over("stock_id").alias("_entry"),
            pl.col("open").shift(-(1 + horizon)).over("stock_id").alias("_exit"),
            pl.col("amount")
            .rolling_median(window_size=adv_window_sessions, min_samples=adv_window_sessions)
            .over("stock_id")
            .alias("_adv"),
        )
        .with_columns(
            # schema 允許 open >= 0，故 0 是合法值；但 SPEC-011 §2 要求「價格無效」時
            # label 為 null。少了這道防護，除以 0 會得到 inf，再被下一步的當日均值
            # 擴散到整個橫斷面（成本路徑早已有相同判斷，見 estimated_cost_rate）。
            pl.when((pl.col("_entry") > 0.0) & (pl.col("_exit") > 0.0))
            .then(pl.col("_exit") / pl.col("_entry") - 1.0)
            .otherwise(pl.lit(None, dtype=pl.Float64))
            .alias("_gross"),
            pl.struct("_entry", "_exit", "_adv")
            .map_elements(estimated_cost_rate, return_dtype=pl.Float64)
            .alias("_cost"),
        )
        # mean() 略過 null，故均值即 SPEC-011 §2 的「當日有效宇宙」平均。
        .with_columns((pl.col("_gross") - pl.col("_gross").mean().over("date")).alias("_excess"))
        .with_columns((pl.col("_excess") - pl.col("_cost")).alias("_net"))
    )
    return (
        prepared.with_columns(
            pl.when(pl.col("_net") < 0.0)
            .then(pl.col("_net") * (1.0 + downside_penalty))
            .otherwise(pl.col("_net"))
            .alias("label")
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


def assert_feature_coverage(
    features: Mapping[str, pl.DataFrame], *, min_non_null_share: float = 0.0
) -> dict[str, float]:
    """訓練前的特徵覆蓋閘門（T-088 AC-3）：任一特徵無列或非空比例 ≤ 門檻即 raise，回傳各特徵非空比例。

    Phase 1 的 25 個 pin 特徵裡有 2 個在 factor_values 從未有值（D-045）、1 個全 null（T-082），
    LightGBM 照樣「成功」訓練——全空特徵在模型裡是白佔的欄位，而沒有任何一關報錯。
    只掛在訓練路徑；生產 `ml score` 單日缺某因子屬正常，不得套用。
    """
    if not 0.0 <= min_non_null_share < 1.0:
        raise ValueError(f"min_non_null_share 須介於 [0, 1)，得到 {min_non_null_share}")
    shares: dict[str, float] = {}
    dead: list[str] = []
    for name, df in features.items():
        n = df.height
        share = float(df["value"].is_not_null().sum() / n) if n else 0.0
        shares[name] = share
        if n == 0 or share <= min_non_null_share:
            dead.append(f"{name}（{n:,} 列、非空 {share:.1%}）")
    if dead:
        raise ValueError(
            "特徵覆蓋不足，拒絕訓練："
            + "；".join(dead)
            + f"。門檻 non-null > {min_non_null_share:.0%}。全空或近空的特徵在模型裡是白佔的欄位"
            "（Phase 1 曾以 3 個空特徵訓練並回報成功）——先補資料或從配置移除，不得靜默訓練。"
        )
    return shares


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
