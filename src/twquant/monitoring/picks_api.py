"""雲端網站的每日名單 API（T-332／D-170）：讀 PG `daily_picks`，附收盤、漲跌、連續上榜。

連續上榜與新進是純函數（`annotate`），便於單元測試；PG 讀取延後 import psycopg（同 stock_api）。
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from typing import Any

from twquant.config import ServingConfig, load_config
from twquant.errors import DataError
from twquant.monitoring import snapshot as snap

_SID_RE = re.compile(r"^[0-9A-Z]{4,6}$")
STREAK_LOOKBACK = 60  # 連續上榜最多回看幾個有名單的日子（API 參數上限，非模型參數）
RECENT_WINDOW = 6  # 「近 6 日上榜」方格與近 6 日漲幅的天數（同舊看板 HTML 的 html_window_sessions）


def line_names() -> dict[str, str]:
    """線別代碼 → 顯示名稱（以 configs/pick_board.yaml 為準）。"""
    from twquant.reporting.pick_board import PickBoardConfig

    cfg = load_config(snap.CONFIGS_DIR / "pick_board.yaml", PickBoardConfig)
    return {s.code: s.name for s in cfg.strategies}


def annotate(
    today: Sequence[str], previous: Sequence[Sequence[str]]
) -> dict[str, tuple[int, bool]]:
    """每檔的 (連續上榜天數, 是否新進)。`previous` 由近到遠，每項是那天同一條線的名單。

    連續天數含今天；中間有一天不在名單就斷。新進＝前一個有名單的日子不在名單裡（沒有前一天也算新進）。
    """
    out: dict[str, tuple[int, bool]] = {}
    for sid in today:
        streak = 1
        for day_list in previous:
            if sid not in day_list:
                break
            streak += 1
        is_new = not previous or sid not in previous[0]
        out[sid] = (streak, is_new)
    return out


def recent_flags(sid: str, window: Sequence[Sequence[str] | None]) -> list[bool | None]:
    """近 N 日每天是否在名單，舊到新。`window` 由近到遠；None＝那天這條線沒有名單（模型沒打分）。"""
    return [None if day is None else sid in day for day in reversed(window)]


def _query(cfg: ServingConfig, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    from psycopg.rows import dict_row

    from twquant.data.serving import store

    with store.connect(cfg) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def pick_dates(cfg: ServingConfig, limit: int) -> list[str]:
    """有名單的日期，新到舊。"""
    rows = _query(
        cfg,
        f'SELECT DISTINCT date FROM "{cfg.pg_schema}".daily_picks ORDER BY date DESC LIMIT %s',
        (limit,),
    )
    return [r["date"].isoformat() for r in rows]


def pick_counts(cfg: ServingConfig, dates: Sequence[str]) -> dict[str, Any]:
    """日期選單的檔數（同 Cloudflare 版 groupCounts）：counts[日期][線別]＝檔數、since[線別]＝這條線第一天有名單的日子。"""
    sch = cfg.pg_schema
    counts: dict[str, dict[str, int]] = {}
    if dates:
        for r in _query(
            cfg,
            f'SELECT date, line, count(*) AS n FROM "{sch}".daily_picks WHERE date >= %s '
            "GROUP BY date, line",
            (dt.date.fromisoformat(dates[-1]),),
        ):
            counts.setdefault(r["date"].isoformat(), {})[r["line"]] = int(r["n"])
    since = {
        r["line"]: r["d"].isoformat()
        for r in _query(
            cfg, f'SELECT line, min(date) AS d FROM "{sch}".daily_picks GROUP BY line', ()
        )
    }
    return {"counts": counts, "since": since}


def _change(close: float | None, prev: float | None) -> float | None:
    if close is None or prev is None or prev <= 0:
        return None
    return close / prev - 1.0


def picks_payload(
    cfg: ServingConfig, day: dt.date | None, names: Mapping[str, str]
) -> dict[str, Any]:
    """某日（None＝最新）兩條線的名單，每檔附名稱、收盤、當日漲跌、連續上榜、新進，
    以及近 6 日上榜方格（recent）與近 6 日漲幅（chg6＝窗口第一天收盤到當天收盤）。"""
    sch = cfg.pg_schema
    dates = [
        r["date"]
        for r in _query(
            cfg,
            f'SELECT DISTINCT date FROM "{sch}".daily_picks WHERE date <= %s '
            "ORDER BY date DESC LIMIT %s",
            (day or dt.date.max, STREAK_LOOKBACK + 1),
        )
    ]
    if not dates or (day is not None and dates[0] != day):
        raise DataError("daily_picks", day, "這天沒有名單")
    target = dates[0]
    rows = _query(
        cfg,
        f'SELECT date, line, rank, stock_id, score FROM "{sch}".daily_picks '
        "WHERE date = ANY(%s) ORDER BY date DESC, line, rank",
        (dates,),
    )
    by_day: dict[tuple[dt.date, str], list[dict[str, Any]]] = {}
    for r in rows:
        by_day.setdefault((r["date"], r["line"]), []).append(r)
    today_rows = [r for r in rows if r["date"] == target]
    sids = sorted({r["stock_id"] for r in today_rows})
    px = _query(
        cfg,
        f'SELECT p.stock_id, p.close, (SELECT q.close FROM "{sch}".prices_daily q '
        "WHERE q.stock_id = p.stock_id AND q.date < p.date ORDER BY q.date DESC LIMIT 1) AS prev "
        f'FROM "{sch}".prices_daily p WHERE p.date = %s AND p.stock_id = ANY(%s)',
        (target, sids),
    )
    close = {r["stock_id"]: (r["close"], r["prev"]) for r in px}
    window = dates[:RECENT_WINDOW]
    start_close: dict[str, float] = {}
    if len(window) > 1:
        start_close = {
            r["stock_id"]: r["close"]
            for r in _query(
                cfg,
                f'SELECT stock_id, close FROM "{sch}".prices_daily '
                "WHERE date = %s AND stock_id = ANY(%s)",
                (window[-1], sids),
            )
        }
    meta = {
        r["stock_id"]: r["name"]
        for r in _query(
            cfg, f'SELECT stock_id, name FROM "{sch}".stock_meta WHERE stock_id = ANY(%s)', (sids,)
        )
    }
    lines = []
    # D-174：登記過的線一律列出——有門檻的線當天沒有股票是正常的一天（picks 為空），不是整條線消失
    for code in sorted(
        {r["line"] for r in today_rows} | set(names),
        key=lambda c: list(names).index(c) if c in names else 99,
    ):
        today_list = by_day.get((target, code), [])
        today = [r["stock_id"] for r in today_list]
        previous = [[r["stock_id"] for r in by_day.get((d, code), [])] for d in dates[1:]]
        marks = annotate(today, previous)
        recent_lists = [
            [r["stock_id"] for r in by_day[(d, code)]] if (d, code) in by_day else None
            for d in window
        ]
        picks = []
        for r in today_list:
            c, p = close.get(r["stock_id"], (None, None))
            streak, is_new = marks[r["stock_id"]]
            picks.append(
                {
                    "rank": r["rank"],
                    "sid": r["stock_id"],
                    "name": meta.get(r["stock_id"]),
                    "score": r["score"],
                    "close": c,
                    "chg": _change(c, p),
                    "streak": streak,
                    "new": is_new,
                    "recent": recent_flags(r["stock_id"], recent_lists),
                    "chg6": _change(c, start_close.get(r["stock_id"])),
                }
            )
        lines.append({"code": code, "name": names.get(code, code), "picks": picks})
    return {
        "date": target.isoformat(),
        "window": [d.isoformat() for d in reversed(window)],
        "lines": lines,
    }


def stock_pick_days(cfg: ServingConfig, sid: str) -> dict[str, Any]:
    """某檔被推薦過的日子（給 K 線打標記）：{線別: [日期...]}。"""
    if not _SID_RE.match(sid):
        raise DataError("daily_picks", None, f"非法股票代號：{sid!r}")
    rows = _query(
        cfg,
        f'SELECT line, date FROM "{cfg.pg_schema}".daily_picks WHERE stock_id = %s ORDER BY date',
        (sid,),
    )
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["line"], []).append(r["date"].isoformat())
    return {"sid": sid, "days": out}
