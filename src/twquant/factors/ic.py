"""IC 檢驗（SPEC-002 §4，純運算部分）。

計算因子的預測力：RankIC（因子值與前向報酬的橫斷面 Spearman 相關）時序、均值、
t-stat、資訊比率，以及五分位多空價差。純函數（輸入 polars frame，無 IO）；落地與
research_log 由 reporting 層負責。前向報酬用 shift（假設資料連續；缺口會失真——
故 IC 應在連續期間、乾淨宇宙上算）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import polars as pl


@dataclass(frozen=True)
class HorizonIc:
    """單一前向視窗的 IC 統計。"""

    horizon: int
    n_days: int
    ic_mean: float
    ic_std: float
    ic_ir: float  # 資訊比率 = mean/std
    t_stat: float  # mean / (std/sqrt(n))
    ls_spread: float  # 前五分位 − 後五分位的平均前向報酬


@dataclass(frozen=True)
class IcReport:
    factor: str
    horizons: list[HorizonIc]


def forward_returns(prices: pl.DataFrame, horizon: int) -> pl.DataFrame:
    """每 (date, stock_id) 的 horizon 日前向報酬 close(t+h)/close(t)−1。"""
    fut = pl.col("close").shift(-horizon).over("stock_id", order_by="date")
    return (
        prices.sort(["stock_id", "date"])
        .with_columns((fut / pl.col("close") - 1.0).alias("fwd"))
        .select("date", "stock_id", "fwd")
        .drop_nulls("fwd")
    )


def rank_ic_series(factor_df: pl.DataFrame, fwd_df: pl.DataFrame) -> pl.DataFrame:
    """逐日 RankIC = 因子值秩 與 前向報酬秩 的 Pearson 相關。回傳 (date, rank_ic)。"""
    merged = factor_df.join(fwd_df, on=["date", "stock_id"], how="inner").drop_nulls(
        ["value", "fwd"]
    )
    ranked = merged.with_columns(
        pl.col("value").rank().over("date").alias("rx"),
        pl.col("fwd").rank().over("date").alias("ry"),
    )
    return (
        ranked.group_by("date")
        .agg(pl.corr("rx", "ry").alias("rank_ic"), pl.len().alias("n"))
        .filter(pl.col("n") >= 5)  # 橫斷面樣本太少不計
        .select("date", "rank_ic")
        .drop_nulls("rank_ic")
        .sort("date")
    )


def _quintile_spread(factor_df: pl.DataFrame, fwd_df: pl.DataFrame) -> float:
    """五分位多空價差：每日依因子值分 5 組，(Q5−Q1) 平均前向報酬，再對日期平均。"""
    merged = factor_df.join(fwd_df, on=["date", "stock_id"], how="inner").drop_nulls(
        ["value", "fwd"]
    )
    n_by_date = pl.len().over("date")
    q = (
        (((pl.col("value").rank("ordinal").over("date") - 1) / n_by_date * 5).floor() + 1)
        .clip(1, 5)
        .cast(pl.Int32)
    )
    grp = (
        merged.with_columns(q.alias("q"))
        .group_by(["date", "q"])
        .agg(pl.col("fwd").mean().alias("ret"))
    )
    wide = grp.pivot(values="ret", index="date", on="q")
    if "5" not in wide.columns or "1" not in wide.columns:
        return float("nan")
    val = wide.select((pl.col("5") - pl.col("1")).mean()).item()
    return float(val) if val is not None else float("nan")


def compute_ic_report(
    factor_name: str, factor_df: pl.DataFrame, prices: pl.DataFrame, horizons: list[int]
) -> IcReport:
    """對一個因子算多視窗 IC 報告。factor_df=(date,stock_id,value)，prices=(date,stock_id,close)。"""
    out: list[HorizonIc] = []
    for h in horizons:
        fwd = forward_returns(prices, h)
        ic = rank_ic_series(factor_df, fwd)
        n = ic.height
        mean = ic.select(pl.col("rank_ic").mean()).item() if n else float("nan")
        std = ic.select(pl.col("rank_ic").std()).item() if n > 1 else float("nan")
        mean = float(mean) if mean is not None else float("nan")
        std = float(std) if std is not None else float("nan")
        ir = mean / std if std and not math.isnan(std) and std != 0 else float("nan")
        tstat = mean / (std / math.sqrt(n)) if std and std != 0 and n else float("nan")
        out.append(
            HorizonIc(
                horizon=h,
                n_days=n,
                ic_mean=mean,
                ic_std=std,
                ic_ir=ir,
                t_stat=tstat,
                ls_spread=_quintile_spread(factor_df, fwd),
            )
        )
    return IcReport(factor=factor_name, horizons=out)
