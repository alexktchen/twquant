"""量化「策略是價格報酬、基準是含息報酬」這個不對稱值多少（D-085）。

**缺陷**：`backtest/engine.py` 的 `state.cash` 只有買賣結算三處，**沒有任何收益路徑**
（T-113 早已記錄）。於是除息當天引擎看到價格跳空下跌、記為虧損，而股利現金從未入帳。
同時 `reporting/benchmarks.py` 的 0050／0051／0056 走的是**含息**序列。
兩邊口徑不同，而所有「vs 大盤」的結論都建立在這個比較上。

本腳本用結果包的持倉逐日對上 `corporate_events`，估算期間本應收到的現金股利。

**這是下界**：把股利當成期末一次入帳，沒有再投資、也沒有複利。真實的含息報酬更高。
要拿到精確數字必須改引擎（T-113），本腳本只回答「值不值得改」。

用法：
    python scripts/dividend_drag.py
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import polars as pl

from twquant.backtest.diagnostics import positions_from_bundle
from twquant.config import ServingConfig, load_config
from twquant.data.serving import store

_CONFIG = pathlib.Path(__file__).resolve().parents[1] / "configs" / "serving.yaml"
ROOT = pathlib.Path("D:/project/twquant/data")
START, END = "2022-07-01", "2026-02-28"
DAYS_PER_YEAR = 244.0
CELLS = (
    ("L0-new", "ladder/L0new_ladder_l0_new_1x.json"),
    ("A", "tracks/A_track_a_smallcap_1x.json"),
    ("B1", "tracks/B1_track_b1_large_enhanced_1x.json"),
    ("B2", "tracks/B2_track_b2_broad_capw_1x.json"),
    ("C1", "tracks_c/C1_track_c1_breakout_1x.json"),
    ("M1", "tracks_m/M1_track_m1_ml_c1_1x.json"),
    ("F1", "tracks_fr/F1_track_f1_revpool_1x.json"),
    ("R1", "tracks_fr/R1_track_r1_c1_ddgate_1x.json"),
)


def main() -> None:
    cfg = load_config(_CONFIG, ServingConfig)
    conn = store.connect(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT date, stock_id, adj_factor FROM "{cfg.pg_schema}"."corporate_events" '
                "WHERE adj_factor IS NOT NULL AND date BETWEEN %s AND %s",
                (START, END),
            )
            ev = pl.DataFrame(
                cur.fetchall(),
                schema={"date": pl.Date, "stock_id": pl.String, "adj_factor": pl.Float64},
                orient="row",
            )
            cur.execute(
                f'SELECT date, stock_id, close FROM "{cfg.pg_schema}"."prices_daily" '
                "WHERE date BETWEEN %s AND %s AND stock_id ~ '^[1-9][0-9]{3}$'",
                ("2022-06-01", END),
            )
            px = pl.DataFrame(
                cur.fetchall(),
                schema={"date": pl.Date, "stock_id": pl.String, "close": pl.Float64},
                orient="row",
            )
    finally:
        conn.close()

    # 每股現金 ≈ 前收 × (1 − adj_factor)。**adj_factor < 1 是本 repo 的慣例**：
    # 還原價 = close/adj，adj 隨事件變小，含息序列才會高於價格序列
    # （0050 實測 +40.08% 含息 vs +35.55% 價格）。用 1−1/adj 會得到負的股利。
    prev = px.sort("stock_id", "date").with_columns(
        _prev=pl.col("close").shift(1).over("stock_id", order_by="date")
    )
    ev = (
        ev.join(prev.select("date", "stock_id", "_prev"), on=["date", "stock_id"], how="left")
        .drop_nulls("_prev")
        .with_columns(_dps=pl.col("_prev") * (1.0 - pl.col("adj_factor")))
        .filter(pl.col("_dps") > 0.0)
    )

    print(f"視窗 {START} ~ {END}　除息事件 {ev.height:,} 筆")
    print(
        f"\n{'策略':7s} {'股利（元）':>16s} {'占初始權益':>10s} {'CAGR 價格':>10s} {'含息下界':>10s} {'差':>8s}"
    )
    for name, rel in CELLS:
        p = ROOT / rel
        if not p.exists():
            print(f"{name:7s} （缺結果包 {rel}）")
            continue
        b = json.loads(p.read_text(encoding="utf-8"))
        pos = positions_from_bundle(b)
        if pos.height == 0:
            print(f"{name:7s} （無持倉）")
            continue
        got = pos.join(ev.select("date", "stock_id", "_dps"), on=["date", "stock_id"], how="inner")
        cash = float((got["shares"] * got["_dps"]).sum())
        eq0, eqt = float(b["initial_equity"]), float(b["final_equity"])
        yrs = b["metrics"]["n_days"] / DAYS_PER_YEAR
        cagr = float(b["metrics"]["cagr"])
        lower = ((eqt + cash) / eq0) ** (1.0 / yrs) - 1.0
        print(
            f"{name:7s} {cash:16,.0f} {cash / eq0:10.2%} {cagr:+10.2%} "
            f"{lower:+10.2%} {(lower - cagr) * 100:+7.2f}pp"
        )
    print(
        "\n**含息是下界**：股利當期末一次入帳，無再投資、無複利。精確數字須改引擎（T-113）。"
        "\n基準對照：0050 含息 +40.08%／價格 +35.55%（差 4.53pp），0051 含息 +31.57%。"
    )


if __name__ == "__main__":
    main()
