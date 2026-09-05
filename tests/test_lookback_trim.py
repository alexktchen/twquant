"""T-083：因子計算依 `lookback_days` 裁切歷史（物化的 O(n²) 根因）。

`@factor` 宣告了 `lookback_days`，但 `compute.py` **從不讀它**——每個因子每天都重讀
六年全史。實測全因子物化約 100 秒/日 × 1,622 日 ≈ **45 小時**，讓任何一次重物化
都變成不可行的工程。

裁切的風險是**靜默算錯**：裁太少 → 因子在窗首得到較短的歷史 → 值不同，而且不會
有任何錯誤。故本檔的核心不是「裁切能加速」，而是**「裁切後的結果與未裁切相同」**——這道等價性斷言
把優化從「相信」變成「證明」。

注意用的是**相對容差 1e-9 而非逐位元相等**：`rolling_std` 在較長與較短的序列上累加順序
不同，實測 `price.vol_20d` 差在 1e-16 的相對量級，那是浮點結合律不是邏輯錯誤。
容差是否夠緊由 `test_ac1_deliberately_short_trim_is_caught` 證明——故意裁太短時必須被抓到。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

import twquant.factors.builtins  # noqa: F401
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.factors.compute import compute
from twquant.factors.registry import get


def _wh(tmp_path: Path, n_days: int = 400) -> tuple[Warehouse, list[dt.date]]:
    """單調上升的合成行情，長到足以讓 60 日窗與 52 週高都有值。"""
    base = dt.date(2024, 1, 1)
    days = [base + dt.timedelta(days=i) for i in range(n_days)]
    rows = []
    for i, d in enumerate(days):
        for sid, k in (("1101", 1.0), ("2330", 2.0), ("2454", 0.5)):
            c = 100.0 + k * i
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": c,
                    "high": c,
                    "low": c,
                    "close": c,
                    "volume": 1000,
                    "amount": c * 1000,
                    "limit_up_price": None,
                    "limit_down_price": None,
                    "available_at": dt.datetime(d.year, d.month, d.day, 18, tzinfo=TZ),
                }
            )
    df = pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(schemas.TW_DT),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", df, source="twse")
    return wh, days


_PRICE_FACTORS = [
    "price.mom_1d",
    "price.mom_20d",
    "price.mom_60d",
    "price.reversal_5d",
    "price.vol_20d",
    "price.high_52w",
]


# 逐位元相等對**滾動聚合**不可能成立：`rolling_std` 在較長與較短的序列上累加順序不同，
# 實測 `price.vol_20d` 差在 1e-16 的相對量級。那是浮點結合律，不是邏輯錯誤。
# 用 1e-9 的相對容差——真正的窗口錯誤（少讀了歷史）會差好幾個數量級，
# 由 `test_ac1_deliberately_short_trim_is_caught` 證明這個容差確實夠緊。
_REL_TOL = 1e-9


def _max_rel_diff(a: pl.DataFrame, b: pl.DataFrame) -> float:
    j = a.join(b, on=["date", "stock_id", "factor"], how="inner", suffix="_b")
    assert j.height == a.height == b.height, "兩邊的鍵不一致，先前的比較會漏列"
    d = j.select(
        (
            (pl.col("value") - pl.col("value_b")).abs()
            / pl.max_horizontal(pl.col("value").abs(), pl.lit(1e-12))
        ).alias("rel")
    )
    mx = d["rel"].max()
    return float(mx) if isinstance(mx, int | float) else 0.0


@pytest.mark.parametrize("name", _PRICE_FACTORS)
def test_ac1_trimmed_equals_untrimmed(tmp_path: Path, name: str) -> None:
    """**這是本次優化唯一重要的斷言**：裁切後的值與未裁切相同。

    裁太少會讓因子在窗首拿到較短的歷史，算出不同的值——而且不會有任何錯誤訊息。
    效能是次要的；正確性由這條保證。
    """
    wh, days = _wh(tmp_path)
    d = days[-1]
    full = compute([name], wh.view(d), dates=[d]).sort("stock_id")
    trimmed = compute([name], wh.view(d), dates=[d], trim_lookback=True).sort("stock_id")
    assert trimmed["value"].null_count() == full["value"].null_count(), f"{name} 的 null 數改變"
    rel = _max_rel_diff(full, trimmed)
    assert rel < _REL_TOL, f"{name} 裁切後結果改變：最大相對差 {rel:.3e}"


def test_ac1_deliberately_short_trim_is_caught(tmp_path: Path) -> None:
    """**證明容差夠緊**：故意只給 3 天回看，60 日動能必須明顯不同或整個消失。

    少了這條，上面的容差可能寬到放行真正的窗口錯誤——那樣整組測試就是裝飾品。
    """
    from twquant.factors.compute import _TrimmedView

    wh, days = _wh(tmp_path)
    d = days[-1]
    full = compute(["price.mom_60d"], wh.view(d), dates=[d]).sort("stock_id")
    short_view = _TrimmedView(wh.view(d), d - dt.timedelta(days=3))
    starved = compute(["price.mom_60d"], short_view, dates=[d]).sort("stock_id")  # type: ignore[arg-type]
    if starved.height == 0:
        return  # 歷史不足直接無值，同樣是「被抓到」
    rel = _max_rel_diff(full, starved)
    assert rel > _REL_TOL * 1000, f"裁太短卻只差 {rel:.3e}——容差過寬，測試無效"


def test_ac1_trimmed_equals_untrimmed_for_all_registered_price_factors(tmp_path: Path) -> None:
    """一次算全部：因子之間若共用中間結果，逐一比對可能漏掉交互作用。"""
    wh, days = _wh(tmp_path)
    d = days[-1]
    full = compute(_PRICE_FACTORS, wh.view(d), dates=[d]).sort(["factor", "stock_id"])
    trimmed = compute(_PRICE_FACTORS, wh.view(d), dates=[d], trim_lookback=True).sort(
        ["factor", "stock_id"]
    )
    assert trimmed["value"].null_count() == full["value"].null_count()
    rel = _max_rel_diff(full, trimmed)
    assert rel < _REL_TOL, f"合併計算時結果改變：最大相對差 {rel:.3e}"


def test_ac2_trim_actually_reduces_the_rows_read(tmp_path: Path) -> None:
    """反向確認：若裁切根本沒生效，上面的等價測試會恆真而毫無意義。

    `price.mom_1d` 的 `lookback_days` 很小，裁切後讀到的日期數必須遠少於全史。
    """
    from twquant.factors.compute import _trimmed_view

    wh, days = _wh(tmp_path)
    d = days[-1]
    view = wh.view(d)
    n_full = view.table("prices_daily").select("date").unique().collect().height
    trimmed_view = _trimmed_view(view, get("price.mom_1d").lookback_days)
    n_trim = trimmed_view.table("prices_daily").select("date").unique().collect().height
    assert n_full == 400
    assert n_trim < n_full / 4, f"裁切未生效：全史 {n_full} 日，裁切後仍有 {n_trim} 日"


def test_ac3_default_is_off_so_existing_callers_are_unchanged(tmp_path: Path) -> None:
    """預設不裁切：既有呼叫端（回測、策略、IC 報告）的行為逐位元不變。"""
    wh, days = _wh(tmp_path)
    d = days[-1]
    assert compute(["price.mom_20d"], wh.view(d), dates=[d]).equals(
        compute(["price.mom_20d"], wh.view(d), dates=[d], trim_lookback=False)
    )


def test_ac3_multi_date_request_widens_the_window(tmp_path: Path) -> None:
    """要求多個日期時，裁切窗須涵蓋**最早**那個日期的回看需求，否則早期日子會算錯。"""
    wh, days = _wh(tmp_path)
    want = [days[-100], days[-1]]
    full = compute(["price.mom_20d"], wh.view(days[-1]), dates=want).sort(["date", "stock_id"])
    trimmed = compute(["price.mom_20d"], wh.view(days[-1]), dates=want, trim_lookback=True).sort(
        ["date", "stock_id"]
    )
    rel = _max_rel_diff(full, trimmed)
    assert rel < _REL_TOL, f"多日請求時早期日子算錯：最大相對差 {rel:.3e}"
    assert trimmed["date"].n_unique() == 2
