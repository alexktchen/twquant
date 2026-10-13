"""名單績效（D-176）：每條線照公布的名單自動操作的模擬帳戶（A），以及每一檔新進名單的逐筆結果（B）。

名單由系統照固定規則全部當成買了、事後結算，使用者不能挑——這是名單的成績，不是個人損益
（個人損益是另一個功能「我的持股」）。這裡只做純運算：把回測結果、逐筆結果與名單的公布時間
整理成網站要的 JSON。讀資料庫與寫入在 CLI 邊界。

**即時與事後補分開**：名單在下一個交易日開盤前寫進資料庫才算「即時公布」；之後才補的
（網站上線前回頭補的那段）照算，但標成事後補，數字分開列，可信度才不會被高估。
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from pydantic import Field

from twquant.backtest.engine import EquityPoint, Holding
from twquant.backtest.list_trades import EntryTrade
from twquant.backtest.metrics import compute_metrics
from twquant.config import BaseConfig
from twquant.conventions import TZ

if TYPE_CHECKING:
    from twquant.backtest.costs import CostConfig
    from twquant.data.serving.scorecard_store import PublishedPick
    from twquant.data.warehouse import TableSource
    from twquant.reporting.pick_board import BoardStrategy
    from twquant.strategy.config import StrategyConfig

_ONE_YEAR_SESSIONS = 252  # 不滿一年的年化只是換算，頁面要標出來


class ScorecardConfig(BaseConfig):
    """configs/scorecard.yaml（D-176 事前登記：規則寫死，之後不改）。"""

    start: dt.date  # 帳戶開戶日＝第一份名單那天；之前空手
    initial_capital: float = Field(gt=0.0)
    lot_size: int = Field(ge=1)
    max_participation: float = Field(gt=0.0, le=1.0)
    warmup_days: int = Field(ge=1)  # 開戶日前多讀幾個日曆日的行情（前收、20 日成交額中位數）
    benchmark: str = Field(min_length=1)  # 對照：同期買進抱著（含息）
    live_cutoff: dt.time  # 名單在下一個交易日這個時刻之前寫好＝即時公布
    hold_sessions: int = Field(ge=1)  # 逐筆：買進後抱幾個交易日
    big_loss: float = Field(lt=0.0)  # 逐筆：虧超過這個比例算「大虧」


def _deadline(sessions: Sequence[dt.date], i: int, cutoff: dt.time) -> dt.datetime:
    """第 i 個交易日的名單最晚要在什麼時候寫好：下一個交易日的 cutoff（最後一天用下一個平日）。"""
    if i + 1 < len(sessions):
        nxt = sessions[i + 1]
    else:
        nxt = sessions[i] + dt.timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += dt.timedelta(days=1)
    return dt.datetime.combine(nxt, cutoff, tzinfo=TZ)


def publication_flags(
    written: Mapping[dt.date, dt.datetime],
    day_written: Mapping[dt.date, dt.datetime],
    sessions: Sequence[dt.date],
    cutoff: dt.time,
    *,
    allow_empty: bool,
) -> dict[dt.date, bool]:
    """每個有公布的交易日 → 是不是即時公布（True）還是事後補（False）。沒公布的日子不在結果裡。

    `written`＝這條線那天名單的寫入時間（有股票的日子）；`day_written`＝那天任何一條線的寫入時間。
    允許空名單的線（高分爆量）沒股票的日子沒有自己的寫入時間：看當天其他線，**而且這條線前一個有股票的
    日子要是即時的**——否則無法證明這條線當時已經在公布（例如這條線是後來才加上、整段回頭補的）。
    """
    for when in (*written.values(), *day_written.values()):
        if when.tzinfo is None:
            raise ValueError(f"名單寫入時間必須帶時區：{when}")
    out: dict[dt.date, bool] = {}
    last_live = False
    for i, day in enumerate(sessions):
        deadline = _deadline(sessions, i, cutoff)
        if day in written:
            last_live = written[day] < deadline
            out[day] = last_live
        elif allow_empty and day in day_written:
            out[day] = last_live and day_written[day] < deadline
    return out


def live_since(flags: Mapping[dt.date, bool]) -> dt.date | None:
    """從哪一天起之後每一份名單都是即時公布；最後一份就不是即時 → None。"""
    since: dt.date | None = None
    for day in sorted(flags, reverse=True):
        if not flags[day]:
            break
        since = day
    return since


def trade_stats(rets: Sequence[float], *, big_loss: float) -> dict[str, Any]:
    """已賣出的逐筆：筆數、平均、中位數、賺錢比例、大虧比例、最好與最差。沒有筆數時數字是 None（不是 0）。"""
    n = len(rets)
    if n == 0:
        keys = ("mean", "median", "win_rate", "big_loss_rate", "best", "worst")
        return {"n": 0, **dict.fromkeys(keys)}
    return {
        "n": n,
        "mean": sum(rets) / n,
        "median": statistics.median(rets),
        "win_rate": sum(1 for r in rets if r > 0.0) / n,
        "big_loss_rate": sum(1 for r in rets if r <= big_loss) / n,
        "best": max(rets),
        "worst": min(rets),
    }


def nav_summary(values: Sequence[float], bench: Sequence[float] | None) -> dict[str, Any]:
    """一段淨值的報酬、年化（不滿一年標 short）、最大回撤；有對照就附同一段的對照報酬與回撤。"""
    if len(values) < 1 or values[0] <= 0.0:
        raise ValueError("淨值序列是空的或起點 ≤ 0")
    m = compute_metrics(list(values), 0.0)
    out: dict[str, Any] = {
        "n_days": len(values),
        "ret": values[-1] / values[0] - 1.0,
        "cagr": m.cagr,
        "mdd": m.mdd,
        "short": len(values) < _ONE_YEAR_SESSIONS,
        "bench_ret": None,
        "bench_mdd": None,
    }
    if bench is not None:
        if len(bench) != len(values):
            raise ValueError(f"對照長度 {len(bench)} 與淨值 {len(values)} 不同")
        bm = compute_metrics(list(bench), 0.0)
        out["bench_ret"] = bench[-1] / bench[0] - 1.0
        out["bench_mdd"] = bm.mdd
    return out


def _iso(d: dt.date | None) -> str | None:
    return None if d is None else d.isoformat()


def _r(x: float | None, digits: int) -> float | None:
    """縮短 JSON（逐筆每條線上千列）；None 照樣是 None。"""
    return None if x is None else round(x, digits)


def trade_rows(
    trades: Sequence[EntryTrade], flags: Mapping[dt.date, bool], names: Mapping[str, str]
) -> list[dict[str, Any]]:
    """逐筆明細（新到舊）。`live`＝那份名單是不是即時公布。"""
    rows = [
        {
            "signal": t.signal_date.isoformat(),
            "sid": t.stock_id,
            "name": names.get(t.stock_id),
            "status": t.status,
            "entry_date": _iso(t.entry_date),
            "entry_price": _r(t.entry_price, 4),
            "exit_date": _iso(t.exit_date),
            "exit_price": _r(t.exit_price, 4),
            "held": t.sessions_held,
            "ret": _r(t.ret, 6),
            "reason": t.unfilled_reason or t.exit_reason,
            "live": bool(flags.get(t.signal_date, False)),
        }
        for t in trades
    ]
    return sorted(rows, key=lambda r: (r["signal"], r["sid"]), reverse=True)


def trade_summary(
    trades: Sequence[EntryTrade], flags: Mapping[dt.date, bool], *, big_loss: float
) -> dict[str, Any]:
    """逐筆統計：全部、事後補、即時三段（只算已賣出），加上還抱著／買不到／明天才買的筆數。"""
    closed = [
        (bool(flags.get(t.signal_date, False)), t.ret)
        for t in trades
        if t.status == "closed" and t.ret is not None
    ]

    def _rets(live: bool | None) -> list[float]:
        return [r for is_live, r in closed if live is None or is_live == live]

    return {
        "all": trade_stats(_rets(None), big_loss=big_loss),
        "backfill": trade_stats(_rets(False), big_loss=big_loss),
        "live": trade_stats(_rets(True), big_loss=big_loss),
        "open": sum(1 for t in trades if t.status == "open"),
        "unfilled": sum(1 for t in trades if t.status == "unfilled"),
        "pending": sum(1 for t in trades if t.status == "pending"),
    }


def line_payload(
    *,
    code: str,
    name: str,
    kind: str,
    slots: int,
    summary: str,
    cfg: ScorecardConfig,
    as_of: dt.date,
    generated_at: dt.datetime,
    equity: Sequence[EquityPoint],
    bench: Sequence[float] | None,
    bench_error: str | None,
    flags: Mapping[dt.date, bool],
    gaps: Sequence[dt.date],
    holdings: Sequence[Holding],
    trades: Sequence[EntryTrade],
    names: Mapping[str, str],
    costs: Mapping[str, float],
) -> dict[str, Any]:
    """一條線的名單績效 JSON（網站 `/api/scorecard?line=` 原樣回傳）。"""
    if not equity:
        raise ValueError(f"{code}：沒有淨值")
    values = [p.equity for p in equity]
    if bench is not None and len(bench) != len(values):
        raise ValueError(f"{code}：對照 {len(bench)} 天、淨值 {len(values)} 天，沒對齊")
    since = live_since(flags)
    live_part = None
    if since is not None:
        i = next(k for k, p in enumerate(equity) if p.date >= since)
        live_part = nav_summary(values[i:], bench[i:] if bench is not None else None)
        live_part["start"] = equity[i].date.isoformat()
    whole = nav_summary(values, bench) | {"start": equity[0].date.isoformat()}
    last = equity[-1]
    return {
        "code": code,
        "name": name,
        "kind": kind,
        "slots": slots,
        "summary": summary,
        "as_of": as_of.isoformat(),
        "generated_at": generated_at.isoformat(),
        "start": cfg.start.isoformat(),
        "initial_capital": cfg.initial_capital,
        "live_since": _iso(since),
        "benchmark": {"code": cfg.benchmark, "error": bench_error},
        "nav": [
            {
                "d": p.date.isoformat(),
                "v": round(p.equity, 2),
                "b": None if bench is None else round(bench[k], 2),
                "x": round(p.positions_value / p.equity, 4) if p.equity > 0 else None,
            }
            for k, p in enumerate(equity)
        ],
        "account": {
            "all": whole,
            "live": live_part,
            "equity": last.equity,
            "cash": last.cash,
            "holdings": [
                {
                    "sid": h.stock_id,
                    "name": names.get(h.stock_id),
                    "entry_date": _iso(h.entry_date),
                    "weight": _r(h.weight, 6),
                    "ret": _r(h.ret, 6),
                }
                for h in holdings
            ],
        },
        "gaps": [d.isoformat() for d in gaps],
        "trades": {
            "hold_sessions": cfg.hold_sessions,
            "big_loss": cfg.big_loss,
            "notional": cfg.initial_capital / slots,
            "costs": dict(costs),
            "stats": trade_summary(trades, flags, big_loss=cfg.big_loss),
            "rows": trade_rows(trades, flags, names),
        },
    }


def benchmark_values(
    source: TableSource, code: str, sessions: Sequence[dt.date], initial_capital: float
) -> tuple[list[float] | None, str | None]:
    """對照（0050）同期買進抱著的價值，含息、分割還原，對齊帳戶的每個交易日。

    算不出來（雲端沒有它的行情、事件表沒有它的股利）→ (None, 原因)，頁面照原因顯示。
    **不退回未含息**：帳戶有收股利，對照不含息會把帳戶相對表現說得比實際好。
    """
    import polars as pl

    from twquant.errors import DataError
    from twquant.reporting.benchmarks import align_to, benchmark_series, cumulative_factors

    try:
        prices = (
            source.scan("prices_daily")
            .filter(pl.col("stock_id") == code)
            .select("date", "stock_id", "close")
            .collect()
        )
        events = (
            source.scan("corporate_events")
            .filter(pl.col("stock_id") == code)
            .select("date", "stock_id", "event_type", "adj_factor")
            .collect()
        )
        series = benchmark_series(cumulative_factors(prices, events), code, total_return=True)
        values = align_to(
            series, list(sessions), name=code, event_dates=set(events["date"].to_list())
        )
    except DataError as exc:
        return None, exc.detail
    base = values[0]
    return [initial_capital * v / base for v in values], None


def build_scorecards(
    *,
    source: TableSource,
    published: Sequence[PublishedPick],
    names: Mapping[str, str],
    lines: Sequence[tuple[BoardStrategy, StrategyConfig]],
    cfg: ScorecardConfig,
    cost: CostConfig,
    as_of: dt.date,
    generated_at: dt.datetime,
) -> list[dict[str, Any]]:
    """每條線一份名單績效：照公布的名單重播帳戶（A）＋每一檔新進名單的逐筆（B）。

    `source` 只要有行情、除權息（讀取下界涵蓋開戶日前 `warmup_days`）；`published` 是
    [cfg.start, as_of] 的 `daily_picks`。不重算任何模型分數。
    """
    from twquant.backtest.engine import (
        dividends_by_date,
        price_panel,
        run_backtest,
        splits_by_date,
    )
    from twquant.backtest.list_trades import new_entries, simulate_entries
    from twquant.data import calendar as cal
    from twquant.errors import DataError

    sessions = cal.load(source).between(cfg.start, as_of)
    if not sessions or sessions[-1] != as_of:
        raise DataError("prices_daily", as_of, "名單績效算到這天，但這天沒有行情")
    day_written: dict[dt.date, dt.datetime] = {}
    for p in published:
        day_written[p.date] = max(day_written.get(p.date, p.created_at), p.created_at)
    panel = price_panel(source, cfg.start, as_of)
    divs = dividends_by_date(source, cfg.start, as_of)
    splits = splits_by_date(source, cfg.start, as_of)
    bench, bench_error = benchmark_values(source, cfg.benchmark, sessions, cfg.initial_capital)
    costs = {
        "commission": cost.commission_rate * cost.commission_discount,
        "tax": cost.tax_rate_sell,
    }
    out: list[dict[str, Any]] = []
    for line, strat in lines:
        mine = [p for p in published if p.line == line.code]
        picks: dict[dt.date, list[str]] = {}
        written: dict[dt.date, dt.datetime] = {}
        for p in mine:  # 已依日期、名次排好
            picks.setdefault(p.date, []).append(p.stock_id)
            written[p.date] = max(written.get(p.date, p.created_at), p.created_at)
        flags = publication_flags(
            written, day_written, sessions, cfg.live_cutoff, allow_empty=line.allow_empty
        )
        # 有公布的日子（含允許的空名單）才有名單；沒公布的日子補位型沒有候選、月換股型遇到換股日 raise
        lists = {d: picks.get(d, []) for d in flags}
        slots = strat.slots.n if strat.slots is not None else strat.selection.top_n
        if slots is None:
            raise DataError("scorecard", as_of, f"{line.code}：名單要固定檔數（selection.top_n）")
        res = run_backtest(
            strat,
            cost,
            cfg.start,
            as_of,
            source,
            initial_capital=cfg.initial_capital,
            max_participation=cfg.max_participation,
            lot_size=cfg.lot_size,
            picks=lists,
        )
        trades = simulate_entries(
            new_entries(lists, sessions),
            sessions,
            panel,
            divs,
            splits,
            cost,
            hold_sessions=cfg.hold_sessions,
            notional=cfg.initial_capital / slots,
            max_participation=cfg.max_participation,
            lot=cfg.lot_size,
        )
        out.append(
            line_payload(
                code=line.code,
                name=line.name,
                kind="slots" if strat.slots is not None else "rebalance",
                slots=slots,
                summary=line.summary,
                cfg=cfg,
                as_of=as_of,
                generated_at=generated_at,
                equity=res.equity,
                bench=bench,
                bench_error=bench_error,
                flags=flags,
                gaps=[d for d in sessions if d not in flags],
                holdings=res.final_holdings,
                trades=trades,
                names=names,
                costs=costs,
            )
        )
    return out
