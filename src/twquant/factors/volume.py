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


def _vol_ratio(view: PitView, window: int) -> pl.DataFrame:
    """量比 = 今日成交量 / 前 window 日均量（不含今日）。"""
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
    return _vol_ratio(view, window)


@factor(
    name="volume.vol_ratio_20d",
    category="volume",
    direction=1,
    tables=["prices_daily"],
    lookback_days=25,
    params={"window": 20},
)
def vol_ratio_20d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """20 日量比 = 今日成交量 / 前 20 日均量（不含今日）；「帶量突破」線的帶量條件（D-215）。

    與 `vol_ratio_5d` 同一個算法、只差窗口；另外註冊是為了物化與「為什麼選它」的句型都以因子名為鍵。
    """
    raw = params.get("window", 20)
    window = int(raw) if isinstance(raw, int | float | str) else 20
    return _vol_ratio(view, window)


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


@factor(
    name="volume.adv_20d",
    category="volume",
    direction=1,
    tables=["prices_daily"],
    lookback_days=25,
    params={"window": 20},
    anchor_invariant=True,
)
def adv_20d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """ADV：過去 window 日成交金額的**中位數**（D-005 的唯一定義，TWD）。

    中位數而非平均：單日爆量（除權息、借殼、當沖行情）會把平均拉高好幾倍，
    而參與率上限要問的是「平常吃得下多少」。窗內不足即 null，不用短窗充數。

    **回傳的是絕對金額**，不是分位——D-143 §三 的規則臂要「取成交額前 15」，
    每日補位的進場分數吃的是原始值。
    """
    raw = params.get("window", 20)
    window = int(raw) if isinstance(raw, int | float | str) else 20
    return (
        view.table("prices_daily")
        .select("date", "stock_id", "amount")
        .sort(["stock_id", "date"])
        .with_columns(value=pl.col("amount").rolling_median(window).over("stock_id"))
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .collect()
    )
