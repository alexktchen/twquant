"""D-089 待裁決二：R 線的 +0.4189 有多少是單一因子的機械關係？

標籤是「未來 60 日相對 t 日收盤的最低點跌幅」的橫斷面百分位。波動度大的股票
跌幅分位天然靠後——那是定義，不是預測。本腳本量**單因子** RankIC，不訓練模型：
若 `price.vol_20d` 一個欄位就拿到大部分的 +0.4189，那 LightGBM 的增量才是貢獻。

用模型自己的 `oos_scores.parquet` 當底（同一批 date×stock_id、同一個標籤），
再把 `factor_values.z` 接上去——**與模型看到的是同一批列**，故可直接相比。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import polars as pl


def _ic(df: pl.DataFrame, col: str) -> tuple[float, float, int]:
    """逐日 spearman(col, label) 後取均值——與 `oos_rank_ic` 同一個定義。"""
    per_day = (
        df.select("date", col, "label")
        .drop_nulls()
        .group_by("date")
        .agg(pl.corr(pl.col(col).rank("average"), pl.col("label").rank("average")).alias("ic"))
        .drop_nulls("ic")
    )
    if not per_day.height:
        return (float("nan"), float("nan"), 0)
    return (float(per_day["ic"].mean()), float(per_day["ic"].gt(0).mean()), per_day.height)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--model", default="D:/project/twquant/data/models/lgbm_lw_dd_h60_20260225_1bd7c6a0"
    )
    ap.add_argument("--config", default="configs/ml_r_drawdown60_lw.yaml")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    from twquant.config import MlConfig, load_config

    cfg = load_config(Path(args.config), MlConfig)
    oos = pl.read_parquet(Path(args.model) / "oos_scores.parquet").filter(
        pl.col("label").is_not_null()
    )
    dates = oos["date"].unique().sort()
    print(f"模型 OOS：{oos.height:,} 列、{len(dates)} 個訊號日 {dates[0]} ~ {dates[-1]}")

    import psycopg

    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT date, stock_id, factor, z FROM serving.factor_values"
            " WHERE factor = ANY(%s) AND date = ANY(%s)",
            (list(cfg.features), list(dates)),
        )
        fv = pl.DataFrame(cur.fetchall(), schema=["date", "stock_id", "factor", "z"], orient="row")
    print(f"factor_values：{fv.height:,} 列、{fv['factor'].n_unique()} 個因子")

    wide = fv.pivot(on="factor", index=["date", "stock_id"], values="z")
    df = oos.join(wide, on=["date", "stock_id"], how="left")

    print("\n單因子 RankIC（與模型同一批列、同一個標籤）：")
    rows = [(c, *_ic(df, c)) for c in cfg.features if c in df.columns]
    for col, ic, pos, n in sorted(rows, key=lambda r: -abs(r[1])):
        print(f"  {col:32s} {ic:+.4f}　為正的日數 {pos:5.0%}　{n} 日")

    model_ic, model_pos, _ = _ic(df.rename({"score": "_s"}).with_columns(_s=pl.col("_s")), "_s")
    best = max(rows, key=lambda r: abs(r[1]))
    print(f"\n模型（重算，應等於 meta 的值）  {model_ic:+.4f}　為正的日數 {model_pos:.0%}")
    print(f"最強單因子 {best[0]}  {best[1]:+.4f}")
    print(f"→ **LightGBM 相對最強單因子的增量：{model_ic - abs(best[1]):+.4f}**")


if __name__ == "__main__":
    main()
