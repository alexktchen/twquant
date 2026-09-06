"""面板語意的共用純函數：缺口感知的列位移與零分母防護（T-139／T-140）。

放在 `twquant` 根層而非 `factors/` 或 `ml/`：兩邊都要用，而讓 `ml` 依賴 `factors`
只為了一個位移工具並不合理。本模組無 IO、無狀態（黃金規則 10）。

**為什麼需要它。** polars 的 `shift(n)` 是**列位移**，不是曆日位移。某檔中間缺 N 個
開市日時，`close.shift(20)` 撈到的是 20 **列**之前而非 20 **個交易日**之前的價格。
實測 `prices_daily` 有 413 處 >5 開市日的個股缺口（最大 260 日），於是：

- **標籤**：h=20 下約 8,673 個訓練樣本的「20 日報酬」實際橫跨數月，而且不帶任何標記
  ——它只是一個看起來很大的數字，會被模型當成真實的極端報酬學進去。
- **特徵**：`factor_values` 的 22,988 列 `inf` 就是這樣來的。5547 在 2026-03 只有 6 列，
  `shift(20)` 往回落到 **2021-02** 一列 `close=0` 的無效資料上 → 除以零 → `inf`。
  而 `inf` **不是** null，`drop_nulls`／`is_not_null` 都攔不住它。

兩件事同一個根，故一起修：**位移要驗跨距，除法要驗分母。**
"""

from __future__ import annotations

import polars as pl

_DATE = "date"
_STOCK = "stock_id"
_SIDX = "_sidx"


def session_index(frame: pl.DataFrame) -> pl.DataFrame:
    """加一欄 `_sidx`：該列的日期在**全市場日曆**中的稠密序號（0 起）。

    序號取自 frame 裡出現過的所有日期，**不是個股自己的列**——個股缺列正是要偵測的
    東西，用個股自己的序號會讓缺口消失。與 `strategy/universe.py` 的衛生濾網同源：
    市場日曆＝`prices_daily` 的日期聯集，故農曆年封關（全市場休市）不漏任何開市日。
    """
    for c in (_DATE, _STOCK):
        if c not in frame.columns:
            raise ValueError(f"缺少欄位：{c}（session_index 需要 date 與 stock_id）")
    cal = frame.select(_DATE).unique().sort(_DATE).with_row_index(_SIDX)
    return frame.join(cal, on=_DATE, how="left")


def shift_within_span(expr: pl.Expr, n: int, *, max_span: int) -> pl.Expr:
    """`expr.shift(n)`，但**跨距超過 `max_span` 個開市日時回 null**。

    需要 frame 已有 `session_index` 產生的 `_sidx` 欄。`max_span` 給的是開市日跨距，
    故連假不受影響（全市場休市不佔開市日），真正的停牌與資料缺段才會被攔下。

    `max_span` 必須 ≥ `n`：往回數 n 列**至少**跨 n 個開市日，設得比 n 小會讓所有值
    都變 null——那不是防護，是把因子關掉。
    """
    if max_span < n:
        raise ValueError(f"max_span({max_span}) 不得小於 n({n})：往回 n 列至少跨 n 個開市日")
    prev = expr.shift(n).over(_STOCK, order_by=_DATE)
    span = pl.col(_SIDX).cast(pl.Int64) - pl.col(_SIDX).cast(pl.Int64).shift(n).over(
        _STOCK, order_by=_DATE
    )
    return pl.when(span <= max_span).then(prev).otherwise(None)


def safe_ratio(num: pl.Expr, den: pl.Expr) -> pl.Expr:
    """`num / den`，分母 ≤ 0 或缺值時回 **null 而非 inf**。

    價格與市值的分母為 0 或負都沒有意義；讓它算出 `inf` 只會把錯誤往下游推，而且
    `inf` 通過每一道 null 檢查。標籤若含 `inf`，`mean().over("date")` 會把它擴散到
    當日**整個橫斷面**——一列壞資料毀掉一整天的樣本。

    輸出的不變量：每個值不是 null 就是有限。這是可以斷言的，而先前沒有人斷言。

    **只防分母不夠**：`1e300 / 1e-300` 的分母是正的，結果仍溢位成 `inf`。
    故連結果本身一起驗——memory 的建議「guard the denominator, **then assert the
    output is finite**」兩句話都要做，先前只做了前半句。
    """
    r = num / den
    return pl.when(den.is_not_null() & (den > 0.0) & r.is_finite()).then(r).otherwise(None)
