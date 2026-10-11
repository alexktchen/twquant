"""每日選股看板的資金流旗標（SPEC-016 §7，T-341）：純函數。**監控、未回測**——不是交易訊號。

每檔股票（看板上出現的代號）在看板日的：
- `pressure`：`chip.etf_flow_pressure_5d`（5 日 ETF 機械流量 ÷ ADV）；
- `disc_streak`：`chip.trust_disc_streak`（投信扣掉 ETF 機械流量後的連買天數）；
- `events`：最近 `recent_sessions` 個交易日內發生過的 E1～E8（SPEC-016 §4；不含延遲對齊的描述列）；
- `active_etf`：主動式 ETF 在最近兩份持股之間對它的增減（股）。主動式 ETF 只有 2025-05 起的資料。

沒資料就是 None（頁面顯示「無資料」），不是 0（AC-24）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from typing import Any

import polars as pl

NOTE = "監控、未回測（SPEC-016）"


def active_changes(holdings: pl.DataFrame, active_ids: Iterable[str]) -> pl.DataFrame:
    """主動式 ETF 最近兩份持股之間的增減：(etf_id, stock_id, shares_delta, base, prev)。

    只有一份清單的 ETF 不產生列（沒有「前一份」可比，不是 0）。前一份有、這一份沒有 → 全數減持。
    """
    h = holdings.filter(pl.col("etf_id").is_in(sorted(set(active_ids))))
    rows: list[tuple[Any, ...]] = []
    for (etf_id,), g in h.group_by(["etf_id"], maintain_order=True):
        dates = sorted(set(g["date"].to_list()))
        if len(dates) < 2:
            continue
        prev, base = dates[-2], dates[-1]
        a = dict(g.filter(pl.col("date") == prev).select("stock_id", "shares").iter_rows())
        b = dict(g.filter(pl.col("date") == base).select("stock_id", "shares").iter_rows())
        for sid in sorted(set(a) | set(b)):
            delta = int(b.get(sid, 0)) - int(a.get(sid, 0))
            if delta:
                rows.append((etf_id, sid, delta, base, prev))
    return pl.DataFrame(
        rows,
        schema={
            "etf_id": pl.String,
            "stock_id": pl.String,
            "shares_delta": pl.Int64,
            "base": pl.Date,
            "prev": pl.Date,
        },
        orient="row",
    )


def _at(df: pl.DataFrame | None, day: dt.date) -> dict[str, float] | None:
    if df is None:
        return None
    return dict(df.filter(pl.col("date") == day).select("stock_id", "value").iter_rows())


def build_flow_flags(
    sids: Sequence[str],
    day: dt.date,
    *,
    pressure: pl.DataFrame | None,
    streak: pl.DataFrame | None,
    events: pl.DataFrame | None,
    changes: pl.DataFrame | None,
) -> dict[str, dict[str, Any]]:
    """{代號: 旗標}。某一類輸入整個是 None（資料不存在）→ 每檔該欄 None；有資料但這檔沒值 → 該檔 None。"""
    p, s = _at(pressure, day), _at(streak, day)
    ev: dict[str, list[str]] = {}
    if events is not None:
        for name, sid in events.select("event", "stock_id").unique().sort("event").iter_rows():
            ev.setdefault(sid, []).append(name)
    ch: dict[str, list[dict[str, Any]]] = {}
    if changes is not None:
        for etf_id, sid, delta, base, _prev in changes.sort("etf_id").iter_rows():
            ch.setdefault(sid, []).append(
                {"etf_id": etf_id, "shares_delta": delta, "base": base.isoformat()}
            )
    return {
        sid: {
            "pressure": None if p is None else p.get(sid),
            "disc_streak": None if s is None else s.get(sid),
            "events": None if events is None else ev.get(sid, []),
            "active_etf": None if changes is None else ch.get(sid, []),
        }
        for sid in sids
    }


def flags_text(flag: dict[str, Any] | None) -> str:
    """看板一格的短文字（HTML 用）；整筆或單欄沒資料都寫「無資料」，不寫 0。"""
    if flag is None:
        return "無資料"
    parts = []
    p = flag.get("pressure")
    parts.append("ETF 壓力 無資料" if p is None else f"ETF 壓力 {p * 100:+.1f}%")
    s = flag.get("disc_streak")
    parts.append("投信裁量連買 無資料" if s is None else f"投信裁量連買 {int(s)} 日")
    ev = flag.get("events")
    if ev is None:
        parts.append("事件 無資料")
    elif ev:
        parts.append("事件 " + "、".join(ev))
    act = flag.get("active_etf")
    if act is None:
        parts.append("主動 ETF 無資料")
    elif act:
        parts.append(
            "主動 ETF " + "、".join(f"{a['etf_id']} {a['shares_delta'] // 1000:+,} 張" for a in act)
        )
    return "｜".join(parts)
