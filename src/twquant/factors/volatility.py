"""波動類因子（T-284，公式由 D-143 §十 事前寫死）。

**為什麼補這一類**：本專案 43 個因子裡波動只有 `price.vol_20d` 一支（close-to-close 的
20 日標準差），而 2026-09-22 的探索量到波動是所有單因子裡對「飆股」區辨力最強的一個
——前 10% 的飆股率 13.85%，是基礎率 5.88% 的 2.36 倍。一個維度最強、卻只有一個最粗糙
的估計量，是明顯的缺口。

八支分三組：

1. **更有效率的波動估計**（`atr_14`／`parkinson_20`／`garman_klass_20`）：
   `vol_20d` 只用收盤價，丟掉了當日高低的資訊。Parkinson 用 high/low，同樣天數下的
   估計效率約為 close-to-close 的 5 倍；Garman-Klass 再納入開盤。
2. **上下行拆開**（`upside_vol_20`／`downside_vol_20`／`vol_asym_20`）：
   **這是修 D-126／D-132 病根的一組**。`vol_20d` 不分方向，模型因此無法分辨
   「會動」與「會漲」——兩輪診斷都量到模型挑的是會大幅移動的股票、方向近乎擲銅板。
   拆成上行／下行之後，模型第一次有機會分辨。
3. **分布形狀**（`ret_skew_60`／`jump_count_60`）：右偏與跳躍頻率。文獻上偏度**負向**
   預測平均報酬（彩券型股票被高估），但這一輪問的是右尾——同一個量在兩個問題上
   方向相反，正是換標籤要檢驗的張力。

**有限窗優先**：除 `atr_14` 外全部用滾動窗。D-138 實測過 `ewm_mean` 的無限記憶會讓
「從哪天開始算」影響結果（裁切歷史後 RSI 與全史版差到 28.3），故 `atr_14` 沿用
該模組的處方——Wilder 遞迴平滑搭配 250 個交易日的 `lookback_days`，讓初始條件的殘留
低於浮點精度。**D-143 §十 表上 `atr_14` 的 lookback 寫 20，那是我寫登記時的疏漏**：
lookback 對 EWM 因子是正確性參數而非美化參數，20 天會讓逐日、裁切、整批三種算法得到
不同的值。此處採 250 並在任務完成註記說明，公式本身不變。

缺值一律回 null，不填補（黃金規則 5）：窗內不足、分母為零、GK 的變異數估計為負，
都不得用短窗或中性值充數。
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import polars as pl

from twquant.factors.registry import factor
from twquant.panel import safe_ratio

if TYPE_CHECKING:
    from twquant.data.warehouse import PitView

_TABLES = ["prices_daily"]
_LN2 = math.log(2.0)


def _int(params: dict[str, object], key: str, default: int) -> int:
    raw = params.get(key, default)
    return int(raw) if isinstance(raw, int | float | str) else default


def _panel(view: PitView, *cols: str) -> pl.LazyFrame:
    """價格面板，依 (stock_id, date) 排序——ewm 沒有 order_by，必須先排序再 over。"""
    return view.table("prices_daily").select("date", "stock_id", *cols).sort(["stock_id", "date"])


def _finish(lf: pl.LazyFrame) -> pl.DataFrame:
    return lf.select("date", "stock_id", "value").drop_nulls("value").collect()


def _daily_return() -> pl.Expr:
    """close(t)/close(t−1) − 1。分母為零回 null（safe_ratio），不得填 0。"""
    return safe_ratio(pl.col("close"), pl.col("close").shift(1).over("stock_id")) - 1.0


def _semi_variance(positive: bool, window: int) -> pl.Expr:
    """上行（或下行）半變異數：只取符合方向的日報酬平方，其餘記 0，缺值仍為 null。

    記 0 而不是排除，是因為「這 20 天裡只有 3 天上漲」本身就是資訊——
    把不符方向的日子排除會讓分母變動，量到的就不是同一件事。
    """
    r = pl.col("_r")
    signed = r > 0 if positive else r < 0
    contrib = (
        pl.when(r.is_null()).then(None).when(signed).then(r * r).otherwise(0.0).alias("_contrib")
    )
    return contrib.rolling_mean(window).over("stock_id").sqrt()


@factor(
    name="price.atr_14",
    category="price",
    direction=-1,  # 沿用 vol_20d 的低波動異象先驗；ML 特徵取原值，不受 direction 影響
    tables=_TABLES,
    lookback_days=250,  # Wilder 遞迴是無限記憶，見模組 docstring
    params={"window": 14},
    anchor_invariant=True,
)
def atr_14(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """Wilder ATR ÷ 收盤價。真實區間納入跳空，除以收盤價才能跨股比較。

    TR = max(H−L, |H−C₋₁|, |L−C₋₁|)；ATR = alpha=1/window 的指數平滑。
    """
    window = _int(params, "window", 14)
    prev_close = pl.col("close").shift(1).over("stock_id")
    tr = pl.max_horizontal(
        pl.col("high") - pl.col("low"),
        (pl.col("high") - prev_close).abs(),
        (pl.col("low") - prev_close).abs(),
    )
    # 遞迴平滑沒有「窗滿」的概念，上市第一天也算得出一個值——那是用一天的資料充數。
    # 補一個列位置閘門：累積不足 window 筆真實區間就回 null（黃金規則 5）。
    seasoned = pl.int_range(pl.len()).over("stock_id") >= window
    return _finish(
        _panel(view, "high", "low", "close")
        .with_columns(_tr=tr)
        .with_columns(
            _atr=pl.col("_tr").ewm_mean(alpha=1.0 / window, ignore_nulls=True).over("stock_id")
        )
        .with_columns(
            value=pl.when(seasoned)
            .then(safe_ratio(pl.col("_atr"), pl.col("close")))
            .otherwise(None)
        )
    )


@factor(
    name="price.parkinson_20",
    category="price",
    direction=-1,
    tables=_TABLES,
    lookback_days=25,
    params={"window": 20},
    anchor_invariant=True,
)
def parkinson_20(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """Parkinson (1980) 波動率：√( Σ ln(H/L)² / (4·ln2·n) )。只用高低，效率高於收盤法。"""
    window = _int(params, "window", 20)
    ln_hl = safe_ratio(pl.col("high"), pl.col("low")).log()
    return _finish(
        _panel(view, "high", "low")
        .with_columns(_x=ln_hl * ln_hl)
        .with_columns(
            value=(pl.col("_x").rolling_sum(window).over("stock_id") / (4.0 * _LN2 * window)).sqrt()
        )
    )


@factor(
    name="price.garman_klass_20",
    category="price",
    direction=-1,
    tables=_TABLES,
    lookback_days=25,
    params={"window": 20},
    anchor_invariant=True,
)
def garman_klass_20(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """Garman-Klass (1980) 波動率：0.5·ln(H/L)² − (2ln2−1)·ln(C/O)² 的 n 日均值開根號。

    估計量在單日可能為負（開收差距大於高低區間所能解釋的部分），此時回 null——
    負變異數不是「波動為零」，是這個估計量在該窗不適用。
    """
    window = _int(params, "window", 20)
    ln_hl = safe_ratio(pl.col("high"), pl.col("low")).log()
    ln_co = safe_ratio(pl.col("close"), pl.col("open")).log()
    var = 0.5 * ln_hl * ln_hl - (2.0 * _LN2 - 1.0) * ln_co * ln_co
    return _finish(
        _panel(view, "open", "high", "low", "close")
        .with_columns(_v=var)
        .with_columns(_var=pl.col("_v").rolling_mean(window).over("stock_id"))
        .with_columns(
            value=pl.when(pl.col("_var") >= 0.0).then(pl.col("_var").sqrt()).otherwise(None)
        )
    )


@factor(
    name="price.upside_vol_20",
    category="price",
    direction=1,
    tables=_TABLES,
    lookback_days=25,
    params={"window": 20},
    anchor_invariant=True,
)
def upside_vol_20(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """上行波動：√( Σ_{r>0} r² / n )。只算上漲日的振幅。"""
    window = _int(params, "window", 20)
    return _finish(
        _panel(view, "close")
        .with_columns(_r=_daily_return())
        .with_columns(value=_semi_variance(positive=True, window=window))
    )


@factor(
    name="price.downside_vol_20",
    category="price",
    direction=-1,
    tables=_TABLES,
    lookback_days=25,
    params={"window": 20},
    anchor_invariant=True,
)
def downside_vol_20(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """下行波動：√( Σ_{r<0} r² / n )。即 Sortino 比率的分母。"""
    window = _int(params, "window", 20)
    return _finish(
        _panel(view, "close")
        .with_columns(_r=_daily_return())
        .with_columns(value=_semi_variance(positive=False, window=window))
    )


@factor(
    name="price.vol_asym_20",
    category="price",
    direction=1,
    tables=_TABLES,
    lookback_days=25,
    params={"window": 20},
    anchor_invariant=True,
)
def vol_asym_20(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """波動的不對稱性：ln(上行波動 / 下行波動)。> 0 表示這檔股票的動作偏向上方。

    **這一批的核心。** `vol_20d` 把上下行混在一起，模型只能學到「會動」；
    這一支把方向分出來，是 D-126／D-132 兩度量到的「會動不會漲」的直接對策。
    任一邊為零（20 天內全漲或全跌）→ null，不得填 0：那是無限大，不是「對稱」。
    """
    window = _int(params, "window", 20)
    return _finish(
        _panel(view, "close")
        .with_columns(_r=_daily_return())
        .with_columns(
            _up=_semi_variance(positive=True, window=window),
            _dn=_semi_variance(positive=False, window=window),
        )
        .with_columns(_ratio=safe_ratio(pl.col("_up"), pl.col("_dn")))
        .with_columns(
            value=pl.when(pl.col("_ratio") > 0.0).then(pl.col("_ratio").log()).otherwise(None)
        )
    )


@factor(
    name="price.ret_skew_60",
    category="price",
    direction=-1,  # 文獻先驗：彩券型股票被高估，偏度負向預測「平均」報酬
    tables=_TABLES,
    lookback_days=70,
    params={"window": 60},
    anchor_invariant=True,
)
def ret_skew_60(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """60 日日報酬的樣本偏度。右偏＝少數幾天大漲撐起分布，正是「飆」的形狀。"""
    window = _int(params, "window", 60)
    return _finish(
        _panel(view, "close")
        .with_columns(_r=_daily_return())
        .with_columns(value=pl.col("_r").rolling_skew(window).over("stock_id"))
    )


@factor(
    name="price.jump_count_60",
    category="price",
    direction=1,
    tables=_TABLES,
    lookback_days=85,  # 60 日計數窗 + 20 日波動窗 + 餘裕
    params={"window": 60, "vol_window": 20, "k": 3.0},
    anchor_invariant=True,
)
def jump_count_60(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """跳躍頻率：過去 window 日內 |r| > k × σ(t−1) 的比例。

    門檻用**前一日**的滾動波動，否則跳躍那天會自己撐大自己的門檻、把自己藏起來。
    """
    window = _int(params, "window", 60)
    vol_window = _int(params, "vol_window", 20)
    raw_k = params.get("k", 3.0)
    k = float(raw_k) if isinstance(raw_k, int | float | str) else 3.0
    r = pl.col("_r")
    sigma_prev = r.rolling_std(vol_window).over("stock_id").shift(1).over("stock_id")
    jump = (
        pl.when(r.is_null() | sigma_prev.is_null())
        .then(None)
        .when(r.abs() > k * sigma_prev)
        .then(1.0)
        .otherwise(0.0)
    )
    return _finish(
        _panel(view, "close")
        .with_columns(_r=_daily_return())
        .with_columns(_jump=jump)
        .with_columns(value=pl.col("_jump").rolling_mean(window).over("stock_id"))
    )
