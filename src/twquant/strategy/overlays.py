"""Overlay 掛點（SPEC-003 §4）。

Overlay 在 selection 之後、輸出之前依序套用，調整整體曝險（縮放 target_weight）。
註冊函數簽名：(signal_date, weights, view, params) -> weights。

第一版：breadth_regime_filter（市場寬度 = 站上 N 日均線個股比例，可由 prices 計算）。
index_trend_filter 需加權指數序列（尚未接），待補。完整三態 regime 狀態機（含跨期
遲滯狀態）屬投組層（SPEC-005 / T-020）；此處為單日無狀態版：低於下閾值→exposure_floor、
高於上閾值→1.0、之間線性內插。
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable
from typing import TYPE_CHECKING

import polars as pl

from twquant.errors import ConfigError, DataError

if TYPE_CHECKING:
    from twquant.data.warehouse import PitView

OverlayFn = Callable[[dt.date, pl.DataFrame, "PitView", dict[str, object]], pl.DataFrame]

_OVERLAYS: dict[str, OverlayFn] = {}


def overlay(name: str) -> Callable[[OverlayFn], OverlayFn]:
    def deco(fn: OverlayFn) -> OverlayFn:
        _OVERLAYS[name] = fn
        return fn

    return deco


def get_overlay(name: str) -> OverlayFn:
    try:
        return _OVERLAYS[name]
    except KeyError as exc:
        raise ValueError(f"未註冊 overlay：{name}") from exc


def _scale(weights: pl.DataFrame, mult: float) -> pl.DataFrame:
    return weights.with_columns((pl.col("target_weight") * mult).alias("target_weight"))


def _as_float(value: object, default: float) -> float:
    return float(value) if isinstance(value, int | float | str) else default


def _as_int(value: object, default: int) -> int:
    return int(value) if isinstance(value, int | float | str) else default


def market_breadth(view: PitView, signal_date: dt.date, ma_window: int) -> float:
    """站上 ma_window 日均線的（四碼普通股）比例，作為市場寬度。無資料回 1.0（不過濾）。"""
    lf = (
        view.table("prices_daily")
        .select("date", "stock_id", "close")
        .filter(
            (pl.col("date") <= signal_date) & pl.col("stock_id").str.contains(r"^[1-9][0-9]{3}$")
        )
        .sort(["stock_id", "date"])
        .with_columns(_ma=pl.col("close").rolling_mean(ma_window).over("stock_id"))
        .filter(pl.col("date") == signal_date)
        .drop_nulls("_ma")
    )
    df = lf.select((pl.col("close") > pl.col("_ma")).mean().alias("b")).collect()
    if df.height == 0 or df["b"][0] is None:
        return 1.0
    return float(df["b"][0])


_TRADING_DAYS_PER_YEAR = 252  # 年化基數；與 backtest/metrics.py 一致，非魔術數字

# ── 五條擇時／風控規則的共用估計基礎（D-092）──────────────────────────
#
# 五條規則各自出自不同文獻，但都要用到「指數的歷史」或「組合的已實現波動」。
# **共用同一個估計函數是刻意的**：否則規則之間的差異會混進估計方法的差異，
# 而這批實驗要問的正是「哪一條規則有效」，不是「哪一種估法有效」。


def _index_closes(
    view: PitView, signal_date: dt.date, index_id: str, window: int, what: str
) -> pl.Series:
    """指數在 signal_date **之前** window 個交易日的收盤（不含當日）。

    不含當日：訊號在 t 日產生、t+1 開盤執行，t 日收盤在訊號時刻尚未發生。
    `index_daily` 不見得有 available_at 擋得住（截點設在收盤後就放行了），故自己擋。
    歷史不足直接 raise——靜默放行會讓 overlay 在回測最前段「成功地什麼都沒做」。
    """
    hist = (
        view.table("index_daily")
        .filter((pl.col("index_id") == index_id) & (pl.col("date") < signal_date))
        .select("date", "close")
        .sort("date")
        .tail(window)
        .collect()
    )
    if hist.height < window:
        raise DataError(
            "index_daily",
            signal_date,
            f"{index_id} 在 {signal_date} 之前只有 {hist.height} 個交易日，"
            f"不足 {window} 日的{what}；請把資料起點往前推（不得靜默放行）。",
        )
    return hist["close"]


def _realized_vol(view: PitView, signal_date: dt.date, held: list[str], window: int) -> float:
    """持股**等權組合**近 window 日報酬的年化標準差。

    用組合報酬而非個股波動的加權平均：後者忽略相關性，個股齊漲齊跌時會嚴重低估。
    """
    px = (
        view.table("prices_daily")
        .select("date", "stock_id", "close")
        .filter(pl.col("stock_id").is_in(held))
        .collect()
        .sort(["stock_id", "date"])
    )
    if px.height == 0:
        raise DataError(
            "prices_daily",
            signal_date,
            f"{signal_date} 的持股 {held[:5]} 在 PIT 視圖中沒有任何行情，無法估波動率。",
        )
    rets = (
        px.with_columns(
            (pl.col("close") / pl.col("close").shift(1).over("stock_id") - 1.0).alias("r")
        )
        .drop_nulls("r")
        .group_by("date")
        .agg(pl.col("r").mean().alias("r"))
        .sort("date")
        .tail(window)
    )
    if rets.height < window:
        raise DataError(
            "prices_daily",
            signal_date,
            f"估波動率需要 {window} 個交易日，只有 {rets.height} 個。"
            "不得靜默回傳原權重——那會讓 overlay 在資料不足時「成功地什麼都沒做」，"
            "而那剛好是回測最前段、曝險最該被管的時候。",
        )
    # polars 的 Series.std 型別是 float | timedelta（同一個方法也服務時間欄），
    # 這裡的欄位是報酬率，故明確收斂到 float 而不是讓型別檢查器猜。
    raw_sd = rets["r"].std(ddof=1)
    sd = float(raw_sd) if isinstance(raw_sd, int | float) else None
    if sd is None or sd <= 0.0:
        raise DataError(
            "prices_daily",
            signal_date,
            f"近 {window} 日組合報酬的標準差為 {raw_sd!r}——零波動代表價格未動或資料有誤，"
            "此時縮放倍數無定義。",
        )
    return sd * math.sqrt(_TRADING_DAYS_PER_YEAR)


@overlay("index_trend_gate")
def index_trend_gate(
    signal_date: dt.date, weights: pl.DataFrame, view: PitView, params: dict[str, object]
) -> pl.DataFrame:
    """Faber (2007, JWM)：大盤收盤 > N 日簡單移動平均 → 全額曝險；否則降到 `floor`（預設 0＝空手）。

    這是 D-092 P4 的擇時層。三個刻意的決定：

    1. **只看 signal_date 之前的指數**（`date < signal_date`）。訊號在 t 日產生、t+1 開盤執行，
       t 日的收盤在訊號時刻尚未發生；用它就是前視。`index_daily` 若無 `available_at`，
       PitView 不會替你擋，所以這裡自己擋。
    2. **N 固定在配置、不掃**。Zakamulin (2014, JAM) 指出這條規則對均線長度極敏感、
       樣本外弱得多——掃 N 就是在製造那個問題。預設 200（Faber 的 10 個月）。
    3. **歷史不足 N 日就 raise**，不靜默放行。放行等於 overlay 在資料不足時
       「成功地什麼都沒做」，而那正好落在回測最前段。

    現金報酬記 0（台幣活存約 1%，忽略是保守方向）。「換到防禦標的」引擎不支援，另立任務。
    參數：`index_id`（預設 TAIEX）、`window`（預設 200）、`floor`（預設 0.0）。
    """
    index_id = str(params.get("index_id", "TAIEX"))
    window = _as_int(params.get("window", 200), 200)
    floor = _as_float(params.get("floor", 0.0), 0.0)
    if window < 2:
        raise ConfigError(f"window 須 ≥ 2，得到 {window}")
    if not 0.0 <= floor <= 1.0:
        raise ConfigError(f"floor 須在 [0, 1]，得到 {floor}")

    closes = _index_closes(view, signal_date, index_id, window, "均線")
    sma = float(closes.mean())  # type: ignore[arg-type]  # Series.mean 的回傳型別是寬聯集
    return _scale(weights, 1.0 if float(closes.item(-1)) > sma else floor)


@overlay("absolute_momentum_gate")
def absolute_momentum_gate(
    signal_date: dt.date, weights: pl.DataFrame, view: PitView, params: dict[str, object]
) -> pl.DataFrame:
    """Antonacci (2014)《Dual Momentum Investing》的**絕對動能**那一半。

    規則：大盤過去 `window` 個交易日（預設 244 ≈ 12 個月）的報酬 > `hurdle` → 全額曝險；
    否則降到 `floor`（預設 0＝空手）。原著的另一半「相對動能」由策略本身的選股負責，
    這個 overlay 只做「該不該在場上」。

    **與 `index_trend_gate` 是兩條不同的規則，不是同一條的參數變體**：
    Faber 比的是「現價 vs 過去 N 日的平均」，Antonacci 比的是「現價 vs N 日前的價格」。
    均線會被中間那段路徑影響，端點報酬不會——同一段行情兩者可以給出相反的訊號。
    所以兩者要在同一個基座上並排跑，才分得出「擇時有用」與「這條規則有用」。

    三個刻意的決定，與 `index_trend_gate` 同一個理由：

    1. **只看 `date < signal_date` 的指數**——t 日收盤在訊號時刻尚未發生。
    2. **window 與 hurdle 固定在配置、不掃**（Zakamulin 2014, JAM）。
    3. **歷史不足就 raise**，不靜默放行。

    `hurdle` 預設 0.0 而非台幣短率：原著用超額報酬（相對國庫券），但台灣短率極低，
    且**提高 hurdle 會讓它更常空手**——設 0 是「較少出場」的保守方向，
    不會讓這條規則的效果被高估。要跑超額版就在配置裡明寫。
    """
    index_id = str(params.get("index_id", "TAIEX"))
    window = _as_int(params.get("window", 244), 244)
    hurdle = _as_float(params.get("hurdle", 0.0), 0.0)
    floor = _as_float(params.get("floor", 0.0), 0.0)
    if window < 2:
        raise ConfigError(f"window 須 ≥ 2，得到 {window}")
    if not 0.0 <= floor <= 1.0:
        raise ConfigError(f"floor 須在 [0, 1]，得到 {floor}")

    closes = _index_closes(view, signal_date, index_id, window, "絕對動能回看")
    first, last = float(closes.item(0)), float(closes.item(-1))
    if first <= 0.0:
        raise DataError("index_daily", signal_date, f"{index_id} 起點收盤為 {first}，無法算報酬。")
    return _scale(weights, 1.0 if (last / first - 1.0) > hurdle else floor)


@overlay("tsmom_vol_scaling")
def tsmom_vol_scaling(
    signal_date: dt.date, weights: pl.DataFrame, view: PitView, params: dict[str, object]
) -> pl.DataFrame:
    """Moskowitz, Ooi & Pedersen (2012, JFE)「Time Series Momentum」：方向 × 波動縮放。

    原著：部位 = sign(過去 12 個月報酬) × (目標波動 ÷ 事前波動)。兩個成分缺一不可，
    而這正是它與前兩條規則的差別：
    - 與 Antonacci 的差別：Antonacci 只有方向（在場／空手），MOP 在場時還要**按波動縮放部位**。
    - 與 Barroso–Santa-Clara 的差別：後者永遠在場、只縮放，MOP 多了**方向閘門**。
    所以 MOP ≈ Antonacci ∘ Barroso——若它的結果與兩者疊加一致，就證明沒有交互作用。

    方向看大盤（原著看資產自身；本專案的「資產」是策略組合，而組合的 12 個月報酬
    就是它自己的績效——用它當閘門會讓策略在自己賠錢後停手，那是另一條規則，不在本批）。
    波動看**持股等權組合**的近 `vol_window` 日已實現波動（與 Barroso 版共用同一個估計）。
    不做空（sign 為負 → `floor`，預設 0），不加槓桿（倍數封頂 1.0）。
    參數：`index_id`、`mom_window`（預設 244）、`vol_window`（預設 60）、`target_vol`（預設 0.20）、
    `min_mult`（在場時的倍數下限，預設 0.2）、`floor`（方向為負時，預設 0.0）。全部固定不掃。
    """
    index_id = str(params.get("index_id", "TAIEX"))
    mom_window = _as_int(params.get("mom_window", 244), 244)
    vol_window = _as_int(params.get("vol_window", 60), 60)
    target = _as_float(params.get("target_vol", 0.20), 0.20)
    min_mult = _as_float(params.get("min_mult", 0.2), 0.2)
    floor = _as_float(params.get("floor", 0.0), 0.0)
    if mom_window < 2 or vol_window < 2:
        raise ConfigError(f"mom_window／vol_window 須 ≥ 2，得到 {mom_window}／{vol_window}")
    if target <= 0.0:
        raise ConfigError(f"target_vol 須 > 0，得到 {target}")

    closes = _index_closes(view, signal_date, index_id, mom_window, "時序動能回看")
    first = float(closes.item(0))
    if first <= 0.0:
        raise DataError("index_daily", signal_date, f"{index_id} 起點收盤為 {first}，無法算報酬。")
    if float(closes.item(-1)) / first - 1.0 <= 0.0:
        return _scale(weights, floor)
    realized = _realized_vol(view, signal_date, weights["stock_id"].unique().to_list(), vol_window)
    return _scale(weights, max(min_mult, min(1.0, target / realized)))


@overlay("inverse_variance_scaling")
def inverse_variance_scaling(
    signal_date: dt.date, weights: pl.DataFrame, view: PitView, params: dict[str, object]
) -> pl.DataFrame:
    """Moreira & Muir (2017, JF)「Volatility-Managed Portfolios」：曝險 ∝ 1／已實現**變異數**。

    與 Barroso–Santa-Clara 的差別只有一個指數，但那個指數是全部的差別：
    Barroso 用 target／σ（∝ 1/σ），Moreira–Muir 用 c／σ²（∝ 1/σ²）。
    平方讓它在高波動時砍得**遠**比 Barroso 兇、在低波動時也放得更開——
    原著的論點是變異數的可預測性遠高於報酬，故按變異數的倒數配置能提高 Sharpe。

    `c` 是原著裡讓策略平均曝險等於 1 的常數；本專案不加槓桿（封頂 1.0），故 `c` 改由
    `ref_vol` 給：倍數 = min(1, (ref_vol／realized)²)。ref_vol 預設 0.20（與 Barroso 版的
    target_vol 同值——**刻意相同**，讓兩者的差只剩「一次方 vs 二次方」）。
    參數：`vol_window`（預設 60）、`ref_vol`（預設 0.20）、`min_mult`（預設 0.2）。固定不掃。
    """
    vol_window = _as_int(params.get("vol_window", 60), 60)
    ref = _as_float(params.get("ref_vol", 0.20), 0.20)
    min_mult = _as_float(params.get("min_mult", 0.2), 0.2)
    if vol_window < 2:
        raise ConfigError(f"vol_window 須 ≥ 2，得到 {vol_window}")
    if ref <= 0.0:
        raise ConfigError(f"ref_vol 須 > 0，得到 {ref}")
    realized = _realized_vol(view, signal_date, weights["stock_id"].unique().to_list(), vol_window)
    return _scale(weights, max(min_mult, min(1.0, (ref / realized) ** 2)))


@overlay("crash_risk_gate")
def crash_risk_gate(
    signal_date: dt.date, weights: pl.DataFrame, view: PitView, params: dict[str, object]
) -> pl.DataFrame:
    """Daniel & Moskowitz (2016, JFE)「Momentum Crashes」：空頭 **且** 高波動時才降曝險。

    原著的發現：動能策略的崩盤集中在「大盤過去兩年為負」**且**「市場波動高」的時候
    ——空頭裡的急反彈把做多贏家、做空輸家的組合同時打穿。兩個條件缺一不可：
    空頭但低波動（慢跌）不觸發，高波動但多頭（急漲）也不觸發。

    這是**條件式**規則，與前面五條都不同：
    - Faber／Antonacci 只看方向，不看波動。
    - Barroso／Moreira–Muir／MOP 的縮放對「為什麼波動高」不分青紅皂白——
      多頭裡的急漲也會被砍。DM 只砍空頭裡的高波動。
    所以若 DM 的效果與 Antonacci 相近，代表台股的高波動幾乎都在空頭裡；
    若 DM 明顯較好，代表「多頭高波動」那段被前幾條規則錯砍了。

    空頭 = 大盤過去 `bear_window`（預設 488 ≈ 24 個月）報酬 < 0；
    高波動 = 大盤近 `vol_window`（預設 60）日的年化波動 > `vol_threshold`（預設 0.25——
    台股加權指數長期年化波動約 18~20%，0.25 是「高於常態」的保守界，不掃）。
    兩者同時成立 → `floor`（預設 0）；否則 1.0。
    """
    index_id = str(params.get("index_id", "TAIEX"))
    bear_window = _as_int(params.get("bear_window", 488), 488)
    vol_window = _as_int(params.get("vol_window", 60), 60)
    vol_threshold = _as_float(params.get("vol_threshold", 0.25), 0.25)
    floor = _as_float(params.get("floor", 0.0), 0.0)
    if bear_window < 2 or vol_window < 2:
        raise ConfigError(f"bear_window／vol_window 須 ≥ 2，得到 {bear_window}／{vol_window}")
    if vol_threshold <= 0.0:
        raise ConfigError(f"vol_threshold 須 > 0，得到 {vol_threshold}")

    closes = _index_closes(view, signal_date, index_id, bear_window, "空頭狀態回看")
    first = float(closes.item(0))
    if first <= 0.0:
        raise DataError("index_daily", signal_date, f"{index_id} 起點收盤為 {first}，無法算報酬。")
    bear = float(closes.item(-1)) / first - 1.0 < 0.0
    # 大盤自身的已實現波動（不是持股組合的）：原著的條件是市場狀態，不是策略狀態
    tail = closes.tail(vol_window + 1)
    rets = (tail / tail.shift(1) - 1.0).drop_nulls()
    raw_sd = rets.std(ddof=1)
    sd = float(raw_sd) if isinstance(raw_sd, int | float) else None
    if sd is None or sd <= 0.0:
        raise DataError(
            "index_daily", signal_date, f"{index_id} 近 {vol_window} 日波動為 {raw_sd!r}。"
        )
    high_vol = sd * math.sqrt(_TRADING_DAYS_PER_YEAR) > vol_threshold
    return _scale(weights, floor if (bear and high_vol) else 1.0)


@overlay("breadth_regime_filter")
def breadth_regime_filter(
    signal_date: dt.date, weights: pl.DataFrame, view: PitView, params: dict[str, object]
) -> pl.DataFrame:
    """市場寬度低 → 縮減整體曝險（雙閾值；本版單日無狀態、之間線性內插）。"""
    ma_window = _as_int(params.get("ma_window", 60), 60)
    lower = _as_float(params.get("lower", 0.4), 0.4)
    upper = _as_float(params.get("upper", 0.6), 0.6)
    floor = _as_float(params.get("exposure_floor", 0.5), 0.5)

    breadth = market_breadth(view, signal_date, ma_window)
    if breadth <= lower:
        mult = floor
    elif breadth >= upper:
        mult = 1.0
    else:
        mult = floor + (1.0 - floor) * (breadth - lower) / (upper - lower)
    return _scale(weights, mult)


@overlay("vol_target_scaling")
def vol_target_scaling(
    signal_date: dt.date, weights: pl.DataFrame, view: PitView, params: dict[str, object]
) -> pl.DataFrame:
    """Barroso & Santa-Clara (2015, JFE)「Momentum Has Its Moments」：以已實現波動率縮放曝險。

    動能策略的病不是報酬不夠，是崩起來太深——實測本專案 A 軌 MDD −38.5%、ML −46.1%，
    而 Daniel & Moskowitz (2016, JFE)「Momentum Crashes」指出動能在空頭＋高波動時崩得最慘。
    原文的做法是用**近期已實現波動率**縮放部位，把組合的事後波動率壓到一個常數目標，
    實證上大致讓 Sharpe 翻倍、極端左尾大幅收斂。

    本版的實作與原文的三點差異，都是刻意的：

    1. **不加槓桿**（倍數封頂 1.0）。原文允許 >1，但本專案的回測引擎沒有融資模型，
       放大到 >1 會產生引擎無法成交的目標權重——那是一個靜默的錯數字。
    2. **用等權組合報酬的波動率**，而非個股波動率的加權平均：後者忽略相關性，
       在個股齊漲齊跌時會嚴重低估組合波動。
    3. **歷史不足時 raise 而非放行**。靜默放行會讓 overlay 在資料不足時「成功地什麼都沒做」，
       而那剛好發生在回測最前面那段——曝險最該被管的時候。

    參數：`target_vol`（年化，預設 0.20）、`window`（估計窗，交易日，預設 60）、
    `min_mult`（倍數下限，預設 0.2；避免極端行情下曝險歸零而完全踏空）。
    """
    target = _as_float(params.get("target_vol", 0.20), 0.20)
    window = _as_int(params.get("window", 60), 60)
    min_mult = _as_float(params.get("min_mult", 0.2), 0.2)
    if target <= 0.0:
        raise ConfigError(f"target_vol 須 > 0，得到 {target}")
    if window < 2:
        raise ConfigError(f"window 須 ≥ 2，得到 {window}")

    held = weights["stock_id"].unique().to_list()
    realized = _realized_vol(view, signal_date, held, window)
    mult = min(1.0, target / realized)  # 封頂 1.0：不加槓桿
    mult = max(min_mult, mult)
    return _scale(weights, mult)
