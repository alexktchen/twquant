"""回測純診斷：成本分解、同成交路徑 gross 與持有期。"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass

import polars as pl

from twquant.backtest.engine import BacktestResult
from twquant.backtest.metrics import Metrics, compute_metrics
from twquant.errors import DataError


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


def positions_from_ledger(result: BacktestResult) -> pl.DataFrame:
    """由成交流水重建每個交易日的持倉股數（T-125／SPEC-013 §6.2）。

    `BacktestResult` 只存期末快照 `final_holdings`，歷史部位從未落地，
    但「歷年持股」報表需要逐日的持股清單。唯一的重建來源就是 `ledger`。

    回傳 (date, stock_id, shares)，**只含股數 > 0 的列**：清空當日不留 0 股的列，
    否則持股表會出現一堆空部位。日期取自 `result.equity`（回測實際走過的交易日），
    未成交日的部位由前一日結轉。

    三種情況一律 raise 而非靜默處理（黃金規則 5）：
      * 股數變負——放空未實作，負值只可能是重建邏輯錯誤或流水損壞
      * 成交日不在權益曲線的日期內——靜默略過會讓持股表少掉一整段
      * 未知的 side——買賣以外的字串不得被當成其中之一

    純函數、無 IO：落地由呼叫端負責（黃金規則 10）。
    """
    schema = {"date": pl.Date, "stock_id": pl.String, "shares": pl.Int64}
    dates = [e.date for e in result.equity]
    if not dates:
        return pl.DataFrame(schema=schema)
    valid = set(dates)

    deltas: dict[dt.date, dict[str, int]] = {}
    for t in result.ledger:
        if t.date not in valid:
            raise DataError(
                "backtest_ledger",
                t.date,
                f"成交日 {t.date} 不在權益曲線的日期範圍內"
                f"（{dates[0]} ~ {dates[-1]}），無法重建持倉。",
            )
        if t.side not in ("buy", "sell"):
            raise DataError(
                "backtest_ledger",
                t.date,
                f"未知的 side「{t.side}」（{t.stock_id}），只接受 buy／sell。",
            )
        sign = 1 if t.side == "buy" else -1
        deltas.setdefault(t.date, {})
        deltas[t.date][t.stock_id] = deltas[t.date].get(t.stock_id, 0) + sign * t.shares

    rows: list[dict[str, object]] = []
    held: dict[str, int] = {}
    for d in dates:
        for sid, ds in sorted(deltas.get(d, {}).items()):
            held[sid] = held.get(sid, 0) + ds
            if held[sid] < 0:
                raise DataError(
                    "backtest_ledger",
                    d,
                    f"{sid} 於 {d} 的累計股數為 {held[sid]}（負）。"
                    "放空未實作，負股數代表流水損壞或重建邏輯錯誤。",
                )
        rows.extend(
            {"date": d, "stock_id": sid, "shares": sh} for sid, sh in sorted(held.items()) if sh > 0
        )
    return pl.DataFrame(rows, schema=schema)
