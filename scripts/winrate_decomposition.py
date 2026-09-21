"""為什麼「追高」和「接刀子」的勝率都是 75~82%？——把勝率拆成出場的函數。

假說：勝率 ≈ P(持有期內曾經碰到停利門檻)，主要由「門檻多近」與「等多久」決定，
      進場方式的影響小得多。若成立，**隨機進場**配同樣出場也該落在同一區間。

進出皆用開盤價（訊號日隔天開盤進；觸發日隔天開盤出），已扣成本。
**這是探索**，用來理解機制，不是判定。
"""

import os
import sys

import polars as pl
import psycopg

sys.stdout.reconfigure(encoding="utf-8")
COST, RANK, MAXH = 0.00435, "lgbm_lw_net_h20_20260225_83890036", 20
with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as c:
    px = pl.DataFrame(
        c.execute(
            "SELECT date,stock_id,open,high,low,close,amount FROM serving.prices_daily "
            "WHERE date BETWEEN '2018-11-01' AND '2026-08-31' "
            "AND stock_id ~ '^[1-9][0-9]{3}$' AND open > 0 AND close > 10"
        ).fetchall(),
        schema={
            "date": pl.Date,
            "stock_id": pl.String,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "amount": pl.Float64,
        },
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
    [
        pl.col("high").rolling_max(20).shift(1).over("stock_id", **o).alias("hh20"),
        pl.col("low").rolling_min(20).shift(1).over("stock_id", **o).alias("ll20"),
        pl.col("close").rolling_mean(5).over("stock_id", **o).alias("ma5"),
        pl.col("close").rolling_mean(60).over("stock_id", **o).alias("ma60"),
        pl.col("amount").rolling_median(20).over("stock_id", **o).alias("adv"),
        pl.col("close").shift(1).over("stock_id", **o).alias("pc"),
        (pl.col("close") / pl.col("close").shift(5).over("stock_id", **o) - 1.0).alias("r5"),
        pl.col("open").shift(-1).over("stock_id", **o).alias("ent"),
    ]
)
px = px.with_columns(
    pl.concat_list(
        [pl.col("open").shift(-k - 1).over("stock_id", **o) for k in range(1, MAXH + 1)]
    ).alias("outs")
)
df = px.join(sc, on=["date", "stock_id"], how="inner").drop_nulls(
    ["ent", "outs", "hh20", "ll20", "ma60", "adv"]
)
df = df.filter(pl.col("adv") >= 3e8)  # 使用者：100 元上下幾千張 ≈ 日均 3 億
ENTRIES = {
    "隨機進場（全部交易日）": pl.lit(True),
    "站上 20 日前高（追高）": (pl.col("close") > pl.col("hh20")) & (pl.col("pc") <= pl.col("hh20")),
    "跌破 20 日前低（接刀）": (pl.col("close") < pl.col("ll20")) & (pl.col("pc") >= pl.col("ll20")),
    "上升趨勢回檔": (pl.col("close") > pl.col("ma60"))
    & (pl.col("close") < pl.col("ma5"))
    & (pl.col("r5") <= -0.10),
    "模型前 10%": pl.col("rp") >= 0.90,
}


def run(g: pl.DataFrame, theta: float, hold: int) -> tuple[int, float, float]:
    flags = pl.concat_list(
        [(pl.col("outs").list.get(k) / pl.col("ent") - 1.0 - COST > theta) for k in range(hold)]
    )
    ex = (
        pl.when(flags.list.any())
        .then(pl.col("outs").list.get(flags.list.arg_max()))
        .otherwise(pl.col("outs").list.get(hold - 1))
    )
    s = g.select((ex / pl.col("ent") - 1.0 - COST).alias("x")).drop_nulls()["x"]
    return s.len(), float((s > 0).mean()), float(s.mean())


print("【一】固定持有 10 日，改變停利門檻——勝率是門檻的函數嗎？\n")
print(f"{'進場方式':<24}" + "".join(f"{f'+{t:.0%}':>16}" for t in (0.005, 0.02, 0.05, 0.10)))
print(f"{'':<24}" + "".join(f"{'勝率／期望值':>16}" for _ in range(4)))
for name, cond in ENTRIES.items():
    g = df.filter(cond)
    cells = []
    for t in (0.005, 0.02, 0.05, 0.10):
        n, wr, ev = run(g, t, 10)
        cells.append(f"{wr:.0%}／{ev:+.2f}%".rjust(16) if n >= 500 else "—".rjust(16))
    print(f"{name:<24}" + "".join(cells))
print("\n【二】固定停利 +0.5%，改變持有天數——勝率是天數的函數嗎？\n")
print(f"{'進場方式':<24}" + "".join(f"{f'{h} 日':>16}" for h in (3, 5, 10, 20)))
for name, cond in ENTRIES.items():
    g = df.filter(cond)
    cells = []
    for h in (3, 5, 10, 20):
        n, wr, ev = run(g, 0.005, h)
        cells.append(f"{wr:.0%}／{ev:+.2f}%".rjust(16) if n >= 500 else "—".rjust(16))
    print(f"{name:<24}" + "".join(cells))
