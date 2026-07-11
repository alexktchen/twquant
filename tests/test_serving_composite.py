"""D-020：綜合分只納入實證有效子集（純函數，無需 PG，CI 可跑）。

驗證 `composite_from_signed` 只對 subset 內因子取 signed_z 均值、正確算宇宙百分位、
且缺席子集因子時只對出席者平均；並驗證 config 子集正確載入、snapshot 讀同一子集。
"""

from __future__ import annotations

import polars as pl

from twquant.data.serving.composite import composite_from_signed


def _df(rows: list[tuple[str, str, str, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows, schema=["date", "stock_id", "factor", "signed_z"], orient="row"
    ).with_columns(pl.col("date").str.to_date())


SUBSET = ["price.reversal_5d", "price.residual_reversal_5d", "fundamental.revenue_yoy"]


def test_composite_uses_only_subset_factors() -> None:
    """非子集因子（如反向的動能）不得進綜合分——否則會拉低/污染均值。"""
    rows = [
        ("2026-06-01", "1111", "price.reversal_5d", 2.0),
        ("2026-06-01", "1111", "price.residual_reversal_5d", 1.0),
        ("2026-06-01", "1111", "fundamental.revenue_yoy", 0.0),
        ("2026-06-01", "1111", "price.mom_20d", -10.0),  # 非子集，應被忽略
        ("2026-06-01", "1111", "volume.turnover_z_20d", -8.0),  # 非子集
    ]
    out = composite_from_signed(_df(rows), SUBSET)
    val = out.filter(pl.col("stock_id") == "1111")["signed_z_mean"].item()
    assert val == 2.0 / 3 + 1.0 / 3 + 0.0  # (2+1+0)/3 = 1.0，不含 mom_20d/turnover
    assert val == 1.0


def test_composite_missing_subset_factor_averages_present_only() -> None:
    """子集中某因子當日缺席 → 只對出席子集因子平均（不補零、不視為 0）。"""
    rows = [
        ("2026-06-01", "1111", "price.reversal_5d", 3.0),
        ("2026-06-01", "1111", "fundamental.revenue_yoy", 1.0),
        # residual_reversal_5d 缺席
    ]
    out = composite_from_signed(_df(rows), SUBSET)
    assert out.filter(pl.col("stock_id") == "1111")["signed_z_mean"].item() == 2.0  # (3+1)/2


def test_composite_pct_is_universe_rank() -> None:
    """pct = 當日宇宙百分位（0..1，越大越站多方）。"""
    rows = [
        ("2026-06-01", "1111", "price.reversal_5d", 3.0),  # 最高
        ("2026-06-01", "2222", "price.reversal_5d", 1.0),  # 中
        ("2026-06-01", "3333", "price.reversal_5d", -1.0),  # 最低
    ]
    out = composite_from_signed(_df(rows), SUBSET).sort("signed_z_mean")
    assert out["pct"].to_list() == [0.0, 0.5, 1.0]


def test_config_loads_composite_subset() -> None:
    """configs/serving.yaml 的 composite_factors 正確載入（非全因子）。"""
    from pathlib import Path

    from twquant.config import ServingConfig, load_config

    repo = Path(__file__).resolve().parent.parent
    cfg = load_config(repo / "configs" / "serving.yaml", ServingConfig)
    assert list(cfg.composite_factors) == SUBSET


def test_snapshot_reads_same_subset() -> None:
    """live 現算路徑（snapshot）與 PG 物化用同一子集。"""
    from twquant.monitoring.snapshot import _composite_factors

    assert _composite_factors() == SUBSET
