"""盤整型態因子（T-302，定義由 D-151 §三 事前寫死）。

第十二輪要回答「還沒飆的股票裡，誰接下來會飆」。使用者點名三種起漲前的樣子：布林通道壓縮、
均線糾結、關鍵價位撐住——「關鍵價位就是要配合量，在大量交易的位置卡住下不去」。五支因子：

| 因子 | 量什麼 |
|---|---|
| `price.bb_squeeze_120` | 布林寬度在自己過去 120 日的百分位（越低越壓縮）；寬度同 `price.bollinger_width_20` |
| `price.ma_tangle` | 5／10／20／60 日均線的最大最小差 ÷ 收盤（越小越糾結） |
| `price.vp_poc_dist_60` | 收盤相對 60 日量價分布高峰（POC）的距離 |
| `price.vp_support_hold_20` | 近 20 日「最低價碰到大量區、收盤守在其上」的天數 |
| `price.vp_near_share_60` | 60 日成交量中落在今日收盤 ±3% 內的比例 |

**量價分布只有日線可用**：每天的成交量平均攤到當日〔低, 高〕——這是近似，真正的分價量要日內資料
（D-151 §六 延後）。當日最高＝最低（一價到底）時整筆量記在那個價位所在的格子。

收盤 ≤ 0 的列依 D-040 的契約是「當日無成交」，與停牌一樣不算一列。窗內不足或有缺值一律 null，
不用短窗充數（黃金規則 5）。`direction` 只是先驗；ML 特徵取原值，不受影響。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np
import polars as pl
from numpy.lib.stride_tricks import sliding_window_view

from twquant.factors.registry import factor
from twquant.panel import safe_ratio

if TYPE_CHECKING:
    from twquant.data.warehouse import PitView

_TABLES = ["prices_daily"]
_OUT_SCHEMA: dict[str, type[pl.DataType]] = {
    "date": pl.Date,
    "stock_id": pl.String,
    "value": pl.Float64,
}
_CHUNK = 256  # 量價分布一次處理幾列：(列, 窗, 格) 的暫存陣列上限，只影響記憶體不影響結果


def _int(params: dict[str, object], key: str, default: int) -> int:
    raw = params.get(key, default)
    return int(raw) if isinstance(raw, int | float | str) else default


def _float(params: dict[str, object], key: str, default: float) -> float:
    raw = params.get(key, default)
    return float(raw) if isinstance(raw, int | float | str) else default


def _panel(view: PitView, *cols: str) -> pl.DataFrame:
    """依 (stock_id, date) 排序的價格面板；收盤 ≤ 0（D-040：無成交）不算一列。"""
    return (
        view.table("prices_daily")
        .select("date", "stock_id", "close", *cols)
        .filter(pl.col("close") > 0.0)
        .sort(["stock_id", "date"])
        .collect()
    )


def _per_stock(df: pl.DataFrame, fn: Callable[[pl.DataFrame], np.ndarray]) -> pl.DataFrame:
    """逐檔用 numpy 算、再拼回 (date, stock_id, value)；NaN 轉 null 後丟掉。"""
    parts = [
        g.select("date", "stock_id").with_columns(
            value=pl.Series("value", fn(g), dtype=pl.Float64, nan_to_null=True)
        )
        for g in df.partition_by("stock_id", maintain_order=True)
    ]
    if not parts:
        return pl.DataFrame(schema=_OUT_SCHEMA)
    return pl.concat(parts).drop_nulls("value")


def _col(g: pl.DataFrame, name: str) -> np.ndarray:
    return np.asarray(g[name].cast(pl.Float64).to_numpy(), dtype=float)


def rolling_weak_pct(x: np.ndarray, n: int) -> np.ndarray:
    """每一點在自己最近 n 個值（含自己）中「≤ 自己」的比例；窗內有缺值即 NaN。"""
    out = np.full(x.shape, np.nan)
    if x.size < n:
        return out
    win = sliding_window_view(x, n)
    pct = (win <= win[:, -1:]).mean(axis=1)
    pct[np.isnan(win).any(axis=1)] = np.nan
    out[n - 1 :] = pct
    return out


def volume_profile(
    low: np.ndarray,
    high: np.ndarray,
    vol: np.ndarray,
    close: np.ndarray,
    window: int,
    bins: int,
    band: float,
) -> tuple[np.ndarray, np.ndarray]:
    """每一列（含）往前 window 列的日線量價分布 → (POC 價位, 今日收盤 ±band 內的量占比)。

    價格區間〔窗內最低, 窗內最高〕切 `bins` 等分；每日成交量按當日〔低, 高〕與各格的重疊比例攤入。
    POC＝量最多那格的中價（同量取價位最低那格，確保決定性）。窗內有缺值、區間為零或總量為零 → NaN。
    """
    n = close.size
    poc = np.full(n, np.nan)
    near = np.full(n, np.nan)
    if n < window:
        return poc, near
    lw, hw, vw = (sliding_window_view(a, window) for a in (low, high, vol))
    k = np.arange(bins, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        for start in range(0, lw.shape[0], _CHUNK):
            stop = min(lw.shape[0], start + _CHUNK)
            lo_d, hi_d, v = lw[start:stop], hw[start:stop], vw[start:stop]
            now = close[window - 1 + start : window - 1 + stop]
            lo, hi, tot = lo_d.min(axis=1), hi_d.max(axis=1), v.sum(axis=1)
            ok = (
                np.isfinite(lo_d).all(axis=1)
                & np.isfinite(hi_d).all(axis=1)
                & np.isfinite(v).all(axis=1)
                & (hi > lo)
                & (tot > 0.0)
            )
            width = np.where(ok, (hi - lo) / bins, 1.0)
            e_lo = lo[:, None, None] + width[:, None, None] * k[None, None, :]
            e_hi = e_lo + width[:, None, None]
            rng = (hi_d - lo_d)[:, :, None]
            overlap = np.clip(
                np.minimum(hi_d[:, :, None], e_hi) - np.maximum(lo_d[:, :, None], e_lo), 0.0, None
            )
            cell = np.clip(np.floor((lo_d - lo[:, None]) / width[:, None]), 0, bins - 1)
            point = (k[None, None, :] == cell[:, :, None]).astype(float)
            share = np.where(rng > 0.0, overlap / np.where(rng > 0.0, rng, 1.0), point)
            prof = (v[:, :, None] * share).sum(axis=1)
            poc[window - 1 + start : window - 1 + stop] = np.where(
                ok, lo + (prof.argmax(axis=1) + 0.5) * width, np.nan
            )
            b_lo, b_hi = (now * (1.0 - band))[:, None], (now * (1.0 + band))[:, None]
            ov = np.clip(np.minimum(hi_d, b_hi) - np.maximum(lo_d, b_lo), 0.0, None)
            r2 = hi_d - lo_d
            inside = ((lo_d >= b_lo) & (lo_d <= b_hi)).astype(float)
            sh2 = np.where(r2 > 0.0, ov / np.where(r2 > 0.0, r2, 1.0), inside)
            near[window - 1 + start : window - 1 + stop] = np.where(
                ok, (v * sh2).sum(axis=1) / np.where(ok, tot, 1.0), np.nan
            )
    return poc, near


def _vp(g: pl.DataFrame, params: dict[str, object]) -> tuple[np.ndarray, np.ndarray]:
    return volume_profile(
        _col(g, "low"),
        _col(g, "high"),
        _col(g, "volume"),
        _col(g, "close"),
        _int(params, "window", 60),
        _int(params, "bins", 20),
        _float(params, "near_band", 0.03),
    )


@factor(
    name="price.bb_squeeze_120",
    category="price",
    direction=1,  # 先驗沿用 bollinger_width_20：帶寬收縮平均較差（ML 取原值，不受影響）
    tables=_TABLES,
    lookback_days=150,  # 20 日寬度 ＋ 120 日百分位 − 1 ＝ 139 列
    params={"window": 20, "n_std": 2.0, "rank_window": 120},
    anchor_invariant=True,
)
def bb_squeeze_120(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """布林寬度（2×n_std×σ ÷ MA）在自己過去 rank_window 日中「≤ 今日」的比例（0~1，越低越壓縮）。"""
    window = _int(params, "window", 20)
    n_std = _float(params, "n_std", 2.0)
    rank = _int(params, "rank_window", 120)
    ma = pl.col("close").rolling_mean(window).over("stock_id")
    sd = pl.col("close").rolling_std(window).over("stock_id")
    df = _panel(view).with_columns(_w=safe_ratio(2.0 * n_std * sd, ma))
    return _per_stock(df, lambda g: rolling_weak_pct(_col(g, "_w"), rank))


@factor(
    name="price.ma_tangle",
    category="price",
    direction=-1,  # 先驗：越糾結越接近變盤
    tables=_TABLES,
    lookback_days=70,
    params={"windows": [5, 10, 20, 60]},
    anchor_invariant=True,
)
def ma_tangle(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """（各均線最大值 − 最小值）÷ 收盤。任一條均線還算不出來 → null（不拿較少的均線充數）。"""
    raw = params.get("windows", [5, 10, 20, 60])
    windows = [int(w) for w in raw] if isinstance(raw, list | tuple) else [5, 10, 20, 60]
    mas = [pl.col("close").rolling_mean(w).over("stock_id") for w in windows]
    ready = pl.all_horizontal([m.is_not_null() for m in mas])
    spread = safe_ratio(pl.max_horizontal(mas) - pl.min_horizontal(mas), pl.col("close"))
    return (
        _panel(view)
        .with_columns(value=pl.when(ready).then(spread))
        .select("date", "stock_id", "value")
        .drop_nulls("value")
    )


@factor(
    name="price.vp_poc_dist_60",
    category="price",
    direction=1,  # 先驗：站在大量區之上
    tables=_TABLES,
    lookback_days=70,
    params={"window": 60, "bins": 20, "near_band": 0.03},
    anchor_invariant=True,
)
def vp_poc_dist_60(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """收盤 ÷ 60 日量價分布 POC − 1（正＝在大量區之上）。"""
    df = _panel(view, "low", "high", "volume")

    def one(g: pl.DataFrame) -> np.ndarray:
        poc, _ = _vp(g, params)
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(poc > 0.0, _col(g, "close") / poc - 1.0, np.nan)

    return _per_stock(df, one)


@factor(
    name="price.vp_support_hold_20",
    category="price",
    direction=1,  # 先驗：大量區被測試而守住
    tables=_TABLES,
    lookback_days=90,  # 60 日 POC ＋ 20 日計數 − 1 ＝ 79 列
    params={"window": 60, "bins": 20, "near_band": 0.03, "hold_window": 20, "touch": 0.02},
    anchor_invariant=True,
)
def vp_support_hold_20(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """近 hold_window 日中「最低價 ≤ 當日 POC×(1+touch) 且收盤 ≥ 當日 POC」的天數。

    每一天用**它自己那天**的 60 日 POC（只含該日以前的資料）。窗內任一天沒有 POC → null。
    """
    hold = _int(params, "hold_window", 20)
    touch = _float(params, "touch", 0.02)
    df = _panel(view, "low", "high", "volume")

    def one(g: pl.DataFrame) -> np.ndarray:
        poc, _ = _vp(g, params)
        low, close = _col(g, "low"), _col(g, "close")
        valid = np.isfinite(poc)
        with np.errstate(invalid="ignore"):
            held = (valid & (low <= poc * (1.0 + touch)) & (close >= poc)).astype(float)
        out = np.full(poc.shape, np.nan)
        if poc.size < hold:
            return out
        n_ok = sliding_window_view(valid.astype(float), hold).sum(axis=1)
        cnt = sliding_window_view(held, hold).sum(axis=1)
        out[hold - 1 :] = np.where(n_ok == hold, cnt, np.nan)
        return out

    return _per_stock(df, one)


@factor(
    name="price.vp_near_share_60",
    category="price",
    direction=1,  # 先驗：股價正坐在大量區
    tables=_TABLES,
    lookback_days=70,
    params={"window": 60, "bins": 20, "near_band": 0.03},
    anchor_invariant=True,
)
def vp_near_share_60(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """60 日成交量中落在今日收盤 ±near_band 內的比例（0~1）。"""
    df = _panel(view, "low", "high", "volume")
    return _per_stock(df, lambda g: _vp(g, params)[1])
