"""回測純診斷：成本分解、同成交路徑 gross 與持有期。"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import pairwise

import polars as pl

from twquant.backtest.engine import BacktestResult, Trade
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


@dataclass(frozen=True)
class RoundTripStats:
    """逐筆（round-trip）損益統計（T-133）。

    **與 `Metrics.win_rate` 是不同的東西**：後者是**日勝率**（報酬為正的交易日比例）。
    右尾型策略常見「賠得比較多次、但賺得大筆」，兩個勝率可以差十個百分點以上，
    混用會得到相反的印象（D-061 明列此為必須分開呈現的項目）。
    """

    n_closed: int  # 已平倉趟數
    n_open: int  # 期末仍持有的部位數（不計入已實現統計）
    win_rate: float  # 已平倉中獲利的比例
    profit_factor: float | None  # 總獲利 ÷ 總虧損（絕對值）；無虧損時為 None
    avg_win: float  # 獲利趟的平均報酬率
    avg_loss: float  # 虧損趟的平均報酬率（≤0）
    total_pnl: float  # 已實現損益（TWD，已含兩邊費用）


def round_trip_stats(ledger: list[Trade]) -> RoundTripStats:
    """由成交流水算逐筆損益。一趟 ＝ 部位由 0 建立到回到 0 的完整循環。

    分批出場仍屬**同一趟**（部位沒歸零就還沒結束）；清空後再進場是**新的一趟**。
    期末未平倉者不計入已實現統計，但回報筆數——把它當成平倉會虛報勝率。

    兩邊的費用都計：買進費用加進成本、賣出費用從價款扣除。只扣一邊會高估報酬。

    純函數、無 IO（黃金規則 10）。
    """
    pos: dict[str, int] = {}
    cost: dict[str, float] = {}
    proceeds: dict[str, float] = {}
    closed: list[tuple[float, float]] = []  # (損益, 報酬率)

    for t in sorted(ledger, key=lambda x: (x.date, x.stock_id, x.side)):
        fees = t.commission + t.tax + t.slippage
        if t.side == "buy":
            pos[t.stock_id] = pos.get(t.stock_id, 0) + t.shares
            cost[t.stock_id] = cost.get(t.stock_id, 0.0) + t.shares * t.price + fees
        elif t.side == "sell":
            pos[t.stock_id] = pos.get(t.stock_id, 0) - t.shares
            proceeds[t.stock_id] = proceeds.get(t.stock_id, 0.0) + t.shares * t.price - fees
            if pos[t.stock_id] < 0:
                raise DataError(
                    "backtest_ledger",
                    t.date,
                    f"{t.stock_id} 於 {t.date} 的累計股數為 {pos[t.stock_id]}（負）。"
                    "放空未實作，負部位代表流水損壞。",
                )
            if pos[t.stock_id] == 0:
                c = cost.pop(t.stock_id, 0.0)
                p = proceeds.pop(t.stock_id, 0.0)
                closed.append((p - c, (p - c) / c if c > 0 else 0.0))
        else:
            raise DataError("backtest_ledger", t.date, f"未知的 side「{t.side}」（{t.stock_id}）。")

    n_open = sum(1 for v in pos.values() if v > 0)
    if not closed:
        return RoundTripStats(0, n_open, 0.0, None, 0.0, 0.0, 0.0)
    wins = [r for _, r in closed if r > 0]
    losses = [r for _, r in closed if r <= 0]
    gross_win = sum(p for p, _ in closed if p > 0)
    gross_loss = -sum(p for p, _ in closed if p <= 0)
    return RoundTripStats(
        n_closed=len(closed),
        n_open=n_open,
        win_rate=len(wins) / len(closed),
        # 無虧損時分母為 0：回傳 None 而非 0——0 會被讀成「全賠」，是反向的誤導
        profit_factor=(gross_win / gross_loss) if gross_loss > 0 else None,
        avg_win=sum(wins) / len(wins) if wins else 0.0,
        avg_loss=sum(losses) / len(losses) if losses else 0.0,
        total_pnl=sum(p for p, _ in closed),
    )


@dataclass(frozen=True)
class HoldingBucket:
    """一段持有期區間內的逐筆統計（T-134）。`lo`／`hi` 為曆日、含端點。"""

    lo: int
    hi: int
    n: int
    share: float  # 佔已平倉趟數的比例
    win_rate: float
    avg_win: float
    avg_loss: float
    expectancy: float  # 每筆平均報酬率（含兩邊費用）
    pnl: float  # 該桶已實現損益（TWD，帶號）——永遠可讀，不受分母問題影響
    pnl_share: float  # pnl ÷ 全部已實現損益；各桶加總為 1


def holding_buckets(ledger: list[Trade], *, edges: tuple[int, ...]) -> list[HoldingBucket]:
    """把已平倉的 round trip 依**持有曆日**分桶，回報每桶的期望值與損益佔比。

    這個診斷回答的是「**策略靠什麼賺錢**」，不是「該抱多久」。

    **不得讀成「抱久一點比較好」。** 部位之所以活過下一次調倉，正是因為它繼續得高分，
    而繼續得高分與股價上漲高度相關——「抱久的比較賺」有一大部分只是
    「賺的才被抱久」的同義反覆。D-042 已經為這個混淆付過代價：當時依落後 IC 推論
    「拉長持有能保住 alpha」，實作 banding 後周轉降 47% 但**毛報酬由 +4.49% 翻成 −3.03%**。
    該裁決的結論是：落後 IC 支持的是「降低再平衡頻率」（抱著原本的選股），
    **不是**「遲滯」（留住名次已衰退的股票）。

    `pnl_share` 是本函數的重點：它直接量出集中度脆弱性——若少數趟數貢獻多數獲利，
    把那幾趟拿掉策略就垮了，而那不是勝率能反映的。

    `edges` 由呼叫端給定（桶界是呈現參數，不寫死在此，黃金規則 8）。
    區間為**左閉右開** `[edges[i], edges[i+1])`，最後一桶右端閉合——
    全用閉區間會讓恰好落在邊界的趟數同時算進兩個桶，筆數加總就不等於總趟數了。

    空桶保留並回報 0，不得悄悄消失——那會讓讀者以為該區間不存在，而非沒有樣本。

    `pnl_share` 的分母是**帶號的**總損益，故各桶加總為 1；總損益為負時符號隨之翻轉
    （該桶「貢獻了多少虧損」）。總損益接近 0 時此比值不穩定，故 `pnl`（TWD）並列，
    它永遠可讀。
    """
    if list(edges) != sorted(edges) or len(set(edges)) != len(edges):
        raise ValueError(f"edges 必須嚴格遞增且不重複，得到 {edges}")

    pos: dict[str, int] = {}
    cost: dict[str, float] = {}
    proc: dict[str, float] = {}
    start: dict[str, dt.date] = {}
    trips: list[tuple[int, float, float]] = []  # (持有曆日, 報酬率, 損益)

    for t in sorted(ledger, key=lambda x: (x.date, x.stock_id, x.side)):
        fees = t.commission + t.tax + t.slippage
        if t.side == "buy":
            if pos.get(t.stock_id, 0) == 0:
                start[t.stock_id] = t.date
            pos[t.stock_id] = pos.get(t.stock_id, 0) + t.shares
            cost[t.stock_id] = cost.get(t.stock_id, 0.0) + t.shares * t.price + fees
        elif t.side == "sell":
            pos[t.stock_id] = pos.get(t.stock_id, 0) - t.shares
            proc[t.stock_id] = proc.get(t.stock_id, 0.0) + t.shares * t.price - fees
            if pos[t.stock_id] < 0:
                raise DataError(
                    "backtest_ledger",
                    t.date,
                    f"{t.stock_id} 於 {t.date} 的累計股數為 {pos[t.stock_id]}（負）。",
                )
            if pos[t.stock_id] == 0:
                c = cost.pop(t.stock_id, 0.0)
                p = proc.pop(t.stock_id, 0.0)
                held = (t.date - start[t.stock_id]).days
                trips.append((held, (p - c) / c if c > 0 else 0.0, p - c))
        else:
            raise DataError("backtest_ledger", t.date, f"未知的 side「{t.side}」。")

    total_n = len(trips)
    total_pnl = sum(p for _, _, p in trips)
    out: list[HoldingBucket] = []
    n_edges = len(edges)
    for i, (lo, hi) in enumerate(pairwise(edges)):
        last = i == n_edges - 2
        g = (
            [x for x in trips if lo <= x[0] <= hi]
            if last
            else [x for x in trips if lo <= x[0] < hi]
        )
        if not g:
            out.append(HoldingBucket(lo, hi, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0))
            continue
        wins = [r for _, r, _ in g if r > 0]
        losses = [r for _, r, _ in g if r <= 0]
        out.append(
            HoldingBucket(
                lo=lo,
                hi=hi,
                n=len(g),
                share=len(g) / total_n if total_n else 0.0,
                win_rate=len(wins) / len(g),
                avg_win=sum(wins) / len(wins) if wins else 0.0,
                avg_loss=sum(losses) / len(losses) if losses else 0.0,
                expectancy=sum(r for _, r, _ in g) / len(g),
                pnl=sum(p for _, _, p in g),
                pnl_share=(sum(p for _, _, p in g) / total_pnl) if total_pnl != 0 else 0.0,
            )
        )
    return out
