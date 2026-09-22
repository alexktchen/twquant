"""模型的優勢在哪一層流動性最大？——解釋 LQ1 為什麼比 E0 差。

我先前量的是「高流動性股票的**基礎率**較高」（無條件平均報酬）。
那與「模型在高流動性股票裡的**優勢**有多大」是兩回事，我把兩者混為一談了。
"""

import os
import sys

import polars as pl
import psycopg

sys.stdout.reconfigure(encoding="utf-8")
RANK = "lgbm_lw_net_h20_20260225_83890036"
with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as c:
    px = pl.DataFrame(
        c.execute(
            "SELECT date,stock_id,open,amount FROM serving.prices_daily "
            "WHERE date BETWEEN '2018-11-01' AND '2026-08-31' "
            "AND stock_id ~ '^[1-9][0-9]{3}$' AND open > 0"
        ).fetchall(),
        schema={"date": pl.Date, "stock_id": pl.String, "open": pl.Float64, "amount": pl.Float64},
        orient="row",
    )
    sc = pl.DataFrame(
        c.execute(
            "SELECT date,stock_id,rank_pct FROM serving.ml_scores WHERE model_id=%s", (RANK,)
        ).fetchall(),
        schema={"date": pl.Date, "stock_id": pl.String, "rp": pl.Float64},
        orient="row",
    )
o = dict(order_by="date")
px = px.sort("stock_id", "date").with_columns(
    pl.col("amount").rolling_median(20).over("stock_id", **o).alias("adv"),
    (
        pl.col("open").shift(-21).over("stock_id", **o)
        / pl.col("open").shift(-1).over("stock_id", **o)
        - 1.0
    ).alias("r"),
)
df = px.join(sc, on=["date", "stock_id"], how="inner").drop_nulls(["r", "adv", "rp"])
print(
    f"{'流動性層':<22}{'筆數':>11}{'檔/日':>7}{'基礎率（平均 20 日報酬）':>24}{'模型 IC':>11}{'前 10% 減全體':>14}"
)
for lo, hi, name in (
    (0, 1e8, "< 1 億"),
    (1e8, 3e8, "1~3 億"),
    (3e8, 1e9, "3~10 億"),
    (1e9, 1e18, "≥ 10 億"),
):
    g = df.filter(pl.col("adv").is_between(lo, hi, closed="left"))
    if g.height < 5000:
        continue
    ic = (
        g.group_by("date").agg(pl.corr("rp", "r", method="spearman").alias("ic"))["ic"].drop_nulls()
    )
    top = g.filter(pl.col("rp") >= 0.90)["r"].mean()
    per_day = g.group_by("date").len()["len"].median()
    print(
        f"{name:<22}{g.height:>11,}{per_day:>7,.0f}{g['r'].mean():>+24.2%}"
        f"{ic.mean():>+11.4f}{top - g['r'].mean():>+13.2%}pp"
    )
