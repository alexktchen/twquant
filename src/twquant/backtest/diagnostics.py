"""回測純診斷：成本分解、同成交路徑 gross 與持有期。"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass

from twquant.backtest.engine import BacktestResult
from twquant.backtest.metrics import Metrics, compute_metrics


@dataclass(frozen=True)
class BacktestDiagnostics:
    """不改交易結果、僅由 BacktestResult 推導的診斷摘要。"""

    commission: float
    tax: float
    slippage: float
    total_cost: float
    cost_to_initial: float
    same_path_gross_equity: tuple[float, ...]
    same_path_gross_metrics: Metrics
    average_holding_sessions: float
    trade_count: int
    unfilled_count: int
    unfilled_by_reason: dict[str, int]


def _average_holding_sessions(result: BacktestResult) -> float:
    """以部位 0→正→0 為一輪，計算每輪平均持有交易日數。"""
    if not result.equity or not result.ledger:
        return 0.0
    session_index = {point.date: i for i, point in enumerate(result.equity)}
    positions: dict[str, int] = {}
    entries: dict[str, int] = {}
    durations: list[int] = []

    for trade in result.ledger:
        if trade.date not in session_index:
            raise ValueError(f"ledger 交易日 {trade.date} 不在 equity curve")
        current = positions.get(trade.stock_id, 0)
        if trade.side == "buy":
            if current == 0:
                entries[trade.stock_id] = session_index[trade.date]
            positions[trade.stock_id] = current + trade.shares
            continue
        if trade.side != "sell":
            raise ValueError(f"ledger side 非法：{trade.stock_id} {trade.date} {trade.side}")
        if current < trade.shares:
            raise ValueError(
                f"ledger 賣超持倉：{trade.stock_id} {trade.date}，持有 {current}、賣出 {trade.shares}"
            )
        remaining = current - trade.shares
        if remaining == 0:
            entry = entries.pop(trade.stock_id)
            durations.append(session_index[trade.date] - entry)
            positions.pop(trade.stock_id, None)
        else:
            positions[trade.stock_id] = remaining

    last_index = len(result.equity) - 1
    durations.extend(last_index - entries[sid] for sid in sorted(entries))
    return sum(durations) / len(durations) if durations else 0.0


def summarize_backtest(result: BacktestResult) -> BacktestDiagnostics:
    """彙總成本與同成交路徑 gross；不寫檔、不修改輸入。"""
    commission = sum(trade.commission for trade in result.ledger)
    tax = sum(trade.tax for trade in result.ledger)
    slippage = sum(trade.slippage for trade in result.ledger)
    total_cost = commission + tax + slippage

    daily_cost: dict[dt.date, float] = defaultdict(float)
    for trade in result.ledger:
        daily_cost[trade.date] += trade.commission + trade.tax + trade.slippage
    cumulative = 0.0
    gross: list[float] = []
    for point in result.equity:
        cumulative += daily_cost[point.date]
        gross.append(point.equity + cumulative)

    gross_metrics = compute_metrics(gross, result.metrics.turnover)
    reasons = dict(sorted(Counter(item.reason for item in result.unfilled).items()))
    return BacktestDiagnostics(
        commission=commission,
        tax=tax,
        slippage=slippage,
        total_cost=total_cost,
        cost_to_initial=total_cost / result.initial_capital if result.initial_capital > 0 else 0.0,
        same_path_gross_equity=tuple(gross),
        same_path_gross_metrics=gross_metrics,
        average_holding_sessions=_average_holding_sessions(result),
        trade_count=len(result.ledger),
        unfilled_count=len(result.unfilled),
        unfilled_by_reason=reasons,
    )
