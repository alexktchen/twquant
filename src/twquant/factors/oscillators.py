"""震盪指標因子（D-138）：KD、RSI、布林、MACD。

本專案原本 18 個價量因子裡**一個震盪指標都沒有**——有型態辨識（箱型、雙底、頭肩頂）、
有動能與波動度，但模型從未看過 KD／RSI／布林／MACD。本模組補上這個缺口。

方向（`direction`）不照教科書，照量到的結果。在日均成交 ≥ 3 億、股價 > 10 的宇宙裡，
以「ML 前 10%、持有 60 個交易日、扣成本後每筆期望值 +8.11%」為基準，各條件當進場濾網：

| 條件 | 每筆期望值 | 與基準差 |
|---|---:|---:|
| KD 的 K > 80（超買） | +11.27% | **+3.16pp** |
| RSI > 70（超買） | +10.97% | **+2.86pp** |
| 站上布林上軌（%B > 1） | +10.18% | **+2.07pp** |
| MACD 柱狀為正 | +8.86% | +0.75pp |
| 站上 MA20（對照） | +8.58% | +0.47pp |
| KD 的 K < 20（超賣） | +7.98% | −0.13pp |
| 布林帶寬收縮 | +6.88% | −1.23pp |
| RSI < 30（超賣） | +5.62% | **−2.50pp** |

**所以「超買要賣、超賣要買」在這個宇宙與持有期是反的**：超買 +2.9pp、超賣 −2.5pp。
一致的解釋是排序模型挑的本來就是高波動標的（前 3% 的年化波動中位 65.1%），
超買代表動能正在發生，超賣代表還在跌。故 K／RSI／%B 一律 `direction=+1`。

**黃金交叉不做成因子**：KD 黃金交叉 +0.07pp、MACD 黃金交叉 −0.84pp，兩者都無效；
而「K 在 D 之上」（狀態）也只有 −0.12pp。有效的是**強度**（K 多高）不是**轉折**，
故本模組只提供連續值，不提供交叉的布林旗標。

上表是**探索**（一次測 18 個條件），不是驗證過的結論——方向的選擇有事後成分，
真正的驗證要靠模型在樣本外的表現。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from twquant.factors.registry import factor
from twquant.panel import safe_ratio

if TYPE_CHECKING:
    from twquant.data.warehouse import PitView

_TABLES = ["prices_daily"]


def _int(params: dict[str, object], key: str, default: int) -> int:
    raw = params.get(key, default)
    return int(raw) if isinstance(raw, int | float | str) else default


def _panel(view: PitView, *cols: str) -> pl.LazyFrame:
    """價格面板，依 (stock_id, date) 排序——ewm 沒有 order_by，必須先排序再 over。"""
    return view.table("prices_daily").select("date", "stock_id", *cols).sort(["stock_id", "date"])


def _finish(lf: pl.LazyFrame) -> pl.DataFrame:
    return lf.select("date", "stock_id", "value").drop_nulls("value").collect()


@factor(
    name="price.kd_k_9",
    category="price",
    direction=1,  # 量到的是超買較好（+3.16pp），與教科書相反，見模組 docstring
    tables=_TABLES,
    lookback_days=40,
    params={"window": 9, "smooth": 3},
    anchor_invariant=True,
)
def kd_k_9(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """KD 指標的 K 值（0~100）。RSV 取 window 日高低區間內的收盤位置，再以 smooth 平滑兩次。

    台股慣用 (9, 3, 3)。`smooth=3` 對應 alpha=1/3 的指數平滑，即傳統
    「K = 2/3 × 前日K + 1/3 × RSV」。
    """
    window, smooth = _int(params, "window", 9), _int(params, "smooth", 3)
    lo = pl.col("low").rolling_min(window).over("stock_id")
    hi = pl.col("high").rolling_max(window).over("stock_id")
    # 高低相等（整段漲停鎖死）時分母為零——safe_ratio 回傳 null，不得當成 50 填補
    rsv = safe_ratio(pl.col("close") - lo, hi - lo) * 100.0
    return _finish(
        _panel(view, "high", "low", "close")
        .with_columns(_rsv=rsv)
        .with_columns(value=pl.col("_rsv").ewm_mean(alpha=1.0 / smooth).over("stock_id"))
    )


@factor(
    name="price.rsi_14",
    category="price",
    direction=1,  # 超買 +2.86pp、超賣 −2.50pp
    tables=_TABLES,
    lookback_days=45,
    params={"window": 14},
    anchor_invariant=True,
)
def rsi_14(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """RSI（0~100）：Wilder 平滑的漲跌幅比值。全程無漲跌（停牌沿用）時分母為零 → null。"""
    window = _int(params, "window", 14)
    d = pl.col("close") - pl.col("close").shift(1).over("stock_id")
    up = pl.when(d > 0).then(d).otherwise(0.0).ewm_mean(alpha=1.0 / window).over("stock_id")
    dn = pl.when(d < 0).then(-d).otherwise(0.0).ewm_mean(alpha=1.0 / window).over("stock_id")
    return _finish(
        _panel(view, "close")
        .with_columns(_u=up, _d=dn)
        .with_columns(value=safe_ratio(pl.col("_u"), pl.col("_u") + pl.col("_d")) * 100.0)
    )


@factor(
    name="price.bollinger_pctb_20",
    category="price",
    direction=1,  # 站上上軌 +2.07pp
    tables=_TABLES,
    lookback_days=30,
    params={"window": 20, "n_std": 2.0},
    anchor_invariant=True,
)
def bollinger_pctb_20(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """布林通道 %B：0＝下軌、1＝上軌，可超出 [0,1]。標準差為零（連續同價）→ null。"""
    window = _int(params, "window", 20)
    raw = params.get("n_std", 2.0)
    n_std = float(raw) if isinstance(raw, int | float | str) else 2.0
    ma = pl.col("close").rolling_mean(window).over("stock_id")
    sd = pl.col("close").rolling_std(window).over("stock_id")
    return _finish(
        _panel(view, "close").with_columns(
            value=safe_ratio(pl.col("close") - (ma - n_std * sd), 2.0 * n_std * sd)
        )
    )


@factor(
    name="price.bollinger_width_20",
    category="price",
    direction=1,  # 帶寬收縮 −1.23pp，故「寬」較好
    tables=_TABLES,
    lookback_days=30,
    params={"window": 20, "n_std": 2.0},
    anchor_invariant=True,
)
def bollinger_width_20(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """布林帶寬：(上軌 − 下軌) ÷ 中軌。衡量盤整或擴張，不看方向。"""
    window = _int(params, "window", 20)
    raw = params.get("n_std", 2.0)
    n_std = float(raw) if isinstance(raw, int | float | str) else 2.0
    ma = pl.col("close").rolling_mean(window).over("stock_id")
    sd = pl.col("close").rolling_std(window).over("stock_id")
    return _finish(_panel(view, "close").with_columns(value=safe_ratio(2.0 * n_std * sd, ma)))


@factor(
    name="price.macd_hist_norm",
    category="price",
    direction=1,  # 柱狀為正 +0.75pp
    tables=_TABLES,
    lookback_days=60,
    params={"fast": 12, "slow": 26, "signal": 9},
    anchor_invariant=True,
)
def macd_hist_norm(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """MACD 柱狀體 ÷ 收盤價。

    **除以收盤價是必要的**：MACD 是價差，百元股與十元股的柱狀體差一個量級，
    不正規化就無法做橫斷面比較——而本專案所有因子都是橫斷面排序後使用。
    """
    fast, slow = _int(params, "fast", 12), _int(params, "slow", 26)
    signal = _int(params, "signal", 9)
    ef = pl.col("close").ewm_mean(span=fast).over("stock_id")
    es = pl.col("close").ewm_mean(span=slow).over("stock_id")
    return _finish(
        _panel(view, "close")
        .with_columns(_dif=ef - es)
        .with_columns(_dea=pl.col("_dif").ewm_mean(span=signal).over("stock_id"))
        .with_columns(value=safe_ratio(pl.col("_dif") - pl.col("_dea"), pl.col("close")))
    )
