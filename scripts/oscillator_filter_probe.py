"""技術指標當**進場濾網**值多少？KD／MACD／布林／RSI／量能，與均線同框架比較。

評分一律用「每筆期望值」：進場＝訊號日隔天開盤，持有 60 個交易日後開盤出場，已扣成本。
宇宙＝日均成交 ≥ 3 億、股價 > 10。基準線是「ML 前 10%、不加任何濾網」。

**這是探索。** 一次測十幾個指標，最好看的那個有相當機率只是運氣——
要當真必須挑一個事前登記、在沒看過的資料上驗證。
"""

import os
import sys

import polars as pl
import psycopg

sys.stdout.reconfigure(encoding="utf-8")
COST, H, RANK = 0.00435, 60, "lgbm_lw_net_h20_20260225_83890036"
with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as c:
    px = pl.DataFrame(
        c.execute(
            "SELECT date,stock_id,open,high,low,close,volume,amount FROM serving.prices_daily "
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
            "volume": pl.Float64,
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
g = lambda e: e.over("stock_id", **o)  # noqa: E731
px = px.sort("stock_id", "date")
# ── 指標 ──────────────────────────────────────────────
d = pl.col("close") - g(pl.col("close").shift(1))
px = px.with_columns(
    g(pl.col("amount").rolling_median(20)).alias("adv"),
    g(pl.col("close").rolling_mean(20)).alias("ma20"),
    g(pl.col("close").rolling_mean(60)).alias("ma60"),
    g(pl.col("close").ewm_mean(span=12)).alias("e12"),
    g(pl.col("close").ewm_mean(span=26)).alias("e26"),
    g(pl.col("low").rolling_min(9)).alias("l9"),
    g(pl.col("high").rolling_max(9)).alias("h9"),
    g(pl.col("close").rolling_std(20)).alias("sd20"),
    g(pl.col("volume").rolling_mean(20)).alias("v20"),
    g(pl.when(d > 0).then(d).otherwise(0.0).ewm_mean(alpha=1 / 14)).alias("up14"),
    g(pl.when(d < 0).then(-d).otherwise(0.0).ewm_mean(alpha=1 / 14)).alias("dn14"),
)
px = px.with_columns(
    (100 * (pl.col("close") - pl.col("l9")) / (pl.col("h9") - pl.col("l9"))).alias("rsv"),
    (pl.col("e12") - pl.col("e26")).alias("dif"),
    (100 * pl.col("up14") / (pl.col("up14") + pl.col("dn14"))).alias("rsi"),
    ((pl.col("close") - (pl.col("ma20") - 2 * pl.col("sd20"))) / (4 * pl.col("sd20"))).alias(
        "pctb"
    ),  # 布林 %B：0＝下軌、1＝上軌
    (pl.col("volume") / pl.col("v20")).alias("vr"),
)
px = px.with_columns(
    g(pl.col("rsv").ewm_mean(alpha=1 / 3)).alias("k"),
    g(pl.col("dif").ewm_mean(span=9)).alias("dea"),
    (4 * pl.col("sd20") / pl.col("ma20")).alias("bw"),
)  # 布林帶寬
px = px.with_columns(
    g(pl.col("k").ewm_mean(alpha=1 / 3)).alias("dd"),
    (pl.col("dif") - g(pl.col("dif").ewm_mean(span=9))).alias("hist"),
)
px = px.with_columns(
    g(pl.col("open").shift(-1)).alias("ent"),
    g(pl.col("k").shift(1)).alias("k1"),
    g(pl.col("dd").shift(1)).alias("d1"),
    g(pl.col("hist").shift(1)).alias("h1"),
    g(pl.col("bw").rolling_mean(60)).alias("bw60"),
)
px = px.with_columns((g(pl.col("open").shift(-H - 1)) / pl.col("ent") - 1.0 - COST).alias("r"))
df = (
    px.join(sc, on=["date", "stock_id"], how="inner")
    .drop_nulls(
        ["r", "adv", "ml" if False else "rp", "k", "dd", "hist", "rsi", "pctb", "vr", "bw60"]
    )
    .filter(pl.col("adv") >= 3e8)
)
base = df.filter(pl.col("rp") >= 0.90)
b = base["r"]
print(
    f"基準：ML 前 10%、無濾網　{b.len():,} 筆　每筆期望值 {b.mean():+.2%}　勝率 {(b > 0).mean():.0%}"
)
print(f"（持有 {H} 日、已扣成本 {COST:.2%}、日均成交 ≥ 3 億）\n")
print(f"{'進場濾網':<32}{'筆數':>9}{'留下比例':>9}{'勝率':>7}{'每筆期望值':>11}{'與基準差':>10}")


def show(cond: pl.Expr, label: str) -> None:
    s = base.filter(cond)["r"]
    if s.len() < 500:
        print(f"{label:<32}{s.len():>9,}   樣本過少")
        return
    print(
        f"{label:<32}{s.len():>9,}{s.len() / b.len():>9.0%}{(s > 0).mean():>7.0%}"
        f"{s.mean():>+11.2%}{(s.mean() - b.mean()) * 100:>+8.2f}pp"
    )


show(pl.col("close") > pl.col("ma20"), "站上 MA20（對照）")
show(pl.col("close") > pl.col("ma60"), "站上 MA60")
print()
show((pl.col("k") > pl.col("dd")) & (pl.col("k1") <= pl.col("d1")), "KD 黃金交叉（當天）")
show(pl.col("k") < 20, "KD 的 K < 20（超賣）")
show(pl.col("k") > 80, "KD 的 K > 80（超買）")
show((pl.col("k") > pl.col("dd")), "K 在 D 之上")
print()
show((pl.col("hist") > 0) & (pl.col("h1") <= 0), "MACD 黃金交叉（當天）")
show(pl.col("hist") > 0, "MACD 柱狀為正")
show(pl.col("dif") > 0, "MACD 的 DIF > 0")
print()
show(pl.col("rsi") < 30, "RSI < 30（超賣）")
show(pl.col("rsi") > 70, "RSI > 70（超買）")
show(pl.col("rsi").is_between(40, 60), "RSI 40~60（中性）")
print()
show(pl.col("pctb") > 1.0, "站上布林上軌")
show(pl.col("pctb") < 0.0, "跌破布林下軌")
show(pl.col("pctb").is_between(0.5, 1.0), "布林中軌以上")
show(pl.col("bw") < pl.col("bw60") * 0.7, "布林帶寬收縮（盤整待變）")
print()
show(pl.col("vr") > 2.0, "成交量 > 20 日均量 2 倍")
show(pl.col("vr") < 0.7, "成交量 < 20 日均量 0.7 倍")
