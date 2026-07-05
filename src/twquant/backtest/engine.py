"""日頻回測引擎（SPEC-004 §1/§3）。

執行模型：訊號日 t 收盤產生目標權重 → t+1 開盤成交（next_open）。整數張、價格相依
成本、漲跌停不成交（記 unfilled）、參與率上限。會計恆等：權益 = 現金 + 持倉市值，
成本於成交時自現金扣除，全程現金 ≥ 0（AC-6）。純運算：不寫檔（落地由 reporting）。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import polars as pl

from twquant.backtest.costs import CostConfig, limit_down_proxy, limit_up_proxy, trade_cost
from twquant.backtest.metrics import Metrics, compute_metrics

if TYPE_CHECKING:
    from twquant.data.warehouse import Warehouse
    from twquant.strategy.config import StrategyConfig

_LOT = 1000
_Row = dict[str, Any]  # 單日單股行情列（open/close/limit/_prev_close/_adv）


@dataclass(frozen=True)
class EquityPoint:
    date: dt.date
    equity: float
    cash: float
    positions_value: float


@dataclass(frozen=True)
class Trade:
    date: dt.date
    stock_id: str
    side: str  # buy | sell
    shares: int
    price: float
    commission: float
    tax: float
    slippage: float


@dataclass(frozen=True)
class Unfilled:
    date: dt.date
    stock_id: str
    side: str
    shares: int
    reason: str  # limit_up | limit_down | participation | cash


@dataclass(frozen=True)
class BacktestResult:
    equity: list[EquityPoint]
    ledger: list[Trade]
    unfilled: list[Unfilled]
    metrics: Metrics
    initial_capital: float
    final_equity: float


@dataclass
class _State:
    cash: float
    holdings: dict[str, int] = field(default_factory=dict)  # stock_id -> shares
    last_close: dict[str, float] = field(default_factory=dict)
    ledger: list[Trade] = field(default_factory=list)
    unfilled: list[Unfilled] = field(default_factory=list)
    traded_notional: float = 0.0


def _price_panel(wh: Warehouse, start: dt.date, end: dt.date) -> dict[dt.date, dict[str, _Row]]:
    """載入 [start,end] 的日行情，附前收與 20 日 ADV，組成 {date: {stock_id: row}}。"""
    lf = (
        wh.scan("prices_daily")
        .select("date", "stock_id", "open", "close", "amount", "limit_up_price", "limit_down_price")
        .sort(["stock_id", "date"])
        .with_columns(
            _prev_close=pl.col("close").shift(1).over("stock_id", order_by="date"),
            _adv=pl.col("amount").rolling_median(20).over("stock_id"),
        )
        .filter((pl.col("date") >= start) & (pl.col("date") <= end))
    )
    by_date: dict[dt.date, dict[str, _Row]] = {}
    for r in lf.collect().to_dicts():
        by_date.setdefault(r["date"], {})[r["stock_id"]] = r
    return by_date


def _limit_up(row: _Row) -> float | None:
    if row.get("limit_up_price") is not None:
        return float(row["limit_up_price"])
    prev = row.get("_prev_close")
    return limit_up_proxy(float(prev)) if prev is not None else None


def _limit_down(row: _Row) -> float | None:
    if row.get("limit_down_price") is not None:
        return float(row["limit_down_price"])
    prev = row.get("_prev_close")
    return limit_down_proxy(float(prev)) if prev is not None else None


def _participation_cap_shares(row: _Row, price: float, max_participation: float) -> int | None:
    """依 20 日 ADV 的參與率上限換算最大可成交張數（股）。ADV 缺 → 不設限（None）。"""
    adv = row.get("_adv")
    if adv is None or adv <= 0:
        return None
    max_notional = max_participation * float(adv)
    return int(max_notional // (price * _LOT)) * _LOT


def _portfolio_value(state: _State, day_px: dict[str, _Row]) -> float:
    mv = 0.0
    for sid, sh in state.holdings.items():
        px = day_px.get(sid, {}).get("open")
        ref = float(px) if px is not None else state.last_close.get(sid, 0.0)
        mv += sh * ref
    return state.cash + mv


def _execute(
    state: _State,
    targets: dict[str, float],
    day: dt.date,
    day_px: dict[str, _Row],
    cost_cfg: CostConfig,
    max_participation: float,
) -> None:
    """在 day 開盤把持倉調到 targets（先賣後買、買單受現金約束）。"""
    pv = _portfolio_value(state, day_px)
    names = set(state.holdings) | set(targets)
    deltas: dict[str, int] = {}
    for sid in names:
        row = day_px.get(sid)
        cur = state.holdings.get(sid, 0)
        if row is None or row.get("open") is None:  # 停牌：凍結
            continue
        w = targets.get(sid, 0.0)
        o = float(row["open"])
        desired = int((w * pv) // (o * _LOT)) * _LOT if (w > 0 and o > 0) else 0
        if desired != cur:
            deltas[sid] = desired - cur

    sells = sorted((s for s in deltas if deltas[s] < 0), key=lambda s: deltas[s])
    buys = sorted(
        (s for s in deltas if deltas[s] > 0), key=lambda s: -deltas[s] * day_px[s]["open"]
    )
    for sid in sells:
        _fill(state, sid, deltas[sid], day, day_px[sid], cost_cfg, max_participation)
    for sid in buys:
        _fill(state, sid, deltas[sid], day, day_px[sid], cost_cfg, max_participation)


def _fill(
    state: _State,
    sid: str,
    delta: int,
    day: dt.date,
    row: _Row,
    cost_cfg: CostConfig,
    max_participation: float,
) -> None:
    side = "buy" if delta > 0 else "sell"
    price = float(row["open"])
    qty = abs(delta)

    # 漲跌停：買在漲停/賣在跌停不成交
    if side == "buy":
        lu = _limit_up(row)
        if lu is not None and price >= lu:
            _unfill(state, sid, side, qty, day, "limit_up")
            return
    else:
        ld = _limit_down(row)
        if ld is not None and price <= ld:
            _unfill(state, sid, side, qty, day, "limit_down")
            return

    # 參與率上限
    cap = _participation_cap_shares(row, price, max_participation)
    if cap is not None and qty > cap:
        _unfill(state, sid, side, qty - cap, day, "participation")
        qty = cap
    if qty <= 0:
        return

    adv = row.get("_adv")
    part = (price * qty) / float(adv) if adv else 0.0

    if side == "buy":
        # 現金約束：逐張縮減直到 現金 ≥ 成交額 + 成本
        while qty > 0:
            c = trade_cost(side="buy", price=price, shares=qty, participation=part, cfg=cost_cfg)
            if price * qty + c.total <= state.cash + 1e-6:
                break
            qty -= _LOT
        if qty <= 0:
            _unfill(state, sid, side, abs(delta), day, "cash")
            return
        c = trade_cost(side="buy", price=price, shares=qty, participation=part, cfg=cost_cfg)
        state.cash -= price * qty + c.total
        state.holdings[sid] = state.holdings.get(sid, 0) + qty
    else:
        c = trade_cost(side="sell", price=price, shares=qty, participation=part, cfg=cost_cfg)
        state.cash += price * qty - c.total
        new = state.holdings.get(sid, 0) - qty
        if new <= 0:
            state.holdings.pop(sid, None)
        else:
            state.holdings[sid] = new

    state.traded_notional += price * qty
    state.ledger.append(
        Trade(
            date=day,
            stock_id=sid,
            side=side,
            shares=qty,
            price=price,
            commission=c.commission,
            tax=c.tax,
            slippage=c.slippage,
        )
    )


def _unfill(state: _State, sid: str, side: str, shares: int, day: dt.date, reason: str) -> None:
    state.unfilled.append(Unfilled(date=day, stock_id=sid, side=side, shares=shares, reason=reason))


def run_backtest(
    strategy_cfg: StrategyConfig,
    cost_cfg: CostConfig,
    start: dt.date,
    end: dt.date,
    warehouse: Warehouse,
    initial_capital: float = 10_000_000.0,
    max_participation: float = 0.05,
) -> BacktestResult:
    """跑回測，回傳 BacktestResult（權益曲線、ledger、unfilled、指標）。"""
    from twquant.data import calendar as cal
    from twquant.strategy.build import build_targets, signal_dates

    calendar = cal.load(warehouse)
    sessions = calendar.between(start, end)
    if not sessions:
        raise ValueError(f"[{start},{end}] 無交易日")
    sig_dates = set(signal_dates(strategy_cfg.rebalance, calendar, start, end))
    by_date = _price_panel(warehouse, start, end)

    state = _State(cash=initial_capital)
    equity_curve: list[EquityPoint] = []
    pending: dict[str, float] | None = None

    for day in sessions:
        day_px = by_date.get(day, {})
        for sid, row in day_px.items():  # 更新最後收盤（停牌沿用）
            if row.get("close") is not None:
                state.last_close[sid] = float(row["close"])

        if pending is not None:
            _execute(state, pending, day, day_px, cost_cfg, max_participation)
            pending = None

        pos_value = sum(sh * state.last_close.get(sid, 0.0) for sid, sh in state.holdings.items())
        equity_curve.append(
            EquityPoint(
                date=day,
                equity=state.cash + pos_value,
                cash=state.cash,
                positions_value=pos_value,
            )
        )

        if day in sig_dates:
            tdf = build_targets(strategy_cfg, warehouse, day)
            pending = {r["stock_id"]: float(r["target_weight"]) for r in tdf.to_dicts()}

    equity_series = [e.equity for e in equity_curve]
    mean_equity = sum(equity_series) / len(equity_series)
    n_days = len(equity_series)
    single_side = state.traded_notional / 2.0
    turnover_ann = (
        (single_side / mean_equity) * (252.0 / n_days) if mean_equity > 0 and n_days else 0.0
    )
    metrics = compute_metrics(equity_series, turnover_ann)
    return BacktestResult(
        equity=equity_curve,
        ledger=state.ledger,
        unfilled=state.unfilled,
        metrics=metrics,
        initial_capital=initial_capital,
        final_equity=equity_series[-1] if equity_series else initial_capital,
    )
