"""預篩：五支震盪指標值不值得重訓？——不寫資料庫，記憶體裡算完就量。

兩個問題決定一個因子有沒有用：
  (1) 它自己跟未來報酬相關嗎（IC）
  (2) 它跟現有特徵有多像（相關係數）——很像就沒帶來新資訊

**這不取代 D-139 的登記測試**，預測也已經寫死不改。這只是先知道值不值得花那三小時。
"""

import datetime as dt
import pathlib
import sys

import polars as pl

import twquant.factors.builtins  # noqa: F401  # 匯入觸發因子註冊
from twquant.config import ServingConfig, load_config
from twquant.data.serving import store
from twquant.data.serving.pg_view import PgTableSource
from twquant.factors.compute import compute

OSC = [
    "price.kd_k_9",
    "price.rsi_14",
    "price.bollinger_pctb_20",
    "price.bollinger_width_20",
    "price.macd_hist_norm",
]
BASE = [
    "fundamental.revenue_accel",
    "fundamental.revenue_yoy",
    "price.intraday_ret_20d",
    "price.mom_20d",
    "price.mom_60d",
    "price.overnight_ret_20d",
    "price.residual_reversal_5d",
    "price.reversal_5d",
    "price.rs_market_20d",
    "price.vol_20d",
    "volume.turnover_z_20d",
    "volume.vol_ratio_5d",
]
H, END = 20, "2026-08-28"
sys.stdout.reconfigure(encoding="utf-8")
cfg = load_config(pathlib.Path("configs/serving.yaml"), ServingConfig)
conn = store.connect(cfg)
try:
    src = PgTableSource(conn, cfg.pg_schema)

    osc = compute(OSC, src.view(dt.date.fromisoformat(END)), dates=None)
    osc = osc.filter(
        pl.col("stock_id").str.contains(r"^[1-9][0-9]{3}$")
        & (pl.col("date") >= dt.date(2018, 1, 1))
    ).drop_nulls("value")
    print(f"震盪指標算完：{osc.height:,} 列", flush=True)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT date,stock_id,factor,pct FROM serving.factor_values "
            "WHERE factor = ANY(%s) AND date BETWEEN '2018-01-01' AND %s",
            (BASE, END),
        )
        base = pl.DataFrame(
            cur.fetchall(),
            schema={"date": pl.Date, "stock_id": pl.String, "factor": pl.String, "pct": pl.Float64},
            orient="row",
        )
        cur.execute(
            "SELECT date,stock_id,open FROM serving.prices_daily "
            "WHERE date BETWEEN '2018-01-01' AND '2026-09-18' "
            "AND stock_id ~ '^[1-9][0-9]{3}$' AND open > 0"
        )
        px = pl.DataFrame(
            cur.fetchall(),
            schema={"date": pl.Date, "stock_id": pl.String, "open": pl.Float64},
            orient="row",
        )
finally:
    conn.close()
o = dict(order_by="date")
px = (
    px.sort("stock_id", "date")
    .with_columns(
        (
            pl.col("open").shift(-H - 1).over("stock_id", **o)
            / pl.col("open").shift(-1).over("stock_id", **o)
            - 1.0
        ).alias("r")
    )
    .drop_nulls("r")
    .select("date", "stock_id", "r")
)
# 震盪指標轉成當日橫斷面百分位，與 base 的 pct 同口徑
osc = osc.with_columns(
    (pl.col("value").rank().over(["date", "factor"]) - 1)
    .truediv(pl.max_horizontal(pl.col("value").count().over(["date", "factor"]) - 1, pl.lit(1)))
    .alias("pct")
).select("date", "stock_id", "factor", "pct")
allf = pl.concat([base, osc]).join(px, on=["date", "stock_id"], how="inner")
print(f"\n{'因子':<32}{'IC@20日':>10}{'正日比例':>10}{'與現有特徵最高相關':>20}")
wide = allf.pivot(on="factor", index=["date", "stock_id"], values="pct")
for f in OSC + BASE:
    g = allf.filter(pl.col("factor") == f)
    ic = (
        g.group_by("date")
        .agg(pl.corr("pct", "r", method="spearman").alias("ic"))["ic"]
        .drop_nulls()
    )
    tag = "★ " if f in OSC else "  "
    if f in OSC:
        cors = {
            b: abs(wide.select(pl.corr(f, b, method="spearman")).item() or 0.0)
            for b in BASE
            if b in wide.columns
        }
        top = max(cors.items(), key=lambda kv: kv[1])
        extra = f"{top[1]:.2f} {top[0].split('.')[-1]}"
    else:
        extra = ""
    print(f"{tag}{f:<30}{ic.mean():>+10.4f}{(ic > 0).mean():>10.0%}{extra:>20}")
