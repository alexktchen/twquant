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
    import twquant.factors.builtins  # 匯入觸發全因子註冊
    from twquant.factors.compute import compute

    if cfg.slots is None:
        raise ConfigError(f"策略「{cfg.name}」未設 slots，不能取每日補位候選")
    signal = cfg.signals[0]
    view = wh.view(day)
    # 先套絕對門檻、再算宇宙：門檻嚴的策略大多數日子沒有任何人過門檻，
    # 而宇宙過濾要掃全市場 20 日滾動——順序對調不改結果，只省掉白做的工。
    df = compute(
        [signal.factor], view, dates=[day], params={signal.factor: dict(signal.params)}
    ).filter(pl.col("value").is_not_null() & (pl.col("value") >= cfg.slots.entry_threshold))
    if df.is_empty():
        return []
    uni = universe_ids(view, day, cfg.universe)
    df = df.filter(pl.col("stock_id").is_in(uni))
    for rule in cfg.slots.exclude:
        # D-172：排除規則的因子可能不在 builtins（資金流因子只在地端有資料），用到時才註冊
        import twquant.factors.fund_flow  # noqa: F401

        # 逐日補位會呼叫上千次：依因子宣告的回看裁下界（T-083；只動下界、不可能引入前視，
        # 等價性由 tests/test_fund_flow_factors.py 的 trim 測試守著）
        ex = compute(
            [rule.factor],
            view,
            dates=[day],
            params={rule.factor: dict(rule.params)},
            trim_lookback=True,
        )
        blocked = ex.filter(pl.col("value").is_not_null() & (pl.col("value") >= rule.min_value))[
            "stock_id"
        ].to_list()
        df = df.filter(~pl.col("stock_id").is_in(blocked))
        if df.is_empty():
            return []
    rows = sorted(
        ((str(r["stock_id"]), float(r["value"])) for r in df.to_dicts()),
        key=lambda item: (-item[1], item[0]),
    )
    return rows
