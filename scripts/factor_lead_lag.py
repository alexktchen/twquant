"""檢驗使用者的論點：籌碼領先、技術確認、營收落後？

量兩件事：
(1) 每個因子的 IC 在哪個持有期達到高峰——領先指標的 IC 應該在短期就出現並衰退，
    落後／持續型特徵的 IC 則平坦。
(2) 最高分位的每筆期望值。

宇宙：日均成交 ≥ 3 億、股價 > 10。進出用開盤價、已扣成本。**這是探索。**
"""

import os
import sys

import polars as pl
import psycopg

sys.stdout.reconfigure(encoding="utf-8")
COST = 0.00435
FACTORS = [
    "chip.foreign_net_10d",
    "chip.inst_net_10d",
    "chip.trust_net_10d",
    "chip.foreign_streak",
    "chip.trust_streak",
    "chip.margin_velocity_5d",
    "fundamental.revenue_yoy",
    "fundamental.revenue_accel",
    "fundamental.sue",
    "price.mom_20d",
    "price.vol_20d",
]
HS = (5, 10, 20, 60)
with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as c:
    px = pl.DataFrame(
        c.execute(
            "SELECT date,stock_id,open,close,amount FROM serving.prices_daily "
            "WHERE date BETWEEN '2018-11-01' AND '2026-08-31' "
            "AND stock_id ~ '^[1-9][0-9]{3}$' AND open > 0 AND close > 10"
        ).fetchall(),
        schema={
            "date": pl.Date,
            "stock_id": pl.String,
            "open": pl.Float64,
            "close": pl.Float64,
            "amount": pl.Float64,
        },
        orient="row",
    )
    fv = pl.DataFrame(
        c.execute(
            "SELECT date,stock_id,factor,pct FROM serving.factor_values "
            "WHERE factor = ANY(%s) AND date BETWEEN '2018-11-01' AND '2026-08-31'",
            (FACTORS,),
        ).fetchall(),
        schema={"date": pl.Date, "stock_id": pl.String, "factor": pl.String, "pct": pl.Float64},
        orient="row",
    )
o = dict(order_by="date")
px = px.sort("stock_id", "date").with_columns(
    pl.col("amount").rolling_median(20).over("stock_id", **o).alias("adv"),
    pl.col("open").shift(-1).over("stock_id", **o).alias("ent"),
)
for h in HS:
    px = px.with_columns(
        (pl.col("open").shift(-h - 1).over("stock_id", **o) / pl.col("ent") - 1.0 - COST).alias(
            f"r{h}"
        )
    )
base = px.drop_nulls(["ent", "adv"]).filter(pl.col("adv") >= 3e8)
j = fv.join(
    base.select("date", "stock_id", *[f"r{h}" for h in HS]), on=["date", "stock_id"], how="inner"
)
print(
    f"{'因子':<30}"
    + "".join(f"{f'IC@{h}日':>10}" for h in HS)
    + f"{'最高分位@60日':>14}{'涵蓋':>9}"
)
for f in FACTORS:
    g = j.filter(pl.col("factor") == f)
    if g.height < 10_000:
        print(f"{f:<30}{'（樣本不足）':>30}")
        continue
    ics = []
    for h in HS:
        d = g.drop_nulls(f"r{h}")
        ic = d.group_by("date").agg(pl.corr("pct", f"r{h}", method="spearman").alias("ic"))["ic"]
        ics.append(ic.mean())
    top = g.filter(pl.col("pct") >= 0.80).drop_nulls("r60")["r60"]
    print(
        f"{f:<30}"
        + "".join(f"{i:>+10.4f}" for i in ics)
        + f"{top.mean():>+14.2%}{g['date'].n_unique():>7,}天"
    )
