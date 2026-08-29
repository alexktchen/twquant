"""評估階梯純函數（SPEC-010 §7）：逐日 RankIC、Newey-West 校正、分位單調。

純函數、無 IO——受 IO 靜態閘門掃描（見 tests/test_io_boundary.py 的 _PURE_FILES）。
成本後回測與 DSR（階梯第 3/4 級）走既有 backtest/ 與 validation.py，不在本模組。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise

import polars as pl


def daily_rank_ic(scored: pl.DataFrame, score_col: str, label_col: str) -> pl.DataFrame:
    """逐日橫斷面 spearman(score, label)。回傳 (date, ic, n)，依日期排序。

    label null 列先剔除；單一樣本日的 ic 為 null（相關未定義，不入統計也不 raise）。
    """
    return (
        scored.filter(pl.col(label_col).is_not_null())
        .group_by("date")
        .agg(
            # 單一樣本日 polars 回 NaN（非 null）——統一為 null，讓下游 drop_nulls 生效
            pl.corr(pl.col(score_col).rank("average"), pl.col(label_col).rank("average"))
            .fill_nan(None)
            .alias("ic"),
            pl.len().alias("n"),
        )
        .sort("date")
    )


@dataclass(frozen=True)
class IcSummary:
    """IC 時序摘要。t_newey_west 是重疊標籤下的誠實顯著性（樸素 t 會高估）。"""

    n_days: int
    mean: float
    std: float
    ir: float  # mean / std
    hit_rate: float  # ic > 0 的日數占比
    t_naive: float
    t_newey_west: float


def ic_summary(daily_ic: pl.DataFrame, nw_lag: int) -> IcSummary:
    """IC 時序統計。nw_lag 應設為標籤 horizon——h 日重疊使逐日 IC 正自相關，
    樸素 t（假設獨立）嚴重高估顯著性；Newey-West 以 Bartlett 權重校正。"""
    ic = daily_ic["ic"].drop_nulls().to_list()
    n = len(ic)
    if n < 2:
        raise ValueError(f"無可用 IC 日數（得到 {n} 日，至少需 2 日）")
    mean = sum(ic) / n
    var = sum((x - mean) ** 2 for x in ic) / (n - 1)
    std = math.sqrt(var)
    if std == 0:
        raise ValueError("IC 序列無變異，無法計算顯著性")
    gamma0 = sum((x - mean) ** 2 for x in ic) / n
    nw_var = gamma0
    for k in range(1, min(nw_lag, n - 1) + 1):
        gamma_k = sum((ic[t] - mean) * (ic[t - k] - mean) for t in range(k, n)) / n
        nw_var += 2 * (1 - k / (nw_lag + 1)) * gamma_k
    nw_var *= n / (n - 1)  # 小樣本修正：使 nw_lag=0 恰好退化為樸素 t（同用樣本變異）
    nw_var = max(nw_var, 1e-12)  # 樣本自協方差可能使估計為負，夾住以免開根號失敗
    return IcSummary(
        n_days=n,
        mean=mean,
        std=std,
        ir=mean / std,
        hit_rate=sum(1 for x in ic if x > 0) / n,
        t_naive=mean / (std / math.sqrt(n)),
        t_newey_west=mean / math.sqrt(nw_var / n),
    )


def decile_table(scored: pl.DataFrame, score_col: str, label_col: str, k: int = 10) -> pl.DataFrame:
    """逐日等分位分組（0＝分數最低）→ 各組平均 label 與筆數。回傳 (bucket, avg_label, n)。"""
    if k < 2:
        raise ValueError(f"k 須 ≥ 2，得到 {k}")
    data = scored.filter(pl.col(label_col).is_not_null())
    pos = (pl.col(score_col).rank("ordinal").over("date") - 1) / pl.col(score_col).count().over(
        "date"
    )
    return (
        data.with_columns((pos * k).floor().clip(0, k - 1).cast(pl.Int32).alias("bucket"))
        .group_by("bucket")
        .agg(pl.col(label_col).mean().alias("avg_label"), pl.len().alias("n"))
        .sort("bucket")
    )


def is_monotonic(table: pl.DataFrame, col: str = "avg_label") -> bool:
    """分位表是否嚴格遞增（SPEC-010 §7 第 2 級：不是只有頭尾有差）。"""
    vals = table[col].to_list()
    return all(a < b for a, b in pairwise(vals))


def spread(table: pl.DataFrame, col: str = "avg_label") -> float:
    """頭尾價差（最高分位 − 最低分位）。"""
    vals = table[col].to_list()
    return float(vals[-1] - vals[0])


def equal_weight_signed(directions: Mapping[str, int]) -> dict[str, float]:
    """等權基準的 signed_weights：每因子 direction/n（SPEC-010 §7 的永久對照組）。

    direction=-1 的因子以負權重併入，使高分一致代表看多（同 strategy/combine 的約定）。
    刻意等權而非調參——基準若也可調，「打贏基準」就失去意義。
    """
    if not directions:
        raise ValueError("directions 為空：等權基準至少需要一個因子")
    bad = {k: v for k, v in directions.items() if v not in (1, -1)}
    if bad:
        raise ValueError(f"direction 僅允許 ±1，違反者：{bad}")
    n = len(directions)
    return {name: d / n for name, d in directions.items()}
