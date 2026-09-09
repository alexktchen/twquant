"""兩個視窗的基準：短窗口 2022-07-01~2026-02-28 與長窗口 2017-01-03~2026-02-28。

**含息與價格報酬各算一份**——策略那 209 份結果包裡兩種口徑都有，
拿錯口徑的基準去比正是 D-085 指出的那個不對稱。
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import polars as pl

from twquant.config import ServingConfig
from twquant.data.serving import store
from twquant.reporting.benchmarks import benchmark_series, cumulative_factors

_WINDOWS = {
    "short": (dt.date(2022, 7, 1), dt.date(2026, 2, 28)),
    "long": (dt.date(2017, 1, 3), dt.date(2026, 2, 28)),
    # D-102：ML 策略的視窗。基準也要有這一份，否則 ML 的數字沒有同視窗的對照
    "w2019": (dt.date(2019, 5, 2), dt.date(2026, 2, 28)),
}
_ETFS = {
    "0050": "元大台灣50（市值型大盤代理）",
    "0051": "元大中型100（結構上不含台積電，市值 51~150 名）",
    "0056": "元大高股息",
}


def _metrics(s: pl.DataFrame, lo: dt.date, hi: dt.date) -> dict[str, float] | None:
    w = s.filter(pl.col("date").is_between(lo, hi)).sort("date")
    if w.height < 60:
        return None
    v = w["value"].to_numpy()
    yrs = (w["date"][-1] - w["date"][0]).days / 365.25
    cagr = float((v[-1] / v[0]) ** (1 / yrs) - 1)
    peak, mdd = v[0], 0.0
    for x in v:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1)
    r = pl.Series(v).pct_change().drop_nulls()
    sd = float(r.std() or 0.0)
    return {
        "cagr": cagr,
        "mdd": float(mdd),
        "sharpe": (float(r.mean()) / sd * (244**0.5)) if sd else 0.0,
        "n_days": w.height,
        "start": str(w["date"][0]),
        "end": str(w["date"][-1]),
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    conn = store.connect(ServingConfig(pg_schema="serving"))
    try:
        px = pl.read_database(
            "select date, stock_id, close from serving.prices_daily "
            "where stock_id in ('0050','0051','0056') and date >= '2016-12-01'",
            conn,
        )
        ev = pl.read_database(
            "select date, stock_id, event_type, adj_factor from serving.corporate_events", conn
        )
        idx = pl.read_database(
            "select date, index_id, close from serving.index_daily where date >= '2016-12-01'", conn
        )
    finally:
        conn.close()
    px = cumulative_factors(px.sort("stock_id", "date"), ev)

    out: dict[str, dict[str, object]] = {}
    for sid, desc in _ETFS.items():
        for tr in (True, False):
            key = f"{sid}_{'tr' if tr else 'px'}"
            try:
                s = benchmark_series(px, sid, total_return=tr)
            except Exception as exc:  # 資料不足時說出來，不靜默跳過
                print(f"  {key}：{exc}")
                continue
            out[key] = {
                "name": f"{sid} {desc}",
                "total_return": tr,
                **{w: _metrics(s, lo, hi) for w, (lo, hi) in _WINDOWS.items()},
            }
    # TAIEX：發行量加權股價報酬指數本身不含息，故只有價格口徑
    tx = idx.filter(pl.col("index_id") == "TAIEX").select("date", pl.col("close").alias("value"))
    if tx.height:
        out["TAIEX_px"] = {
            "name": "TAIEX 加權指數（不含息）",
            "total_return": False,
            **{w: _metrics(tx, lo, hi) for w, (lo, hi) in _WINDOWS.items()},
        }

    p = Path("D:/project/twquant/data/reports/benchmarks_two_windows.json")
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(
        f"{'基準':36s} {'短 CAGR':>10s}{'MDD':>9s} |{'長 CAGR':>10s}{'MDD':>9s} |{'2019 CAGR':>10s}{'MDD':>9s}"
    )
    for k, v in out.items():
        s, lg = v.get("short"), v.get("long")
        f = lambda m, kk: f"{m[kk] * 100:+7.2f}%" if m else "     —  "  # noqa: E731
        print(
            f"{str(v['name'])[:42]:44s} {f(s, 'cagr'):>11s} {f(s, 'mdd'):>8s} | {f(lg, 'cagr'):>11s} {f(lg, 'mdd'):>8s}"
        )
    print(f"\n→ {p}")


if __name__ == "__main__":
    main()
