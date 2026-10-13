"""成績單逐筆（D-176 §二）：名單每一檔新進都算一筆——隔天開盤買、抱滿 N 個交易日、再隔天開盤賣。

每筆各自獨立，不受帳戶位子與現金限制；成交語意照引擎：開盤漲停買不到、跌停賣不掉（隔天再試）、
參與率上限、跳價防衛（D-029）、手續費＋證交稅＋滑價（`costs.yaml`）、除息現金入帳、分割調股數。
每天的順序也照引擎的迴圈：除息 → 分割 → 收盤（跳價強制出場、持有天數 +1）→ 開盤成交 → 收盤後判斷出場。
純運算：不讀資料庫、不寫檔。
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from twquant.backtest.costs import CostConfig, trade_cost
from twquant.backtest.engine import limit_down_of, limit_up_of
from twquant.backtest.fill import decide_fill

_Panel = Mapping[dt.date, Mapping[str, Mapping[str, Any]]]


@dataclass(frozen=True)
class EntryTrade:
    """一筆新進名單的結果。`status`：closed 已賣出／open 還抱著／unfilled 買不到／pending 明天才買。"""

    signal_date: dt.date  # 名單公布的那天（收盤後）
    stock_id: str
    status: str
    entry_date: dt.date | None = None
    entry_price: float | None = None
    shares: int = 0  # 買進股數（分割前）
    exit_date: dt.date | None = None  # 最後一批賣出的日子
    exit_price: float | None = None  # 賣出均價；持有中＝最新收盤
    exit_reason: str | None = None  # time_stop｜price_jump
    sessions_held: int = 0  # 買進後經過的交易日數（買進日＝0，同引擎）；已賣出＝決定賣出那晚的天數
    dividends: float = 0.0  # 收到的股利現金
    cost: float = 0.0  # 手續費＋證交稅＋滑價合計；持有中含預估的賣出成本
    ret: float | None = None  # 扣成本、含股利；持有中＝以最新收盤估
    unfilled_reason: str | None = None  # suspended｜price_jump｜lot｜limit_up｜participation


def new_entries(
    lists: Mapping[dt.date, Sequence[str]], sessions: Sequence[dt.date]
) -> list[tuple[dt.date, str]]:
    """每個交易日名單裡「上一份名單沒有」的代號，依名次。

    `lists` 有這天的鍵＝那天有公布（空名單也算公布）；沒有鍵＝那天沒公布，不產生新進、
    下一天跟最近一份有公布的名單比——否則漏一天，隔天整份名單都會被算成新進。第一份名單全部是新進。
    """
    out: list[tuple[dt.date, str]] = []
    prev: set[str] = set()
    for day in sessions:
        today = lists.get(day)
        if today is None:
            continue
        out.extend((day, sid) for sid in today if sid not in prev)
        prev = set(today)
    return out


def simulate_entries(
    entries: Sequence[tuple[dt.date, str]],
    sessions: Sequence[dt.date],
    panel: _Panel,
    dividends: Mapping[dt.date, Sequence[tuple[str, float, str]]],
    splits: Mapping[dt.date, Sequence[tuple[str, float]]],
    cost_cfg: CostConfig,
    *,
    hold_sessions: int,
    notional: float,
    max_participation: float,
    lot: int,
) -> list[EntryTrade]:
    """逐筆模擬。`panel` 是引擎的價格面板（`price_panel`），`dividends`／`splits` 是引擎的事件表。"""
    if hold_sessions < 1:
        raise ValueError(f"hold_sessions 須 ≥ 1，收到 {hold_sessions}")
    if notional <= 0.0:
        raise ValueError(f"每筆金額須 > 0，收到 {notional}")
    index = {d: i for i, d in enumerate(sessions)}
    out: list[EntryTrade] = []
    for signal, sid in entries:
        if signal not in index:
            raise ValueError(f"{signal} 不是交易日，無法決定隔天開盤")
        out.append(
            _one(
                signal,
                sid,
                sessions[index[signal] + 1 :],
                panel,
                dividends,
                splits,
                cost_cfg,
                hold_sessions,
                notional,
                max_participation,
                lot,
            )
        )
    return out


def _one(
    signal: dt.date,
    sid: str,
    after: Sequence[dt.date],
    panel: _Panel,
    dividends: Mapping[dt.date, Sequence[tuple[str, float, str]]],
    splits: Mapping[dt.date, Sequence[tuple[str, float]]],
    cfg: CostConfig,
    hold: int,
    notional: float,
    max_participation: float,
    lot: int,
) -> EntryTrade:
    if not after:
        return EntryTrade(signal, sid, "pending")
    entry = after[0]
    row = panel.get(entry, {}).get(sid)
    if row is None or row.get("open") is None:
        return EntryTrade(signal, sid, "unfilled", entry_date=entry, unfilled_reason="suspended")
    price = float(row["open"])
    jump = cfg.data_guard.jump_ratio
    prev = row.get("_prev_close")
    if prev and abs(price / float(prev) - 1.0) > jump:
        return EntryTrade(signal, sid, "unfilled", entry_date=entry, unfilled_reason="price_jump")
    want = int(notional // (price * lot)) * lot if price > 0 else 0
    if want == 0:
        return EntryTrade(signal, sid, "unfilled", entry_date=entry, unfilled_reason="lot")
    buy = decide_fill(
        side="buy",
        price=price,
        shares=want,
        limit_up=limit_up_of(dict(row)),
        limit_down=limit_down_of(dict(row)),
        adv=float(row["_adv"]) if row.get("_adv") else None,
        max_participation=max_participation,
        available_cash=math.inf,  # 每筆各自獨立，不受帳戶現金限制
        cost_cfg=cfg,
        lot=lot,
    )
    if buy.filled_shares <= 0 or buy.cost is None:
        why = buy.unfilled[0][1] if buy.unfilled else "participation"
        return EntryTrade(signal, sid, "unfilled", entry_date=entry, unfilled_reason=why)

    bought = shares = buy.filled_shares
    outlay = price * bought + buy.cost.total
    cost = buy.cost.total
    close = row.get("close")
    last_close = float(close) if close is not None else (float(prev) if prev else None)
    held = 0
    night_held = 0  # 前一晚收盤後的持有天數＝決定賣出時看到的數字
    divs = 0.0
    proceeds = 0.0
    sold = 0
    exit_day: dt.date | None = None
    flagged = False
    reason: str | None = None
    for day in after[1:]:
        for s, adj, _ in dividends.get(day, ()):
            if s == sid and last_close is not None and last_close > 0.0:
                divs += last_close * (1.0 - adj) * shares
        for s, adj in splits.get(day, ()):
            if s == sid and adj > 0.0:
                shares = round(shares / adj)
                last_close = last_close * adj if last_close is not None else None
        r = panel.get(day, {}).get(sid)
        has_close = r is not None and r.get("close") is not None
        if r is not None and has_close:
            c = float(r["close"])
            if last_close is not None and last_close > 0.0 and abs(c / last_close - 1.0) > jump:
                # D-029：跳價視為資料事件，以 min(跳價前, 跳價後) 全數出場（同引擎 `_forced_exit`）
                px = min(last_close, c)
                tc = trade_cost(side="sell", price=px, shares=shares, participation=0.0, cfg=cfg)
                proceeds += px * shares
                cost += tc.total
                sold += shares
                shares = 0
                exit_day, reason = day, "price_jump"
                break
            held += 1
            last_close = c
        if flagged and r is not None and r.get("open") is not None:
            o = float(r["open"])
            p = r.get("_prev_close")
            if not (p and abs(o / float(p) - 1.0) > jump):  # 開盤跳價：同引擎不成交，隔天再試
                sell = decide_fill(
                    side="sell",
                    price=o,
                    shares=shares,
                    limit_up=limit_up_of(dict(r)),
                    limit_down=limit_down_of(dict(r)),
                    adv=float(r["_adv"]) if r.get("_adv") else None,
                    max_participation=max_participation,
                    available_cash=0.0,
                    cost_cfg=cfg,
                    lot=lot,
                )
                if sell.filled_shares > 0 and sell.cost is not None:
                    proceeds += o * sell.filled_shares
                    cost += sell.cost.total
                    sold += sell.filled_shares
                    shares -= sell.filled_shares
                    exit_day = day
                    if shares == 0:
                        reason = "time_stop"
                        break
        # 收盤後判斷：停牌（沒收盤）那天不判斷，同引擎 `exit_reasons`
        flagged = has_close and held >= hold
        night_held = held

    held_trade = EntryTrade(
        signal,
        sid,
        "open",
        entry_date=entry,
        entry_price=price,
        shares=bought,
        sessions_held=held,
        dividends=divs,
    )
    if shares == 0:
        return replace(
            held_trade,
            status="closed",
            sessions_held=night_held,
            exit_date=exit_day,
            exit_price=proceeds / sold,
            exit_reason=reason,
            cost=cost,
            ret=(proceeds - (cost - buy.cost.total) + divs) / outlay - 1.0,
        )
    # 還抱著：以最新收盤估，扣掉預估的賣出成本（已賣掉的部分照實算）
    mark = last_close if last_close is not None else price
    est = trade_cost(side="sell", price=mark, shares=shares, participation=0.0, cfg=cfg).total
    value = proceeds + mark * shares
    return replace(
        held_trade,
        exit_date=exit_day,
        exit_price=mark,
        cost=cost + est,
        ret=(value - (cost + est - buy.cost.total) + divs) / outlay - 1.0,
    )
