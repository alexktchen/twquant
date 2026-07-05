"""量能類因子（SPEC-002 / STRATEGY §1-5、§4 的 P1 量價）。

量是動能的條件而非獨立訊號（STRATEGY §1-5）。以現有 prices_daily 的成交量可算：
量比（今量 / 前 N 日均量）、量能時序 z-score。周轉率的真身需在外流通股數（尚未接），
此處以成交量代理。滾動視窗以列數計，缺口期慎用（同 price.mom_20d 限制）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from twquant.factors.registry import factor

if TYPE_CHECKING:
    from twquant.data.warehouse import PitView


@factor(
    name="volume.vol_ratio_5d",
    category="volume",
    direction=1,
    tables=["prices_daily"],
    lookback_days=8,
    params={"window": 5},
)
def vol_ratio_5d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """量比 = 今日成交量 / 前 N 日均量（不含今日；異常放量偏多，STRATEGY §1-5）。"""
    raw = params.get("window", 5)
    window = int(raw) if isinstance(raw, int | float | str) else 5
    px = view.table("prices_daily").select("date", "stock_id", "volume").sort(["stock_id", "date"])
    prev_avg = (
        pl.col("volume").shift(1).rolling_mean(window).over("stock_id")
    )  # 前 window 日均量（排除今日）
    return (
        px.with_columns(_pa=prev_avg)
        .with_columns(
            value=pl.when(pl.col("_pa") > 0).then(pl.col("volume") / pl.col("_pa")).otherwise(None)
        )
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .collect()
    )


@factor(
    name="volume.turnover_z_20d",
    category="volume",
    direction=1,
    tables=["prices_daily"],
    lookback_days=25,
    params={"window": 20},
)
def turnover_z_20d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """量能時序 z-score = (今量 − 20 日均量) / 20 日標準差（放量偏多）。"""
    raw = params.get("window", 20)
    window = int(raw) if isinstance(raw, int | float | str) else 20
    px = view.table("prices_daily").select("date", "stock_id", "volume").sort(["stock_id", "date"])
    m = pl.col("volume").rolling_mean(window).over("stock_id")
    s = pl.col("volume").rolling_std(window).over("stock_id")
    return (
        px.with_columns(_m=m, _s=s)
        .with_columns(
            value=pl.when(pl.col("_s") > 0)
            .then((pl.col("volume") - pl.col("_m")) / pl.col("_s"))
            .otherwise(None)
        )
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .collect()
    )
