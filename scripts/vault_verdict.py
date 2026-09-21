"""金庫期策略層判讀（D-106 的 P2／P5）。只讀結果包與價格，不寫任何東西。"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from twquant.config import ServingConfig
from twquant.data.serving import store

DATA = Path("D:/project/twquant/data")
VAULT_LO, VAULT_HI = dt.date(2026, 3, 2), dt.date(2026, 8, 31)

# 代號 → (金庫包, 長窗口包, 短窗口包)。短窗口缺兩支是**登記時的疏漏**，照實記，不換判準。
CELLS = [
    (
        "LW-C1",
        "LW-C1_lw_c1_breakout_1x",
        "LW-C1_lw_c1_breakout_1x",
        "tracks_c/C1_track_c1_breakout_1x",
    ),
    (
        "LW-R2",
        "LW-R2_lw_r2_c1_lvgate_1x",
        "LW-R2_lw_r2_c1_lvgate_1x",
        "tracks_fr/R2_track_r2_c1_lvgate_1x",
    ),
    (
        "LW-B1",
        "LW-B1_lw_b1_large_enhanced_1x",
        "LW-B1_lw_b1_large_enhanced_1x",
        "tracks/B1_track_b1_large_enhanced_1x",
    ),
    (
        "LW-B2",
        "LW-B2_lw_b2_broad_capw_1x",
        "LW-B2_lw_b2_broad_capw_1x",
        "tracks/B2_track_b2_broad_capw_1x",
    ),
    ("LW-C1n50", "LW-C1n50_lw_c1_n50_1x", "LW-C1n50_lw_c1_n50_1x", None),
    ("LW-M1", "LW-M1_lw_m1_ml_c1_1x", "LW-M1_lw_m1_ml_c1_1x", "tracks_m/M1_track_m1_ml_c1_1x"),
    (
        "LW-F1",
        "LW-F1_lw_f1_revpool_1x",
        "LW-F1_lw_f1_revpool_1x",
        "tracks_fr/F1_track_f1_revpool_1x",
    ),
    ("LW-M1-faber", "LW-M1-faber_lw_m1_faber_1x", "LW-M1-faber_lw_m1_faber_1x", None),
]
# 長窗口那一批分散在多個目錄；逐一找
LONG_DIRS = [d.name for d in DATA.glob("tracks_lw_*") if d.is_dir()]


def _read(rel: str) -> dict | None:
    p = DATA / f"{rel}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _find_long(stem: str) -> dict | None:
    for d in LONG_DIRS:
        r = _read(f"{d}/{stem}")
        if r is not None:
            return r
    return None


def _m(pkg: dict) -> dict[str, float]:
    mt = pkg["metrics"]
    eq = pkg.get("equity") or []
    total = (
        float(pkg["final_equity"]) / float(pkg["initial_equity"]) - 1.0
        if pkg.get("initial_equity")
        else float(eq[-1][1]) / float(eq[0][1]) - 1.0
    )
    return {
        "cagr": float(mt["cagr"]),
        "mdd": float(mt["mdd"]),
        "sharpe": float(mt["sharpe"]),
        "total": total,
    }


def _bench() -> dict[str, float]:
    conn = store.connect(ServingConfig(pg_schema="serving"))
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT date, close FROM "serving"."prices_daily" '
                "WHERE stock_id = '0050' AND date BETWEEN %s AND %s ORDER BY date",
                (VAULT_LO, VAULT_HI),
            )
            got = cur.fetchall()
        px = pl.DataFrame({"date": [r[0] for r in got], "close": [float(r[1]) for r in got]})
    finally:
        conn.close()
    v = px["close"].to_numpy()
    yrs = (px["date"][-1] - px["date"][0]).days / 365.25
    peak, mdd = v[0], 0.0
    for x in v:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1)
    r = pl.Series(v).pct_change().drop_nulls()
    sd = float(r.std() or 0.0)
    return {
        "total": float(v[-1] / v[0] - 1),
        "cagr": float((v[-1] / v[0]) ** (1 / yrs) - 1),
        "mdd": float(mdd),
        "sharpe": (float(r.mean()) / sd * (244**0.5)) if sd else 0.0,
        "n": px.height,
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    b = _bench()
    print(f"金庫期 {VAULT_LO} ~ {VAULT_HI}（{b['n']} 個交易日）")
    print(
        f"0050 基準：累積 {b['total']:+.2%}　年化 {b['cagr']:+.2%}　"
        f"回撤 {b['mdd']:.2%}　Sharpe {b['sharpe']:+.2f}\n"
    )
    hdr = f"{'代號':12s} {'金庫累積':>9s} {'金庫年化':>9s} {'金庫回撤':>8s} {'Sh':>6s} | {'長窗口年化':>10s} {'短窗口年化':>10s} {'較近':>5s} {'贏0050':>7s}"
    print(hdr)
    print("-" * len(hdr))
    closer_long = closer_short = comparable = beat = 0
    rows = []
    for code, vs, ls, ss in CELLS:
        vp = _read(f"tracks_vault_nodiv/{vs}")
        if vp is None:
            print(f"{code:12s} ❌ 金庫包缺")
            continue
        v = _m(vp)
        lp = _find_long(ls)
        lo_c = _m(lp)["cagr"] if lp else None
        sp = _read(ss) if ss else None
        sh_c = _m(sp)["cagr"] if sp else None
        near = "—"
        if lo_c is not None and sh_c is not None:
            comparable += 1
            dl, ds = abs(v["cagr"] - lo_c), abs(v["cagr"] - sh_c)
            near = "長" if dl < ds else "短"
            if dl < ds:
                closer_long += 1
            else:
                closer_short += 1
        won = v["total"] > b["total"]
        beat += int(won)
        rows.append((code, v, lo_c, sh_c, near, won))
        print(
            f"{code:12s} {v['total']:>+8.2%} {v['cagr']:>+9.2%} {v['mdd']:>8.2%} {v['sharpe']:>+6.2f} | "
            f"{(f'{lo_c:+.2%}' if lo_c is not None else '—'):>10s} "
            f"{(f'{sh_c:+.2%}' if sh_c is not None else '—'):>10s} {near:>5s} {'✔' if won else '✘':>6s}"
        )
    print(
        f"\nP2：可比 {comparable}／8 支（另 2 支無短窗口對照，登記時的疏漏）；"
        f"離長窗口較近 {closer_long}、離短窗口較近 {closer_short}"
    )
    print(f"     登記門檻：≥6／8 為通過、≤2／8 為推翻。最多只能達到 {comparable}／8。")
    print(f"P5：{8 - beat}／8 支輸給 0050（登記預測 ≥5／8 輸）")
    (DATA / "vault_verdict.json").write_text(
        json.dumps(
            {
                "window": [str(VAULT_LO), str(VAULT_HI)],
                "benchmark": b,
                "cells": [
                    {"code": c, **v, "long_cagr": lc, "short_cagr": sc, "closer": n, "beat_0050": w}
                    for c, v, lc, sc, n, w in rows
                ],
                "p2": {
                    "comparable": comparable,
                    "closer_long": closer_long,
                    "closer_short": closer_short,
                },
                "p5": {"lost_to_0050": 8 - beat},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n→ {DATA / 'vault_verdict.json'}")


if __name__ == "__main__":
    main()
