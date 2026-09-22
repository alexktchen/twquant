"""紙上交易的每日一步（D-142，T-280）：開盤成交 → 收盤標記 → 排定明天的單。

執行語意**不是這裡實作的**——它直接呼叫回測引擎公開出來的
`execute_at_open`／`force_exit_on_price_jump`（`backtest.engine`）。
理由寫在 D-142 §二：前向紀錄要能跟 E0 的回測比較，兩邊的整張進位、先賣後買、
參與率上限、漲跌停、跳價防衛與成本扣法就必須是同一份程式，不能是「照抄的一份」。

這裡只多做一件回測沒做的事：**逐筆來回的淨報酬**。回測看的是權益曲線，
而使用者要的是「每一筆平均賺多少」（D-128 §四、D-136）。一檔在 E0 裡可能被加碼、
減碼好幾次才真正出清，所以一筆來回的成本與收入都要跨月累計，出清那天才結算。

純運算：不讀行情、不連 PG、不寫檔。行情與目標名單由呼叫端（CLI）查好傳進來，
落地由 `paper_ledger.append_event` 負責（黃金規則 10 的分工）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from twquant.backtest.engine import ExecutionState, execute_at_open, force_exit_on_price_jump
from twquant.errors import ConfigError, DataError
from twquant.reporting.paper_ledger import (
    ClosedTrade,
    DayRecord,
    FillRow,
    HoldingRow,
    OrderRow,
    PositionState,
    TrackState,
)

if TYPE_CHECKING:
    from twquant.backtest.costs import CostConfig
    from twquant.strategy.config import StrategyConfig

# 單日單股行情列，欄位與 `backtest.engine._price_panel` 產出的完全相同
# （open／close／_prev_close／_adv／limit_up_price／limit_down_price）。
PriceRow = dict[str, Any]


def assert_trackable(cfg: StrategyConfig) -> None:
    """紙上交易只追蹤沒有每日出場規則的策略（D-142 §二：E0 的 `exit_rules` 是空的）。

    這不是懶——帶出場規則的策略要靠引擎的 `_Lot`（進場均價、期間最低收盤）才判得出場，
    而本模組的狀態來自帳本、不重建 lot。與其近似一個會靜默算錯的值，不如擋住。
    """
    if cfg.exit_rules:
        raise ConfigError(
            f"策略「{cfg.name}」帶了 {len(cfg.exit_rules)} 條出場規則，紙上交易帳本不支援："
            "出場判斷需要引擎的持有批次狀態，帳本不重建它。"
            "（D-117／D-141 §五.2 的結論是任何出場規則都輸給不出場，故 E0 本來就沒有）"
        )
    if cfg.rebalance.execution != "next_open":
        raise ConfigError(
            f"策略「{cfg.name}」的成交時點是 {cfg.rebalance.execution}，"
            "紙上交易帳本只實作 next_open（訊號日收盤排單、隔日開盤成交）"
        )


def is_signal_day(
    cfg: StrategyConfig, day: dt.date, sessions_this_month: Sequence[dt.date]
) -> bool:
    """今天是不是再平衡訊號日。

    月頻的定義與 `strategy.build.signal_dates` 相同：當月第一個「日 ≥ day_of_month」的開市日。
    前向執行沒有未來的交易日曆可用，故由呼叫端給出**當月到今天為止**的開市日，
    這裡只看今天是不是其中第一個合格的——與回測逐字同義，且不需要知道未來。
    """
    if cfg.rebalance.schedule != "monthly":
        raise ConfigError(
            f"紙上交易帳本目前只支援月頻再平衡，策略「{cfg.name}」是 {cfg.rebalance.schedule}"
        )
    dom = cfg.rebalance.day_of_month or 1
    qualified = [d for d in sorted(sessions_this_month) if d.day >= dom]
    return bool(qualified) and qualified[0] == day


def _cost_of(fill: FillRow) -> float:
    return fill.commission + fill.tax + fill.slippage


def _apply_fills(
    positions: dict[str, PositionState], fills: Sequence[FillRow], day: dt.date
) -> list[ClosedTrade]:
    """把當日成交套進逐檔的來回帳，回傳這天出清的那些。

    成本基礎含手續費與滑價；賣出收入已扣掉手續費、證交稅與滑價。部分減碼按持股比例
    把成本搬到「已實現」那一側，故加碼、減碼多次之後的報酬仍是整筆來回的金錢加權報酬。
    """
    closed: list[ClosedTrade] = []
    for fill in fills:
        pos = positions.setdefault(fill.stock_id, PositionState())
        if fill.side == "buy":
            if pos.shares == 0:
                pos.opened_on = day
                pos.realized_cost = 0.0
                pos.realized_proceeds = 0.0
                pos.held_sessions = 0
            pos.shares += fill.shares
            pos.cost_basis += fill.price * fill.shares + _cost_of(fill)
            continue
        if fill.shares > pos.shares:
            # 賣超過持有＝帳本與引擎狀態對不上。替它圓過去會讓報酬憑空多一段。
            raise DataError(
                "paper_trading",
                day,
                f"{fill.stock_id} 賣出 {fill.shares} 股但帳上只有 {pos.shares} 股",
            )
        moved = pos.cost_basis * (fill.shares / pos.shares) if pos.shares else 0.0
        pos.cost_basis -= moved
        pos.shares -= fill.shares
        pos.realized_cost += moved
        pos.realized_proceeds += fill.price * fill.shares - _cost_of(fill)
        if pos.shares == 0:
            opened = pos.opened_on or day
            closed.append(
                ClosedTrade(
                    stock_id=fill.stock_id,
                    opened_on=opened,
                    closed_on=day,
                    shares=fill.shares,
                    cost=pos.realized_cost,
                    proceeds=max(0.0, pos.realized_proceeds),
                    net_return=max(0.0, pos.realized_proceeds) / pos.realized_cost - 1.0,
                    held_sessions=pos.held_sessions,
                )
            )
            positions.pop(fill.stock_id, None)
    return closed


def step_day(
    state: TrackState,
    day: dt.date,
    prices: Mapping[str, PriceRow],
    *,
    targets: Sequence[OrderRow] | None,
    cost_cfg: CostConfig,
    max_participation: float,
) -> DayRecord:
    """算出 `day` 這一天要寫進帳本的那一筆。純函數：同樣的輸入永遠給同樣的輸出。

    `targets=None` 代表今天不是訊號日（不排新單）；空序列代表「今天該換股但一檔都沒選出來」
    ——那是合法的空手日，但仍然是一次決策，會以空的 `orders_queued` 記下來。
    """
    if not state.opened:
        raise DataError("paper_trading", day, "這條軌還沒開，不能記交易日")
    positions = {sid: PositionState(**vars(pos)) for sid, pos in state.positions.items()}
    exec_state = ExecutionState(
        cash=state.cash, holdings={sid: pos.shares for sid, pos in positions.items()}
    )
    exec_state.last_close = {sid: pos.last_close for sid, pos in positions.items()}

    # ① 跳價防衛（D-029）：減資、停牌復牌、資料錯誤造成的幻影跳價不得記成損益。
    #    順序與引擎主迴圈相同——先掃收盤跳價，再執行昨天排定的單。
    jump = cost_cfg.data_guard.jump_ratio
    for sid in sorted(exec_state.holdings):
        row = prices.get(sid)
        new_close = None if row is None else row.get("close")
        prev_close = positions[sid].last_close
        if new_close is None or prev_close <= 0:
            continue
        if abs(float(new_close) / prev_close - 1.0) > jump:
            force_exit_on_price_jump(
                exec_state, sid, min(prev_close, float(new_close)), day, cost_cfg
            )

    # ② 昨天排定的單在今天開盤成交（`next_open`）。
    if state.pending:
        execute_at_open(
            exec_state,
            {o.stock_id: o.target_weight for o in state.pending},
            day,
            dict(prices),
            cost_cfg,
            max_participation,
        )

    fills = tuple(
        FillRow(
            stock_id=t.stock_id,
            side=t.side,  # type: ignore[arg-type]
            shares=t.shares,
            price=t.price,
            commission=t.commission,
            tax=t.tax,
            slippage=t.slippage,
            reason=t.reason,
        )
        for t in exec_state.ledger
    )
    closed = _apply_fills(positions, fills, day)

    # ③ 收盤標記。沒有當日行情的持股沿用最後已知收盤，並列進 `stale_marks`——
    #    不得靜默用舊價當今天的價（黃金規則 5）。
    holdings: list[HoldingRow] = []
    stale: list[str] = []
    for sid, shares in sorted(exec_state.holdings.items()):
        pos = positions[sid]
        pos.shares = shares
        row = prices.get(sid)
        close = None if row is None else row.get("close")
        if close is None or float(close) <= 0:
            stale.append(sid)
            mark = pos.last_close
        else:
            mark = float(close)
        if mark <= 0:
            raise DataError("paper_trading", day, f"{sid} 沒有任何可用收盤價，無法標記市值")
        pos.last_close = mark
        pos.held_sessions += 0 if pos.opened_on == day else 1
        holdings.append(
            HoldingRow(
                stock_id=sid,
                shares=shares,
                close=mark,
                cost_basis=pos.cost_basis,
                realized_cost=pos.realized_cost,
                realized_proceeds=pos.realized_proceeds,
                held_sessions=pos.held_sessions,
            )
        )

    equity = exec_state.cash + sum(h.shares * h.close for h in holdings)
    queued = (
        ()
        if targets is None
        else tuple(sorted(targets, key=lambda o: (-o.target_weight, o.stock_id)))
    )
    return DayRecord(
        fills=fills,
        closed=tuple(closed),
        holdings=tuple(holdings),
        orders_queued=queued,
        cash=exec_state.cash,
        equity=equity,
        stale_marks=tuple(stale),
        unfilled=tuple(f"{u.stock_id}:{u.side}:{u.shares}:{u.reason}" for u in exec_state.unfilled),
    )
