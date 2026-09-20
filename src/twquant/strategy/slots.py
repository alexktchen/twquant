"""每日補位模式的候選名單（T-247／D-120 §五）。純運算；只透過 PitView 讀資料。"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

import polars as pl

from twquant.errors import ConfigError
from twquant.strategy.universe import universe_ids

if TYPE_CHECKING:
    from twquant.data.warehouse import TableSource
    from twquant.strategy.config import StrategyConfig


def slot_candidates(cfg: StrategyConfig, wh: TableSource, day: dt.date) -> list[tuple[str, float]]:
    """`day` 收盤後過絕對門檻的候選，分數高者在前（同分以代號決定，確保決定性）。

    **回空 list 是正常結果**——那就是「今天沒有夠好的股票」，呼叫端應該空手。
    這與 `build_targets` 不同：那邊宇宙為空是資料問題所以 raise；這邊宇宙照樣由
    `universe_ids` 把關（空宇宙它會回空，缺表它會 raise），只有「沒人過門檻」才安靜回空。
    """
    import twquant.factors.builtins  # noqa: F401  # 匯入觸發全因子註冊
    from twquant.factors.compute import compute

    if cfg.slots is None:
        raise ConfigError(f"策略「{cfg.name}」未設 slots，不能取每日補位候選")
    signal = cfg.signals[0]
    view = wh.view(day)
    uni = universe_ids(view, day, cfg.universe)
    if not uni:
        return []
    df = compute(
        [signal.factor], view, dates=[day], params={signal.factor: dict(signal.params)}
    ).filter(
        pl.col("stock_id").is_in(uni)
        & pl.col("value").is_not_null()
        & (pl.col("value") >= cfg.slots.entry_threshold)
    )
    rows = sorted(
        ((str(r["stock_id"]), float(r["value"])) for r in df.to_dicts()),
        key=lambda item: (-item[1], item[0]),
    )
    return rows
