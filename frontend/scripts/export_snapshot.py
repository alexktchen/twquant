"""匯出儀表板資料快照（含歷史序列）→ frontend/src/data.json，供 React 前端內嵌。

從 twquant repo 根執行：uv run python frontend/scripts/export_snapshot.py
然後在 frontend/ 用 docker build 重新打包。"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import polars as pl

import twquant.factors.builtins  # noqa: F401
from twquant.backtest.capacity import estimate_capacity
from twquant.backtest.costs import CostConfig
from twquant.backtest.engine import run_backtest
from twquant.config import SystemConfig, load_config
from twquant.data.warehouse import Warehouse
from twquant.factors.compute import compute
from twquant.factors.ic import compute_ic_report
from twquant.factors.registry import get, list_factors
from twquant.strategy.build import build_targets
from twquant.strategy.config import load_strategy

CFG = load_config(Path("configs/system.yaml"), SystemConfig)
WH = Warehouse(CFG.data_dir)
U = r"^[1-9][0-9]{3}$"
START, END = dt.date(2025, 8, 1), dt.date(2025, 12, 31)  # 連續期，避開 TWSE 缺口
as_of = WH.latest_date("prices_daily")
assert as_of is not None

out: dict[str, object] = {"meta": {}, "market": [], "stocks": {}, "factors": [],
                          "backtest": {}, "institutional": [], "revenue": [], "capacity": {}}

# ── 市場等權指數 K 線（歷史）──
mkt = WH.sql(
    "WITH base AS (SELECT stock_id, arg_min(close,date) b FROM prices_daily "
    "WHERE regexp_matches(stock_id,'^[1-9][0-9]{3}$') AND close>0 GROUP BY stock_id) "
    "SELECT p.date::VARCHAR d, round(1000*avg(p.open/b.b),2) o, round(1000*avg(p.high/b.b),2) h, "
    "round(1000*avg(p.low/b.b),2) l, round(1000*avg(p.close/b.b),2) c, round(sum(p.amount)/1e8,0) amt "
    "FROM prices_daily p JOIN base b USING(stock_id) WHERE regexp_matches(p.stock_id,'^[1-9][0-9]{3}$') "
    "GROUP BY p.date ORDER BY p.date"
)
out["market"] = mkt.to_dicts()

# ── 前 8 大成交值個股 OHLC（歷史）──
top = WH.sql(
    "SELECT stock_id FROM prices_daily WHERE regexp_matches(stock_id,'^[1-9][0-9]{3}$') "
    "GROUP BY stock_id ORDER BY avg(amount) DESC LIMIT 8"
)["stock_id"].to_list()
for sid in top:
    df = WH.sql(
        "SELECT date::VARCHAR d, open o, high h, low l, close c, volume v FROM prices_daily "
        f"WHERE stock_id='{sid}' ORDER BY date"
    )
    out["stocks"][sid] = df.to_dicts()  # type: ignore[index]

# ── 因子健檢 IC（連續期）──
view = WH.view(as_of)
prices = (
    view.table("prices_daily").select("date", "stock_id", "close")
    .filter(pl.col("stock_id").str.contains(U)).collect()
)
facs = []
for name in list_factors():
    try:
        fdf = compute([name], view).filter(pl.col("stock_id").str.contains(U)).filter(
            pl.col("date") >= START
        )
        rep = compute_ic_report(name, fdf, prices, [5, 10, 20])
        facs.append({
            "name": name,
            "direction": get(name).direction,
            "horizons": [
                {"h": h.horizon, "ic": round(h.ic_mean, 4), "t": round(h.t_stat, 2),
                 "ir": round(h.ic_ir, 3), "ls": round(h.ls_spread, 4), "n": h.n_days}
                for h in rep.horizons
            ],
        })
    except Exception as e:  # noqa: BLE001
        print("skip factor", name, e)
out["factors"] = facs

# ── 回測權益曲線 + 指標 ──
strat = load_strategy("configs/strategies/sample_revenue_trust.yaml")
cost = load_config(Path("configs/costs.yaml"), CostConfig)
res = run_backtest(strat, cost, START, END, WH)
m = res.metrics
out["backtest"] = {
    "name": strat.name,
    "equity": [{"d": e.date.isoformat(), "e": round(e.equity, 0)} for e in res.equity],
    "metrics": {"sharpe": round(m.sharpe, 2), "cagr": round(m.cagr, 4), "mdd": round(m.mdd, 4),
                "sortino": round(m.sortino, 2), "calmar": round(m.calmar, 2),
                "win_rate": round(m.win_rate, 3), "turnover": round(m.turnover, 2),
                "total_ret": round(res.final_equity / res.initial_capital - 1, 4),
                "n_trades": len(res.ledger)},
}

# ── 法人流向（最新）──
inst_date = WH.latest_date("institutional_daily")
if inst_date is not None:
    out["institutional"] = WH.sql(
        "SELECT i.stock_id sid, i.trust_net trust, i.foreign_net frgn, p.close px "
        "FROM institutional_daily i LEFT JOIN prices_daily p USING(date,stock_id) "
        f"WHERE i.date=DATE '{inst_date}' ORDER BY i.trust_net DESC LIMIT 15"
    ).to_dicts()

# ── 月營收 YoY（最新）──
rev_ym = WH.sql("SELECT max(year_month) ym FROM monthly_revenue")["ym"][0]
if rev_ym:
    out["revenue"] = WH.sql(
        "SELECT stock_id sid, round(revenue/1e8,1) rev, round(yoy*100,1) yoy FROM monthly_revenue "
        f"WHERE year_month='{rev_ym}' AND revenue>5e8 AND yoy IS NOT NULL ORDER BY yoy DESC LIMIT 15"
    ).to_dicts()

# ── 容量 ──
tdf = build_targets(strat, WH, as_of)
weights = {r["stock_id"]: float(r["target_weight"]) for r in tdf.to_dicts()}
adv = {
    r["stock_id"]: float(r["adv"])
    for r in WH.sql(
        "SELECT stock_id, median(amount) adv FROM prices_daily WHERE date IN "
        f"(SELECT DISTINCT date FROM prices_daily WHERE date<=DATE '{as_of}' ORDER BY date DESC LIMIT 20) "
        "GROUP BY stock_id"
    ).to_dicts()
}
cap = estimate_capacity(weights, adv)
out["capacity"] = {"twd": round(cap.capacity_twd, 0), "binding": cap.binding_stock,
                   "n_positions": len(weights)}

# ── coverage / meta ──
cov = []
for tbl, src, label in [("prices_daily", "twse", "上市"), ("prices_daily", "tpex", "上櫃"),
                        ("institutional_daily", "twse", "三大法人"), ("margin_daily", "twse", "融資券")]:
    dates = WH.source_dates(tbl, src)
    cov.append({"label": label, "days": len(dates),
                "span": f"{dates[0]}~{dates[-1]}" if dates else "—"})
out["meta"] = {"as_of": as_of.isoformat(), "window": f"{START}~{END}", "coverage": cov,
               "rev_month": rev_ym, "top_stocks": top}

import math


def _san(o: object) -> object:
    """把非有限 float（NaN/Inf）換成 None——否則 json.dumps 會寫出非法 JSON（rollup 拒解析）。"""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _san(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_san(v) for v in o]
    return o


out = _san(out)  # type: ignore[assignment]
# 丟掉在此窗無有效資料的因子（如 high_52w 需 252 日）
out["factors"] = [
    f
    for f in out["factors"]  # type: ignore[union-attr]
    if next((h for h in f["horizons"] if h["h"] == 10), {}).get("t") is not None
]

dest = Path(__file__).resolve().parent.parent / "src" / "data.json"
dest.write_text(
    json.dumps(out, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8"
)
print(f"寫出 {dest}  大小 {dest.stat().st_size / 1024:.0f} KB")
print("因子數", len(facs), "市場點", len(out["market"]), "個股", list(out["stocks"].keys()))  # type: ignore[arg-type]
