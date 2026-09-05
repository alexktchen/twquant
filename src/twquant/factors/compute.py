"""因子計算引擎（SPEC-002 §2）。

compute 對一組因子名，用給定的 PitView 計算，回傳 long format
(date, stock_id, factor, value)。因子函數負責只看回看窗內的資料（反前視由
assert_no_lookahead 驗證）。
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, cast

import polars as pl

from twquant.errors import TwquantError
from twquant.factors.registry import get

if TYPE_CHECKING:
    from twquant.data.warehouse import PitView

_OUT_COLS = ["date", "stock_id", "factor", "value"]


def _as_df(result: object, name: str) -> pl.DataFrame:
    if isinstance(result, pl.LazyFrame):
        result = result.collect()
    if not isinstance(result, pl.DataFrame):
        raise TwquantError(f"因子 {name} 回傳非 DataFrame：{type(result)}")
    missing = {"date", "stock_id", "value"} - set(result.columns)
    if missing:
        raise TwquantError(f"因子 {name} 回傳缺欄位：{missing}")
    return result


# T-083：物化是 O(n²) 的根因——`@factor` 宣告了 `lookback_days`，但這裡從不讀它，
# 於是每個因子每天都重讀六年全史。實測全因子物化約 100 秒/日 × 1,622 日 ≈ 45 小時。
#
# 裁切的風險是**靜默算錯**：裁太少 → 因子在窗首拿到較短的歷史 → 值不同，而且不會
# 有任何錯誤。故 `tests/test_lookback_trim.py` 的核心不是「能加速」，而是
# **「裁切後與未裁切逐位元相同」**——把這個優化從「相信」變成「證明」。
#
# 安全係數 2 是把交易日換算回曆日：一年約 244 個交易日、365 個曆日，比值 1.5；
# 取 2 再加 30 天緩衝，寬到連續兩週休市也不會傷到窗首。
_TRIM_SAFETY = 2
_TRIM_BUFFER_DAYS = 30


class _TrimmedView:
    """把 `PitView` 的下界也夾住，讓因子只讀回看窗內的歷史（T-083）。

    只包一層 `table()`；`cutoff` 與其餘語意原樣轉發，故 PIT 上界完全不變——
    這個優化只動下界，不可能引入前視。
    """

    def __init__(self, inner: PitView, floor: dt.date) -> None:
        self._inner = inner
        self._floor = floor

    @property
    def cutoff(self) -> dt.datetime:
        return self._inner.cutoff

    def table(self, name: str) -> pl.LazyFrame:
        lf = self._inner.table(name)
        if "date" in lf.collect_schema().names():
            lf = lf.filter(pl.col("date") >= self._floor)
        return lf


def _trimmed_view(view: PitView, lookback_days: int, earliest: dt.date | None = None) -> PitView:
    """回傳只含 `lookback_days`（換算曆日並加緩衝）內歷史的視圖。

    `earliest` 是本次要求的最早輸出日；裁切窗必須涵蓋**它**的回看需求，
    否則多日請求的早期日子會算錯（測試 `test_ac3_multi_date_request_widens_the_window`）。
    """
    anchor = earliest or view.cutoff.date()
    span = lookback_days * _TRIM_SAFETY + _TRIM_BUFFER_DAYS
    # 用 cast 而非繼承 `PitView`：繼承要碰它的私有 `_source`，而我們只需要包一層
    # `table()`。結構上完全相容（`cutoff` 與 `table` 是因子唯一會用到的介面）。
    return cast("PitView", _TrimmedView(view, anchor - dt.timedelta(days=span)))


def compute(
    names: list[str],
    view: PitView,
    dates: list[dt.date] | None = None,
    params: dict[str, dict[str, object]] | None = None,
    trim_lookback: bool = False,
) -> pl.DataFrame:
    """計算因子；params 依因子名覆寫 registry 預設，不修改註冊內容。

    `trim_lookback=True` 時依各因子宣告的 `lookback_days` 裁切歷史下界（T-083）。
    **預設 False**：既有呼叫端（回測、策略、IC 報告）的結果逐位元不變；
    只有物化這種「逐日重跑上千次」的路徑才需要它。

    裁切只動**下界**，PIT 上界（`available_at <= cutoff`）完全不變，故不可能引入前視。
    正確性由 `tests/test_lookback_trim.py` 的等價性斷言保證。
    """
    frames: list[pl.DataFrame] = []
    overrides = params or {}
    earliest = min(dates) if dates else None
    for name in names:
        fdef = get(name)
        if fdef.fn is None:
            raise TwquantError(f"因子 {name} 無實作函數")
        effective = dict(fdef.params)
        effective.update(overrides.get(name, {}))
        # 視窗參數若被 params 覆寫（如 vol_20d 傳 window=60），回看需求會大於註冊值，
        # 故取兩者較大者——用註冊值會裁太少而靜默算錯。
        win = effective.get("window")
        lookback = fdef.lookback_days
        if isinstance(win, int | float) and int(win) > lookback:
            lookback = int(win)
        fview = _trimmed_view(view, lookback, earliest) if trim_lookback else view
        df = _as_df(fdef.fn(fview, effective), name)
        df = df.with_columns(pl.lit(name).alias("factor")).select(_OUT_COLS)
        frames.append(df)

    if not frames:
        return pl.DataFrame(
            schema={
                "date": pl.Date,
                "stock_id": pl.String,
                "factor": pl.String,
                "value": pl.Float64,
            }
        )
    out = pl.concat(frames)
    if dates is not None:
        out = out.filter(pl.col("date").is_in(dates))
    return out.sort(["factor", "date", "stock_id"])
