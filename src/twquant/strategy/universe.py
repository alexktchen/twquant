"""宇宙過濾（SPEC-003 §2）。

以 PIT 視圖為基礎，回傳 signal_date 當日合格的股票集合。過濾在因子計算「之前」執行
（避免處置股污染橫斷面標準化）。

第一版可實作：20 日成交金額中位數（ADV，中位數抗漲停無量）、最低價、四碼普通股
（排除權證/ETF）。**待接資料**：處置/全額交割（disposition_stocks）、上市日
（stock_meta.list_date）、市場別（stock_meta.market）——相關旗標暫為 no-op 並在
STRATEGY/TASKS 標註，接上後於此啟用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    import datetime as dt

    from twquant.data.warehouse import PitView
    from twquant.strategy.config import UniverseConfig

_COMMON_STOCK = r"^[1-9][0-9]{3}$"  # 四碼普通股，排除 00xx ETF 與 5-6 碼權證


def universe_ids(view: PitView, signal_date: dt.date, cfg: UniverseConfig) -> list[str]:
    """回傳 signal_date 合格股票代號（已套用可實作的過濾條件）。"""
    lf = (
        view.table("prices_daily")
        .select("date", "stock_id", "close", "amount")
        .filter(pl.col("date") <= signal_date)
        .sort(["stock_id", "date"])
    )
    # 20 日成交金額中位數（截至 signal_date）；不足 20 日 → null → 淘汰（流動性歷史不足）
    lf = lf.with_columns(_adv=pl.col("amount").rolling_median(20).over("stock_id"))
    at = lf.filter(pl.col("date") == signal_date)

    at = at.filter(pl.col("stock_id").str.contains(_COMMON_STOCK))
    if cfg.min_adv_twd is not None:
        at = at.filter(pl.col("_adv") >= cfg.min_adv_twd)
    if cfg.min_price is not None:
        at = at.filter(pl.col("close") >= cfg.min_price)
    return sorted(at.select("stock_id").collect()["stock_id"].to_list())
