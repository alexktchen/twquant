"""D-143 §十二：第九輪 B 臂（TAIL-B）挑到的，是不是只是「已經在飆的股票」？

1. 進場時「已經漲了多少」：過去 60／20 日漲幅，與同日宇宙比。
2. 命中率（60 日內最大漲幅 ≥ +50%）三種基準：
   a. 同日全市場（D-143 的 3.01× 就是這個）
   b. 同日宇宙（成交額前 30%、股價 ≥ 10，與策略同一個池子）
   c. **同日、同波動十分位 × 同 60 日漲幅十分位**（「一樣暴衝、一樣已經漲過」的股票）
3. 60 日前向報酬（t+1 開盤 → t+60 收盤）的平均／中位，同樣三種基準。
只用開發期：訊號日 2017-01-03 ~ 2026-02-28，前向窗在 2026-06 以前結束（D-109）。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sys

import polars as pl

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("TWQUANT_PG_DSN", "postgresql://twquant:twquant@localhost:5433/twquant")
import psycopg  # noqa: E402
from d143_verdict import _outcomes  # noqa: E402

from twquant.reporting.trade_journal import build_journal  # noqa: E402

LO, HI = dt.date(2017, 1, 3), dt.date(2026, 2, 28)
bundle = json.loads(
    pathlib.Path(r"D:\project\twquant\data\tracks_tail\tail_b_1x.json").read_text("utf-8")
)
journal = build_journal(bundle)
# 與 d146_report 相同的配對口徑：進場那一列的日期
entries = pl.DataFrame(
    [
        (dt.date.fromisoformat(str(d.date)), m.stock_id)
        for d in journal
        for m in d.moves
        if m.action == "enter"
    ],
    schema=["date", "stock_id"],
    orient="row",
)
print(f"TAIL-B 進場 {entries.height:,} 筆（{bundle['start']} ~ {bundle['end']}）")

out = _outcomes(LO, HI)  # (date, stock_id, hit)

with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn, conn.cursor() as cur:
    cur.execute(
        """select date, stock_id, open, close, amount from serving.prices_daily
           where date between %s and %s and stock_id ~ '^[1-9][0-9]{3}$' and close > 0""",
        (LO - dt.timedelta(days=200), HI + dt.timedelta(days=130)),
    )
    rows = cur.fetchall()
px = (
    pl.DataFrame(rows, schema=["date", "stock_id", "open", "close", "amount"], orient="row")
    .with_columns(
        pl.col("date").cast(pl.Date),
        *(pl.col(c).cast(pl.Float64) for c in ("open", "close", "amount")),
    )
    .sort(["stock_id", "date"])
)
g = "stock_id"
r1 = pl.col("close") / pl.col("close").shift(1).over(g) - 1.0
px = px.with_columns(r=r1).with_columns(
    past60=pl.col("close") / pl.col("close").shift(60).over(g) - 1.0,
    past20=pl.col("close") / pl.col("close").shift(20).over(g) - 1.0,
    vol20=pl.col("r").rolling_std(20).over(g),
    adv20=pl.col("amount").rolling_median(20).over(g),
    fwd60=pl.col("close").shift(-60).over(g) / pl.col("open").shift(-1).over(g) - 1.0,
)
# 宇宙：同日成交額前 30%、股價 ≥ 10（策略的池子）
px = px.with_columns(
    adv_rank=pl.col("adv20").rank(method="average").over("date")
    / pl.col("adv20").count().over("date")
)
uni = px.filter(
    (pl.col("adv_rank") >= 0.70)
    & (pl.col("close") >= 10.0)
    & pl.col("past60").is_not_null()
    & pl.col("vol20").is_not_null()
)
uni = uni.with_columns(
    vdec=(pl.col("vol20").rank(method="ordinal").over("date") * 10 / (pl.len().over("date") + 1))
    .floor()
    .cast(pl.Int32),
    mdec=(pl.col("past60").rank(method="ordinal").over("date") * 10 / (pl.len().over("date") + 1))
    .floor()
    .cast(pl.Int32),
)
full = uni.join(out, on=["date", "stock_id"], how="inner").filter(pl.col("date").is_between(LO, HI))
cell = full.group_by(["date", "vdec", "mdec"]).agg(
    c_hit=pl.col("hit").mean(), c_fwd=pl.col("fwd60").mean(), c_n=pl.len()
)
day = full.group_by("date").agg(
    u_hit=pl.col("hit").mean(), u_fwd=pl.col("fwd60").mean(), u_p60=pl.col("past60").median()
)
allm = out.group_by("date").agg(m_hit=pl.col("hit").mean())

e = (
    entries.join(full, on=["date", "stock_id"], how="inner")
    .join(cell, on=["date", "vdec", "mdec"], how="left")
    .join(day, on="date", how="left")
    .join(allm, on="date", how="left")
)
print(f"可比對 {e.height:,} 筆（其餘不在宇宙或缺資料）\n")

print("── 1. 進場時已經漲了多少")
print(
    f"   過去 60 日漲幅中位：TAIL-B {e['past60'].median():+.1%}　同日宇宙 {e['u_p60'].median():+.1%}"
)
print(f"   過去 20 日漲幅中位：TAIL-B {e['past20'].median():+.1%}")
for t in (0.3, 0.5, 1.0):
    print(f"   過去 60 日已漲 ≥ {t:.0%}：{(e['past60'] >= t).mean():.0%} 的進場")
print(
    f"   波動十分位：{(e['vdec'] >= 9).mean():.0%} 落在最高一格、{(e['vdec'] >= 7).mean():.0%} 在前三格"
)
print(
    f"   60 日漲幅十分位：{(e['mdec'] >= 9).mean():.0%} 落在最高一格、{(e['mdec'] >= 7).mean():.0%} 在前三格\n"
)

hit = e["hit"].mean()
print("── 2. 60 日內再漲 ≥ 50% 的比例")
print(f"   TAIL-B 進場         {hit:.2%}")
print(
    f"   同日全市場          {e['m_hit'].mean():.2%}　→ 倍率 {hit / e['m_hit'].mean():.2f}×（D-143 報的那個）"
)
print(f"   同日宇宙            {e['u_hit'].mean():.2%}　→ 倍率 {hit / e['u_hit'].mean():.2f}×")
print(
    f"   同波動×同漲幅的股票 {e['c_hit'].mean():.2%}　→ 倍率 {hit / e['c_hit'].mean():.2f}×　← 模型真正多出來的\n"
)

f = e.filter(pl.col("fwd60").is_not_null())
print("── 3. 60 日前向報酬（未扣成本；t+1 開盤 → t+60 收盤）")
print(f"   TAIL-B 進場         平均 {f['fwd60'].mean():+.2%}　中位 {f['fwd60'].median():+.2%}")
print(f"   同日宇宙            平均 {f['u_fwd'].mean():+.2%}")
print(
    f"   同波動×同漲幅的股票 平均 {f['c_fwd'].mean():+.2%}　→ 模型多出 {f['fwd60'].mean() - f['c_fwd'].mean():+.2%}"
)
