"""關鍵數字表：上線中的幾條線＋候選，與 0050 含息在同一段期間、同一套公式比較。

讀回測結果包（每日權益、成交流水），0050 含息取自本機 PG（分割、股利都還原）。報告見
docs/reports/key-numbers-2026-10-13.md。

    TWQUANT_PG_DSN=postgresql://twquant:twquant@localhost:5433/twquant \\
    uv run --all-groups python scripts/key_numbers_table.py \\
        --data-dir D:/project/twquant/data --out D:/project/twquant/data/reports/key_numbers.json

指標定義：年化＝(期末÷期初)^(252÷交易日間隔數)−1；日／月夏普＝日／月報酬平均÷標準差×√252／√12（無風險利率 0）；
月周轉＝每月買進金額÷當月平均資產（單邊），取各月平均；逐筆＝部位從 0 買進到全部賣出算一筆、兩邊費用都算，
只算期間內開倉也在期間內平倉的。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl

from twquant.config import ServingConfig
from twquant.data.serving import store
from twquant.reporting.benchmarks import benchmark_series, cumulative_factors

TRADING_DAYS = 252

# 開發期：本機 PG 的 0050 從 2020-01-02 起（2026-09 重建後），所以整張表從這天開始，每一欄同一段
DEV = ("2020-01-02", "2026-02-26")
DEV_BUNDLES = {
    "大盤精選": "tracks_e_long_nodiv/E0_lw_e0_ctrl_1x.json",
    "潛伏飆股": "tracks_round13/quiet_s0_10m_lot1.json",
    "高分爆量": "tracks_d172/quiet_p35v_10m_lot1.json",
    "門檻0.35（候選）": "tracks_d172/quiet_p35_10m_lot1.json",
}
# 排序模型沒看過的半年（金庫期）；潛伏飆股用 D-162 重訓到 2026-02 的模型
UNSEEN = ("2026-03-02", "2026-08-31")
UNSEEN_BUNDLES = {
    "大盤精選": "tracks_e_vault_nodiv/E0_lw_e0_ctrl_1x.json",
    "潛伏飆股": "tracks_d162/quiet_s0_2602.json",
}


def curve_stats(dates: list[str], values: list[float]) -> dict[str, object]:
    """權益曲線的報酬、夏普、回撤。"""
    v = np.asarray(values, dtype=float)
    r = v[1:] / v[:-1] - 1.0
    cagr = float((v[-1] / v[0]) ** (TRADING_DAYS / (len(v) - 1)) - 1.0)
    mdd = float((v / np.maximum.accumulate(v) - 1.0).min())
    month_end: dict[str, float] = {}
    for d, x in zip(dates, v, strict=True):
        month_end[d[:7]] = float(x)
    m = np.asarray([v[0], *month_end.values()], dtype=float)
    mr = m[1:] / m[:-1] - 1.0
    return {
        "start": dates[0],
        "end": dates[-1],
        "n_days": len(v),
        "cagr": cagr,
        "sharpe_daily": float(r.mean() / r.std(ddof=1) * math.sqrt(TRADING_DAYS)),
        "sharpe_monthly": float(mr.mean() / mr.std(ddof=1) * math.sqrt(12)),
        "mdd": mdd,
        "cumulative": float(v[-1] / v[0] - 1.0),
        "calmar": cagr / abs(mdd),
    }


def monthly_turnover(ledger: list[dict], dates: list[str], values: list[float]) -> float:
    """每月買進金額 ÷ 當月平均資產，各月平均（單邊）。"""
    equity: dict[str, list[float]] = defaultdict(list)
    for d, x in zip(dates, values, strict=True):
        equity[d[:7]].append(x)
    buys: dict[str, float] = defaultdict(float)
    for t in ledger:
        if t["side"] == "buy" and dates[0] <= t["date"] <= dates[-1]:
            buys[t["date"][:7]] += t["shares"] * t["price"]
    return float(np.mean([buys.get(mo, 0.0) / np.mean(xs) for mo, xs in equity.items()]))


def round_trips(ledger: list[dict], start: str, end: str) -> dict[str, float]:
    """逐筆報酬（同 backtest.diagnostics.round_trip_stats 的定義，另外限定開倉、平倉都在期間內）。"""
    pos: dict[str, int] = {}
    cost: dict[str, float] = {}
    proceeds: dict[str, float] = {}
    opened: dict[str, str] = {}
    rets: list[float] = []
    for t in sorted(ledger, key=lambda x: (x["date"], x["stock_id"], x["side"])):
        sid = t["stock_id"]
        fees = t["commission"] + t["tax"] + t["slippage"]
        if t["side"] == "buy":
            if pos.get(sid, 0) == 0:
                opened[sid] = t["date"]
            pos[sid] = pos.get(sid, 0) + t["shares"]
            cost[sid] = cost.get(sid, 0.0) + t["shares"] * t["price"] + fees
            continue
        pos[sid] = pos.get(sid, 0) - t["shares"]
        proceeds[sid] = proceeds.get(sid, 0.0) + t["shares"] * t["price"] - fees
        if pos[sid] < 0:
            raise ValueError(f"{sid} 在 {t['date']} 的部位變成 {pos[sid]}：成交流水壞了")
        if pos[sid] == 0:
            c, p = cost.pop(sid), proceeds.pop(sid)
            if start <= opened[sid] and t["date"] <= end:
                rets.append(p / c - 1.0)
    a = np.asarray(rets)
    return {
        "n": len(a),
        "mean": float(a.mean()),
        "median": float(np.median(a)),
        "win_rate": float((a > 0).mean()),
    }


def bench_0050(start: str, end: str) -> tuple[list[str], list[float]]:
    """0050 含息（分割、股利都還原）的每日序列。"""
    conn = store.connect(ServingConfig(pg_schema="serving"))
    try:
        px = pl.read_database(
            "select date, stock_id, close from serving.prices_daily "
            f"where stock_id = '0050' and date between '{start}' and '{end}'",
            conn,
        )
        ev = pl.read_database(
            "select date, stock_id, event_type, adj_factor from serving.corporate_events where stock_id = '0050'",
            conn,
        )
    finally:
        conn.close()
    s = benchmark_series(
        cumulative_factors(px.sort("stock_id", "date"), ev), "0050", total_return=True
    )
    dates = [d.isoformat() for d in s["date"].to_list()]
    if dates[0] != start:
        raise ValueError(
            f"0050 第一天是 {dates[0]}，不是 {start}：基準與策略期間不同，不能放同一張表"
        )
    return dates, [float(x) for x in s["value"].to_list()]


def table(
    data_dir: Path, bundles: dict[str, str], start: str, end: str
) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for name, rel in bundles.items():
        b = json.loads((data_dir / rel).read_text(encoding="utf-8"))
        rows = [e for e in b["equity"] if start <= e[0] <= end]
        dates = [e[0] for e in rows]
        values = [float(e[1]) for e in rows]
        out[name] = {
            **curve_stats(dates, values),
            "monthly_turnover": monthly_turnover(b["ledger"], dates, values),
            "exposure": float(np.mean([float(e[3]) / float(e[1]) for e in rows])),
            "trips": round_trips(b["ledger"], dates[0], dates[-1]),
            "initial_capital": b["initial_capital"],
            "bundle": rel,
        }
    out["0050 含息"] = curve_stats(*bench_0050(start, end))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--data-dir", required=True, type=Path, help="資料正本（絕對路徑，見 CLAUDE.md §0.2）"
    )
    ap.add_argument("--out", required=True, type=Path, help="輸出 JSON（絕對路徑）")
    args = ap.parse_args()
    result = {
        "dev": table(args.data_dir, DEV_BUNDLES, *DEV),
        "unseen": table(args.data_dir, UNSEEN_BUNDLES, *UNSEEN),
    }
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
