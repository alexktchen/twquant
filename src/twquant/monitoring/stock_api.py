"""個股 API v2 讀取層（SPEC-009 §4 / PRD-001 §5）——只讀 PostgreSQL 供應層。

個股頁所有資料一律走 PG（serving schema）。**不再降級資料湖 DuckDB**：PG 沒有該資料就回空、
連不上就讓端點 503——避免「一下 PG 一下湖、兩邊不同步」的混淆。橫斷面因子（factor_values /
composite_daily）需先 materialize；未物化即回空。衍生欄（累計淨買超 / 券資比 / MoM /
top 券商）抽成純函數，便於單元測試。psycopg 延後 import（api.py 於頂層 import 本模組；
CI 不裝 serving 群組時本模組仍可載入）。
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, cast

from twquant.config import ServingConfig, load_config
from twquant.errors import DataError
from twquant.monitoring import snapshot as snap

_SID_RE = re.compile(r"^[0-9A-Z]{2,6}$")
_cfg_cache: list[Any] = [0.0, None]  # [mtime, ServingConfig]


def serving_cfg() -> ServingConfig:
    """載入並快取 configs/serving.yaml（mtime 變即重載）。"""
    path = snap.CONFIGS_DIR / "serving.yaml"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    if _cfg_cache[1] is None or _cfg_cache[0] != mtime:
        _cfg_cache[0] = mtime
        _cfg_cache[1] = load_config(path, ServingConfig)
    return cast("ServingConfig", _cfg_cache[1])


def _check(sid: str, table: str) -> None:
    if not _SID_RE.match(sid):
        raise DataError(table, None, f"非法股票代號：{sid!r}")


# ── PG 讀取原語 ──────────────────────────────────────────────────


def _rows(
    cfg: ServingConfig, table: str, cols: tuple[str, ...], sid: str, limit: int | None = None
) -> list[dict[str, Any]]:
    """讀某表某股的欄位，date 遞增。PG 連不上會 raise（由端點轉 503）。"""
    from psycopg.rows import dict_row

    from twquant.data.serving import store

    collist = ", ".join(f'"{c}"' for c in cols)
    sql = (
        f'SELECT {collist} FROM "{cfg.pg_schema}"."{table}" WHERE stock_id = %s ORDER BY date DESC'
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    with store.connect(cfg) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (sid,))
        out = list(cur.fetchall())
    out.reverse()
    return out


def _one(cfg: ServingConfig, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    from psycopg.rows import dict_row

    from twquant.data.serving import store

    with store.connect(cfg) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cast("dict[str, Any] | None", cur.fetchone())


def _meta(cfg: ServingConfig, sid: str) -> dict[str, Any]:
    row = _one(
        cfg,
        f'SELECT name, industry, market FROM "{cfg.pg_schema}".stock_meta WHERE stock_id = %s',
        (sid,),
    )
    return row or {}


def _wrap(sid: str, rows: list[dict[str, Any]], key: str = "date") -> dict[str, Any]:
    return {"sid": sid, "as_of": (rows[-1][key] if rows else None), "source": "pg", "rows": rows}


# ── 純衍生函數（可單元測試，不碰 PG）──────────────────────────────


def derive_institutional(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cf = ct = 0
    for r in rows:
        cf += int(r.get("foreign_net") or 0)
        ct += int(r.get("trust_net") or 0)
        r["cum_foreign"] = cf
        r["cum_trust"] = ct
    return rows


def derive_margin(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prev_m = prev_s = None
    for r in rows:
        mb, sb = r.get("margin_balance"), r.get("short_balance")
        r["margin_chg"] = (mb - prev_m) if (prev_m is not None and mb is not None) else None
        r["short_chg"] = (sb - prev_s) if (prev_s is not None and sb is not None) else None
        r["short_margin_ratio"] = (sb / mb) if (mb) else None
        prev_m, prev_s = mb, sb
    return rows


def derive_revenue(raw: list[dict[str, Any]], months: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    prev = None
    for r in raw[-months:]:
        rev = r.get("revenue")
        mom = (rev / prev - 1) if (prev not in (None, 0) and rev is not None) else None
        out.append({"month": r["year_month"], "revenue": rev, "yoy": r.get("yoy"), "mom": mom})
        prev = rev
    return out


def derive_brokers(rows: list[dict[str, Any]], window: int) -> dict[str, Any]:
    if not rows:
        return {"window_days": window, "top_buy": [], "top_sell": [], "summary": [], "as_of": None}
    win_dates = sorted({r["date"] for r in rows})[-window:]
    win = [r for r in rows if r["date"] in win_dates]
    by_day: dict[Any, dict[str, int]] = {}
    agg: dict[str, dict[str, int]] = {}
    for r in win:
        net = int(r.get("buy_shares") or 0) - int(r.get("sell_shares") or 0)
        day = by_day.setdefault(r["date"], {"buy": 0, "sell": 0})
        if net > 0:
            day["buy"] += 1
        elif net < 0:
            day["sell"] += 1
        b = agg.setdefault(str(r["broker_id"]), {"buy": 0, "sell": 0})
        b["buy"] += int(r.get("buy_shares") or 0)
        b["sell"] += int(r.get("sell_shares") or 0)
    ranked: list[dict[str, Any]] = [
        {
            "broker": k,
            "buy_shares": v["buy"],
            "sell_shares": v["sell"],
            "net_shares": v["buy"] - v["sell"],
        }
        for k, v in agg.items()
    ]
    return {
        "window_days": window,
        "as_of": win_dates[-1],
        "top_buy": sorted(
            (b for b in ranked if b["net_shares"] > 0), key=lambda x: -x["net_shares"]
        )[:15],
        "top_sell": sorted(
            (b for b in ranked if b["net_shares"] < 0), key=lambda x: x["net_shares"]
        )[:15],
        "summary": [
            {
                "date": d,
                "buy_brokers": by_day[d]["buy"],
                "sell_brokers": by_day[d]["sell"],
                "broker_count_diff": by_day[d]["buy"] - by_day[d]["sell"],
            }
            for d in sorted(by_day)
        ],
    }


# ── 端點（wh 參數保留但不使用，維持 api.py 呼叫不變）────────────────

_OHLC = ("date", "open", "high", "low", "close", "volume")
_INST = ("date", "foreign_net", "trust_net", "dealer_net")
_MARGIN = ("date", "margin_balance", "short_balance")
_REVENUE = ("year_month", "revenue", "yoy")
_BROKER = ("date", "broker_id", "buy_shares", "sell_shares", "avg_price")


def ohlc(wh: object, cfg: ServingConfig, sid: str, days: int = 500) -> dict[str, Any]:
    _check(sid, "prices_daily")
    return _wrap(sid, _rows(cfg, "prices_daily", _OHLC, sid, days))


def institutional(wh: object, cfg: ServingConfig, sid: str, days: int = 120) -> dict[str, Any]:
    _check(sid, "institutional_daily")
    return _wrap(sid, derive_institutional(_rows(cfg, "institutional_daily", _INST, sid, days)))


def margin(wh: object, cfg: ServingConfig, sid: str, days: int = 120) -> dict[str, Any]:
    _check(sid, "margin_daily")
    return _wrap(sid, derive_margin(_rows(cfg, "margin_daily", _MARGIN, sid, days)))


def revenue(wh: object, cfg: ServingConfig, sid: str, months: int = 36) -> dict[str, Any]:
    _check(sid, "monthly_revenue")
    raw = _rows(cfg, "monthly_revenue", _REVENUE, sid, None)
    out = derive_revenue(raw, months)
    return {"sid": sid, "as_of": (out[-1]["month"] if out else None), "source": "pg", "rows": out}


def brokers(wh: object, cfg: ServingConfig, sid: str, window: int = 5) -> dict[str, Any]:
    """主力券商買賣超（富邦DJ 母公司層級 top-15±）：近 window 交易日彙總買/賣超前 15 + 家數。"""
    _check(sid, "broker_trades")
    d = derive_brokers(_rows(cfg, "broker_trades", _BROKER, sid, window * 60), window)
    return {"sid": sid, "source": "pg", **d}


def overview(wh: object, cfg: ServingConfig, sid: str) -> dict[str, Any]:
    _check(sid, "prices_daily")
    rows = _rows(cfg, "prices_daily", _OHLC, sid, 252)
    meta = _meta(cfg, sid)
    out: dict[str, Any] = {
        "sid": sid,
        "name": meta.get("name"),
        "industry": meta.get("industry"),
        "market": meta.get("market"),
        "as_of": (rows[-1]["date"] if rows else None),
        "source": "pg",
        "close": None,
        "chg": None,
        "chg_pct": None,
        "volume": None,
        "pos_52w": None,
        "composite": _latest_composite(cfg, sid),
    }
    if rows:
        last = rows[-1]
        close = last["close"]
        out["close"] = close
        out["volume"] = last["volume"]
        if len(rows) >= 2 and rows[-2]["close"]:
            prev = rows[-2]["close"]
            out["chg"] = close - prev
            out["chg_pct"] = (close - prev) / prev
        highs = [r["high"] for r in rows if r["high"] is not None]
        lows = [r["low"] for r in rows if r["low"] is not None]
        if highs and lows and close is not None and max(highs) > min(lows):
            out["pos_52w"] = (close - min(lows)) / (max(highs) - min(lows))
    return out


def _latest_composite(cfg: ServingConfig, sid: str) -> dict[str, Any] | None:
    sch = cfg.pg_schema
    row = _one(
        cfg,
        f'SELECT date, signed_z_mean, pct FROM "{sch}".composite_daily '
        "WHERE stock_id = %s ORDER BY date DESC LIMIT 1",
        (sid,),
    )
    if row is None:
        return None
    n = _one(cfg, f'SELECT count(*) c FROM "{sch}".composite_daily WHERE date = %s', (row["date"],))
    return {
        "signed_z_mean": round(float(row["signed_z_mean"]), 2),
        "pct": round(float(row["pct"]), 3),
        "n_universe": int(n["c"]) if n else 0,
    }


def composite_history(wh: object, cfg: ServingConfig, sid: str, days: int = 250) -> dict[str, Any]:
    _check(sid, "composite_daily")
    return _wrap(sid, _rows(cfg, "composite_daily", ("date", "signed_z_mean", "pct"), sid, days))


def factor_history(
    wh: object, cfg: ServingConfig, sid: str, factors: list[str], days: int = 250
) -> dict[str, Any]:
    _check(sid, "factor_values")
    if not factors:
        return {"sid": sid, "as_of": None, "source": "pg", "rows": []}
    from psycopg.rows import dict_row

    from twquant.data.serving import store

    sch = cfg.pg_schema
    placeholders = ", ".join(["%s"] * len(factors))
    # 每因子取最近 days 個「有物化的點」（row-limit，與 composite-history 同語義）——
    # 物化歷史稀疏時仍回滿序列，不受日曆窗影響。
    sql = (
        "SELECT factor, date, value, z, pct FROM ("
        f"  SELECT factor, date, value, z, pct, row_number() OVER "
        f"  (PARTITION BY factor ORDER BY date DESC) rn "
        f'  FROM "{sch}".factor_values WHERE stock_id = %s AND factor IN ({placeholders})'
        ") t WHERE rn <= %s ORDER BY factor, date"
    )
    with store.connect(cfg) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (sid, *factors, days))
        raw = list(cur.fetchall())
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in raw:
        groups.setdefault(r["factor"], []).append(
            {"date": r["date"], "value": r["value"], "z": r["z"], "pct": r["pct"]}
        )
    rows: list[dict[str, Any]] = [{"factor": k, "series": v} for k, v in groups.items()]
    last = max((s["date"] for g in rows for s in g["series"]), default=None)
    return {"sid": sid, "as_of": last, "source": "pg", "rows": rows}


def scorecard(wh: object, cfg: ServingConfig, sid: str) -> dict[str, Any]:
    """個股因子計分卡（只讀 PG factor_values；未物化即回空卡，不再現算）。"""
    _check(sid, "factor_values")
    import twquant.factors.builtins  # noqa: F401  # 觸發註冊（取 direction）
    from twquant.factors.registry import get

    sch = cfg.pg_schema
    meta = _meta(cfg, sid)
    base: dict[str, Any] = {
        "sid": sid,
        "name": meta.get("name"),
        "industry": meta.get("industry"),
        "market": meta.get("market"),
        "as_of": None,
        "factors": [],
        "composite": None,
    }
    day_row = _one(
        cfg, f'SELECT max(date) d FROM "{sch}".factor_values WHERE stock_id = %s', (sid,)
    )
    day = day_row["d"] if day_row else None
    if day is None:
        return base
    fvs = _rows_at(cfg, sid, day)
    comp = _one(
        cfg,
        f'SELECT signed_z_mean, pct FROM "{sch}".composite_daily WHERE stock_id = %s AND date = %s',
        (sid, day),
    )
    n_univ = _one(cfg, f'SELECT count(*) c FROM "{sch}".composite_daily WHERE date = %s', (day,))
    factors_out: list[dict[str, Any]] = []
    for r in sorted(fvs, key=lambda x: str(x["factor"])):
        name = str(r["factor"])
        d = int(get(name).direction)
        z = float(r["z"])
        factors_out.append(
            {
                "factor": name,
                "zh": snap.FACTOR_ZH.get(name, name),
                "category": name.split(".")[0],
                "value": round(float(r["value"]), 4),
                "z": round(z, 2),
                "pct": round(float(r["pct"]), 3),
                "direction": d,
                "signed_z": round(z * d, 2),
            }
        )
    base["as_of"] = day.isoformat() if isinstance(day, dt.date) else None
    base["factors"] = factors_out
    if comp:
        base["composite"] = {
            "signed_z_mean": round(float(comp["signed_z_mean"]), 2),
            "pct": round(float(comp["pct"]), 3),
            "n_universe": int(n_univ["c"]) if n_univ else 0,
        }
    return base


def _rows_at(cfg: ServingConfig, sid: str, day: dt.date) -> list[dict[str, Any]]:
    from psycopg.rows import dict_row

    from twquant.data.serving import store

    with store.connect(cfg) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f'SELECT factor, value, z, pct FROM "{cfg.pg_schema}".factor_values '
            "WHERE stock_id = %s AND date = %s",
            (sid, day),
        )
        return list(cur.fetchall())
