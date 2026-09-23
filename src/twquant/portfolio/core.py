"""投組核心（SPEC-005 §1/§2/§3）：sleeve 組合、限額、條件式波動目標、kill-switch。

純運算：無網路、無寫檔（狀態持久化在 twquant.state）。限額順序固定（SPEC-005 §1）：
個股上限 → （產業上限，待資料）→ 周轉率上限 → 波動目標；最後套用 kill-switch 狀態。
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import numpy as np
import polars as pl

from twquant.errors import ConfigError

if TYPE_CHECKING:
    from twquant.portfolio.config import (
        PortfolioConfig,
        RiskConfig,
        VolEstimator,
        VolTargetConfig,
    )

Status = Literal["NORMAL", "NO_NEW_ENTRY", "REDUCED", "HALTED"]
STATE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SleeveSignal:
    strategy: str
    date: dt.date
    weights: pl.DataFrame  # (stock_id, target_weight)


@dataclass
class PortfolioState:
    status: Status = "NORMAL"
    entered_at: str = ""  # ISO；空=未設
    trigger_reason: str = ""
    freeze_next_day: bool = False
    equity_high_water: float = 0.0
    schema_version: int = STATE_SCHEMA_VERSION


@dataclass(frozen=True)
class TargetBook:
    positions: pl.DataFrame  # (stock_id, target_weight)
    sleeve_attribution: dict[str, dict[str, float]]  # stock_id -> {sleeve: weight}
    status: Status
    reason: str


# ── 組合 ────────────────────────────────────────────────────────────
def combine_sleeves(
    sleeve_signals: list[SleeveSignal], budgets: dict[str, float]
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """sleeve 內權重 × risk_budget → 帳戶權重 + 歸因（AC-1）。"""
    acct: dict[str, float] = defaultdict(float)
    attr: dict[str, dict[str, float]] = defaultdict(dict)
    for ss in sleeve_signals:
        b = budgets.get(ss.strategy, 0.0)
        for row in ss.weights.select("stock_id", "target_weight").to_dicts():
            sid = str(row["stock_id"])
            w = float(row["target_weight"]) * b
            acct[sid] += w
            attr[sid][ss.strategy] = attr[sid].get(ss.strategy, 0.0) + w
    return dict(acct), {k: dict(vv) for k, vv in attr.items()}


def per_stock_cap(weights: dict[str, float], cap: float) -> dict[str, float]:
    """個股上限：裁掉超額、不重分配（SPEC-005 §1）。"""
    return {s: min(w, cap) for s, w in weights.items()}


def _turnover(target: dict[str, float], current: dict[str, float]) -> float:
    keys = set(target) | set(current)
    return sum(abs(target.get(k, 0.0) - current.get(k, 0.0)) for k in keys) / 2.0


def turnover_cap(
    target: dict[str, float], current: dict[str, float], max_turnover: float
) -> dict[str, float]:
    """周轉率上限：超過則新舊權重線性插值把周轉壓到上限（方向不變，AC-4）。"""
    to = _turnover(target, current)
    if to <= max_turnover or to == 0:
        return dict(target)
    alpha = max_turnover / to
    keys = set(target) | set(current)
    out = {}
    for k in keys:
        c = current.get(k, 0.0)
        t = target.get(k, 0.0)
        w = c + alpha * (t - c)
        if abs(w) > 1e-12:
            out[k] = w
    return out


# ── 條件式波動目標的估計器（D-004；D-149 補實作）──────────────────────
#
# `vol_scale` 在 2026-07 就寫好了，但產生 `VolContext` 的估計器從未實作——整個程式碼裡
# 沒有任何呼叫端算出 (realized_vol, vol_percentile)。第十一輪（D-149）要把它接到每日補位
# 才補上。**單一實作**：投組層與回測引擎都經過這幾個函數，不各自估一份。


def ewma_vol_series(
    returns: Sequence[float] | np.ndarray, est: VolEstimator, periods: float
) -> np.ndarray:
    """每個時點以最近 `window_days` 個日報酬的指數權重標準差 × √periods（D-004 估計器）。

    權重 0.5^(k/halflife)，k=0 為最新；加權平均去心並做無偏修正 (Σw)²/((Σw)²−Σw²)，
    與 polars `ewm_std(half_life=, adjust=True, bias=False)` 在同一段窗上相同（測試守著）。
    回傳長度 len(returns) − window_days + 1，最後一點是最新的估計。
    """
    if est.method != "ewma":
        raise ConfigError(f"波動估計器只實作 ewma（D-004），收到 {est.method!r}")
    if est.window_days < 2 or est.halflife <= 0:
        raise ConfigError(f"估計窗須 ≥ 2、半衰期須 > 0，收到 {est.window_days}／{est.halflife}")
    r = np.asarray(returns, dtype=float)
    win = est.window_days
    if r.size < win:
        raise ValueError(f"波動估計需要至少 {win} 個報酬，只有 {r.size} 個")
    if not np.isfinite(r).all():
        raise ValueError("報酬含 NaN／inf——上游價格有問題，不得帶進波動估計")
    w = 0.5 ** (np.arange(win - 1, -1, -1, dtype=float) / est.halflife)  # 由舊到新
    s1, s2 = float(w.sum()), float((w * w).sum())
    windows = np.lib.stride_tricks.sliding_window_view(r, win)
    mean = windows @ w / s1
    var = ((windows - mean[:, None]) ** 2) @ w / s1 * (s1 * s1 / (s1 * s1 - s2))
    return np.asarray(np.sqrt(var * periods), dtype=float)


def vol_context(
    returns: Sequence[float] | np.ndarray, cfg: VolTargetConfig, periods: float
) -> VolContext:
    """今日的（年化波動, 百分位）：估計值序列的最後一點，及它在最近 `percentile_window_days`
    個估計值（含今日）中「≤ 今日」的比例 × 100。

    需要 window_days + percentile_window_days − 1 個報酬，多給的只用最後那一段。
    不足即 raise：歷史不足時照給 1 倍，就是在回測最前段靜默地不管曝險。
    """
    need = cfg.estimator.window_days + cfg.conditional.percentile_window_days - 1
    r = np.asarray(returns, dtype=float)
    if r.size < need:
        raise ValueError(
            f"條件式波動需要 {need} 個報酬（估計窗 {cfg.estimator.window_days} ＋ 百分位窗 "
            f"{cfg.conditional.percentile_window_days} − 1），只有 {r.size} 個"
        )
    series = ewma_vol_series(r[-need:], cfg.estimator, periods)
    today = float(series[-1])
    return VolContext(realized_vol=today, vol_percentile=100.0 * float(np.mean(series <= today)))


def vol_triggered(ctx: VolContext, cfg: VolTargetConfig) -> bool:
    """D-004 觸發：落在自身百分位窗的 ≥ reduce_above_pct 百分位，**且**高於年化目標。"""
    return (
        ctx.vol_percentile >= cfg.conditional.reduce_above_pct and ctx.realized_vol > cfg.annual_vol
    )


def vol_multiplier(trigger: VolContext, scale_vol: float, cfg: VolTargetConfig) -> float:
    """D-004 倍數：`trigger` 觸發時縮到 annual_vol ÷ scale_vol（上限 max_leverage），否則 1.0。

    `scale_vol` 是被縮的那個組合自己的波動。D-004 原型裡兩者是同一條序列；
    D-149 F2 觸發看大盤、倍數看持股，所以分開傳。
    """
    if not cfg.enabled or scale_vol <= 0.0 or not vol_triggered(trigger, cfg):
        return 1.0
    return min(cfg.annual_vol / scale_vol, cfg.max_leverage)


def vol_scale(
    weights: dict[str, float], realized_vol: float, vol_percentile: float, cfg: PortfolioConfig
) -> dict[str, float]:
    """條件式波動目標（D-004）：只在波動落於 >reduce_above_pct 百分位才縮，且只降不升。"""
    ctx = VolContext(realized_vol=realized_vol, vol_percentile=vol_percentile)
    mult = vol_multiplier(ctx, realized_vol, cfg.vol_target)
    if mult == 1.0:
        return dict(weights)  # 非極端 → 維持 1x
    return {s: w * mult for s, w in weights.items()}


# ── kill-switch 狀態機 ─────────────────────────────────────────────
def evaluate_killswitch(
    state: PortfolioState,
    equity_today: float,
    equity_prev: float,
    cfg: RiskConfig,
    now: dt.datetime,
) -> PortfolioState:
    """依當日權益更新狀態（優先取最嚴；HALTED 僅人工恢復）。"""
    high_water = (
        max(state.equity_high_water, equity_today) if state.equity_high_water else equity_today
    )
    dd = equity_today / high_water - 1.0 if high_water > 0 else 0.0
    daily_ret = equity_today / equity_prev - 1.0 if equity_prev > 0 else 0.0

    def _mk(status: Status, reason: str) -> PortfolioState:
        return PortfolioState(
            status=status,
            entered_at=now.isoformat(),
            trigger_reason=reason,
            freeze_next_day=state.freeze_next_day,
            equity_high_water=high_water,
            schema_version=STATE_SCHEMA_VERSION,
        )

    if state.status == "HALTED":  # 人工才可恢復
        return _mk("HALTED", state.trigger_reason or "halted")
    if dd <= -cfg.drawdown_halt_pct:
        return _mk("HALTED", f"drawdown {dd:.1%} > halt")
    if dd <= -cfg.drawdown_reduce_pct:
        return _mk("REDUCED", f"drawdown {dd:.1%} > reduce")
    if daily_ret <= -cfg.daily_loss_pct:
        return _mk("NO_NEW_ENTRY", f"daily loss {daily_ret:.1%}")
    # 恢復（遲滯）：REDUCED 需回撤收斂；NO_NEW_ENTRY 次日自動回
    if state.status == "REDUCED" and dd > -cfg.drawdown_reduce_recover_pct:
        return _mk("NORMAL", "recovered")
    if state.status == "REDUCED":
        return _mk("REDUCED", state.trigger_reason)
    return _mk("NORMAL", "")


def apply_state(
    target: dict[str, float], current: dict[str, float], state: PortfolioState, reduced_scale: float
) -> dict[str, float]:
    """把 kill-switch 狀態套到目標權重。"""
    if state.status == "HALTED":
        return {}
    if state.status == "REDUCED":
        return {s: w * reduced_scale for s, w in target.items()}
    if state.status == "NO_NEW_ENTRY":
        # 任一股票權重不得上升，且不得新增部位
        return {
            s: min(target.get(s, 0.0), current.get(s, 0.0))
            for s in current
            if current.get(s, 0.0) > 0
        }
    return dict(target)


# ── 組裝 ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class VolContext:
    realized_vol: float
    vol_percentile: float


def build_book(
    sleeve_signals: list[SleeveSignal],
    current_weights: dict[str, float],
    state: PortfolioState,
    cfg: PortfolioConfig,
    risk: RiskConfig,
    vol_ctx: VolContext | None = None,
) -> TargetBook:
    """組合 sleeve → 個股上限 → 周轉上限 → 波動目標 → kill-switch → TargetBook。"""
    acct, attr = combine_sleeves(sleeve_signals, cfg.budgets)
    acct = per_stock_cap(acct, cfg.limits.max_weight_per_stock)
    if vol_ctx is not None:
        acct = vol_scale(acct, vol_ctx.realized_vol, vol_ctx.vol_percentile, cfg)
    acct = turnover_cap(acct, current_weights, cfg.limits.max_daily_turnover)
    acct = apply_state(acct, current_weights, state, risk.reduced_scale)

    positions = pl.DataFrame(
        {"stock_id": list(acct), "target_weight": [acct[s] for s in acct]},
        schema={"stock_id": pl.String, "target_weight": pl.Float64},
    ).sort("stock_id")
    return TargetBook(
        positions=positions,
        sleeve_attribution={s: attr.get(s, {}) for s in acct},
        status=state.status,
        reason=state.trigger_reason,
    )
