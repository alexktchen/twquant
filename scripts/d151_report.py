"""D-151 §四／§八：第十二輪報告——**只報不判**。

    TWQUANT_PG_DSN=... uv run --all-groups python scripts/d151_report.py \\
        --arm A configs/ml_quiet_a.yaml D:/project/twquant/data/models/<A 的目錄> \\
        --arm B configs/ml_hot_b.yaml   D:/project/twquant/data/models/<B 的目錄> ... [--vault]

預設讀各模型目錄的 `oos_scores.parquet`（walk-forward 樣本外；訓練時已只含該模型的狀態樣本）。
`--vault`：用最終模型（只訓練到 2026-04-30）對保留段 **2026-05-01 ~ 08-03** 打分（D-151 §八），
同一套算法報。行情只讀到 2026-08-31——20 日前向窗在此前結束，一天都不碰第二保留期（D-109）。

主報**配對基準**（D-143 §十二 的教訓）：
- A（安靜股 → 20 日 +30%）：同日安靜股中「同 20 日波動十分位 × 同 60 日漲幅十分位」的命中率。
- B（已飆股 → 20 日再跌 15%）：同月已飆股中「同波動十分位 × 同 20 日漲幅十分位」（已飆股一天只有幾十檔，
  逐日切十分位每格不到一檔，故在全樣本上切十分位、同月配對——登記沒寫到這一層，這是操作定義）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import pathlib
import sys

import lightgbm as lgb
import numpy as np
import polars as pl
import psycopg

from twquant.config import MlConfig, load_config
from twquant.data.serving.ml_store import (
    read_corporate_events,
    read_factor_frames,
    read_index_close,
)
from twquant.ml import train as mlt
from twquant.ml.context import market_state_features
from twquant.ml.dataset import (
    assemble,
    attach_adjustment,
    down_probability_labels,
    state_pool,
    tail_up_labels,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = "serving"
DATA_START = dt.date(2015, 6, 1)
DATA_END = dt.date(2026, 8, 31)  # 20 日前向窗在此前結束；第二保留期從 09-09 起
VAULT = (dt.date(2026, 5, 1), dt.date(2026, 8, 3))
TOP_N = 20  # A：每日模型機率前 20 名（D-151 §四）
H = 20
UP, DROP, DROP2, REGAIN = 0.30, 0.15, 0.20, 0.20
LIMIT_GAP = 0.095  # 隔日開盤相對前收 ≥ 9.5% 視為開盤漲停、買不到（台股漲幅 10%）
PATTERNS = (
    "price.bb_squeeze_120",
    "price.ma_tangle",
    "price.vp_poc_dist_60",
    "price.vp_support_hold_20",
    "price.vp_near_share_60",
)
_S = "stock_id"


def load_market(conn: psycopg.Connection) -> pl.DataFrame:
    """四碼普通股的還原行情與報告要用的逐檔欄位（只讀到 DATA_END）。"""
    with conn.cursor() as cur:
        cur.execute(
            f"""select date, stock_id, open, high, low, close, volume, amount
                from "{SCHEMA}".prices_daily
                where date between %s and %s and stock_id ~ '^[1-9][0-9]{{3}}$' and close > 0""",
            (DATA_START, DATA_END),
        )
        rows = cur.fetchall()
    px = pl.DataFrame(
        rows,
        schema=["date", _S, "open", "high", "low", "close", "volume", "amount"],
        orient="row",
    ).with_columns(
        pl.col("date").cast(pl.Date),
        *(pl.col(c).cast(pl.Float64) for c in ("open", "high", "low", "close", "volume", "amount")),
    )
    px = attach_adjustment(px, read_corporate_events(conn, SCHEMA, DATA_END)).sort(_S, "date")
    p = pl.col("close") / pl.col("adj")
    entry = (pl.col("open") / pl.col("adj")).shift(-1).over(_S)
    fwd = [p.shift(-k).over(_S) / entry - 1.0 for k in range(1, H + 1)]
    return px.with_columns(
        vol20=(pl.col("close") / pl.col("close").shift(1).over(_S) - 1.0).rolling_std(20).over(_S),
        past60=p / p.shift(60).over(_S) - 1.0,
        past20=p / p.shift(20).over(_S) - 1.0,
        gap=pl.col("open").shift(-1).over(_S) / pl.col("close") - 1.0,
        fwd20=p.shift(-H).over(_S) / entry - 1.0,
        maxfwd=pl.max_horizontal(fwd),
        minfwd=pl.min_horizontal(fwd),
    ).with_columns(
        # 前向窗不完整（資料到期、停牌過久）→ 沒有答案，不是 0
        *(
            pl.when(pl.col("fwd20").is_not_null()).then(pl.col(c)).alias(c)
            for c in ("maxfwd", "minfwd")
        )
    )


def _dec(col: str, over: str | None) -> pl.Expr:
    rank = pl.col(col).rank("ordinal")
    n = pl.col(col).count()
    if over is not None:
        rank, n = rank.over(over), n.over(over)
    return ((rank - 1) * 10 / n).floor().cast(pl.Int32)


def _block_t(frame: pl.DataFrame, diff: str) -> tuple[float, float, int]:
    """月區塊 t：同一檔連續幾天的標籤高度相關，不能逐列當獨立樣本。"""
    m = frame.group_by(pl.col("date").dt.truncate("1mo")).agg(pl.col(diff).mean())[diff].to_numpy()
    m = m[np.isfinite(m)]
    if m.size < 2:
        return float("nan"), float("nan"), int(m.size)
    return float(m.mean()), float(m.mean() / (m.std(ddof=1) / math.sqrt(m.size))), int(m.size)


def _importance(model_dir: pathlib.Path, features: list[str]) -> list[tuple[str, float]]:
    booster = lgb.Booster(model_file=str(model_dir / "model.txt"))
    gain = booster.feature_importance(importance_type="gain")
    total = float(gain.sum()) or 1.0
    return sorted(zip(features, (float(g) / total for g in gain), strict=True), key=lambda x: -x[1])


def _print_importance(model_dir: pathlib.Path, features: list[str], watch: tuple[str, ...]) -> None:
    imp = _importance(model_dir, features)
    top = "、".join(f"{n.split('.', 1)[1]} {s:.1%}" for n, s in imp[:10])
    print(f"  重要度前十：{top}")
    share = {n: s for n, s in imp}
    watched = [w for w in watch if w in share]
    if watched:
        print(
            f"  指定特徵合計 {sum(share[w] for w in watched):.1%}："
            + "、".join(
                f"{w.split('.', 1)[1]} 第{[n for n, _ in imp].index(w) + 1}名" for w in watched
            )
        )


def report_up(name: str, oos: pl.DataFrame, mk: pl.DataFrame, conn: psycopg.Connection) -> None:
    """模型 A 類：安靜股 → 20 日內碰到 +30%。"""
    df = oos.join(
        mk.select("date", _S, "vol20", "past60", "fwd20", "maxfwd", "gap"), on=["date", _S]
    ).filter(
        pl.col("label").is_not_null()
        & pl.col("vol20").is_not_null()
        & pl.col("past60").is_not_null()
    )
    df = df.with_columns(vdec=_dec("vol20", "date"), mdec=_dec("past60", "date"))
    cells = df.group_by("date", "vdec", "mdec").agg(
        c_hit=pl.col("label").mean(), c_fwd=pl.col("fwd20").mean()
    )
    day = df.group_by("date").agg(q_hit=pl.col("label").mean(), q_fwd=pl.col("fwd20").mean())
    market = (
        mk.filter(pl.col("maxfwd").is_not_null())
        .group_by("date")
        .agg(m_hit=(pl.col("maxfwd") >= UP).cast(pl.Float64).mean())
    )
    df = (
        df.join(cells, on=["date", "vdec", "mdec"])
        .join(day, on="date")
        .join(market, on="date", how="left")
    )
    picks = df.filter(
        pl.col("score").rank("ordinal", descending=True).over("date") <= TOP_N
    ).with_columns(d_hit=pl.col("label") - pl.col("c_hit"))
    hit = float(picks["label"].mean())
    lo, hi = df["date"].min(), df["date"].max()
    print(f"\n══ {name}（{lo} ~ {hi}；每日前 {TOP_N} 名，共 {picks.height:,} 筆）")
    print(
        f"  安靜股樣本 {df.height:,} 列；基礎率（安靜股 20 日內碰到 +30%）{float(df['label'].mean()):.2%}"
    )
    for label, col in (
        ("同日全市場", "m_hit"),
        ("同日安靜股", "q_hit"),
        ("配對（同波動×同漲幅）", "c_hit"),
    ):
        base = float(picks[col].mean())
        print(f"  命中 {hit:.2%}　vs {label} {base:.2%}　→ 倍率 {hit / base:.2f}×")
    mean_d, t, nm = _block_t(picks, "d_hit")
    print(f"  配對超額命中 {mean_d:+.2%}（月區塊 t {t:+.2f}，{nm} 個月）")
    f = picks.filter(pl.col("fwd20").is_not_null())
    print(
        f"  20 日報酬：平均 {float(f['fwd20'].mean()):+.2%}　中位 {float(f['fwd20'].median()):+.2%}　"
        f"配對組平均 {float(f['c_fwd'].mean()):+.2%}　安靜股平均 {float(f['q_fwd'].mean()):+.2%}（未扣成本）"
    )
    yr = (
        picks.group_by(pl.col("date").dt.year().alias("y"))
        .agg(h=pl.col("label").sum(), e=pl.col("c_hit").sum(), n=pl.len())
        .sort("y")
    )
    print(
        "  逐年配對倍率："
        + "　".join(
            f"{r['y']} {r['h'] / r['e']:.2f}×" for r in yr.iter_rows(named=True) if r["e"] > 0
        )
    )
    calib = (
        df.with_columns(sd=_dec("score", "date"))
        .group_by("sd")
        .agg(pl.col("label").mean())
        .sort("sd")
    )
    print(
        "  機率十分位的實際命中："
        + "　".join(f"D{r['sd'] + 1} {r['label']:.1%}" for r in calib.iter_rows(named=True))
    )
    print(
        f"  可交易性：隔日開盤漲停（≥{LIMIT_GAP:.1%}）{float((picks['gap'] >= LIMIT_GAP).mean()):.1%}"
    )
    won = picks.filter((pl.col("label") == 1.0) & pl.col("fwd20").is_not_null())
    if won.height:
        print(
            f"  回吐：命中者 {won.height:,} 筆，持有到第 20 日收盤中位 {float(won['fwd20'].median()):+.1%}、"
            f"< +10% 的 {float((won['fwd20'] < 0.10).mean()):.0%}"
        )
    _regime_split(picks, mk)
    _patterns_alone(df, conn)


def _regime_split(picks: pl.DataFrame, mk: pl.DataFrame) -> None:
    """大盤溫度（市場寬度 20 日）三分位下的配對倍率（D-151 預測 8）。"""
    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn:
        idx = read_index_close(conn, SCHEMA, "TAIEX", DATA_START, DATA_END)
    ctx = market_state_features(idx, mk.select("date", _S, "close"), window=20).select(
        "date", "ctx.breadth_ma20"
    )
    p = picks.join(ctx, on="date", how="left").filter(pl.col("ctx.breadth_ma20").is_not_null())
    cuts = p["ctx.breadth_ma20"].quantile(1 / 3), p["ctx.breadth_ma20"].quantile(2 / 3)
    parts = []
    for tag, cond in (
        ("冷", pl.col("ctx.breadth_ma20") <= cuts[0]),
        ("中", (pl.col("ctx.breadth_ma20") > cuts[0]) & (pl.col("ctx.breadth_ma20") <= cuts[1])),
        ("熱", pl.col("ctx.breadth_ma20") > cuts[1]),
    ):
        s = p.filter(cond)
        e = float(s["c_hit"].sum())
        parts.append(f"{tag} {float(s['label'].sum()) / e:.2f}×" if e > 0 else f"{tag} —")
    print("  大盤寬度三分位的配對倍率：" + "　".join(parts))


def _patterns_alone(df: pl.DataFrame, conn: psycopg.Connection) -> None:
    """五支型態因子不經模型：安靜股內最低／最高十分位的配對倍率（D-151 預測 4）。"""
    lo, hi = df["date"].min(), df["date"].max()
    frames = read_factor_frames(conn, SCHEMA, list(PATTERNS), lo, hi)
    out = []
    for name in PATTERNS:
        fr = frames.get(name)
        if fr is None or fr.is_empty():
            out.append(f"{name.split('.', 1)[1]} 無資料")
            continue
        j = df.join(fr.rename({"value": "_f"}), on=["date", _S]).with_columns(fd=_dec("_f", "date"))
        cells = []
        for tag, d in (("最低", 0), ("最高", 9)):
            s = j.filter(pl.col("fd") == d)
            e = float(s["c_hit"].sum())
            cells.append(f"{tag} {float(s['label'].sum()) / e:.2f}×" if e > 0 else f"{tag} —")
        out.append(f"{name.split('.', 1)[1]}（{'／'.join(cells)}）")
    print("  型態因子單獨（安靜股內十分位 vs 配對）：" + "　".join(out))


def report_down(name: str, oos: pl.DataFrame, mk: pl.DataFrame) -> None:
    """模型 B 類：已飆股 → 20 日內再跌 15%（t+1 開盤基準）。看決策價值，不只看命中。"""
    df = oos.join(
        mk.select("date", _S, "vol20", "past20", "fwd20", "maxfwd", "minfwd"), on=["date", _S]
    ).filter(
        pl.col("label").is_not_null()
        & pl.col("vol20").is_not_null()
        & pl.col("fwd20").is_not_null()
    )
    df = df.with_columns(
        vdec=_dec("vol20", None),
        mdec=_dec("past20", None),
        month=pl.col("date").dt.truncate("1mo"),
        regain=(pl.col("maxfwd") >= REGAIN).cast(pl.Float64),
        drop2=(pl.col("minfwd") <= -DROP2).cast(pl.Float64),
    )
    cells = df.group_by("month", "vdec", "mdec").agg(
        c_drop=pl.col("label").mean(),
        c_drop2=pl.col("drop2").mean(),
        c_fwd=pl.col("fwd20").mean(),
        c_regain=pl.col("regain").mean(),
    )
    df = df.join(cells, on=["month", "vdec", "mdec"])
    pct = pl.col("score").rank("average").over("date") / pl.col("score").count().over("date")
    top = df.filter(pct >= 0.9).with_columns(d_fwd=pl.col("fwd20") - pl.col("c_fwd"))
    print(
        f"\n══ {name}（{df['date'].min()} ~ {df['date'].max()}；已飆股 {df.height:,} 列，風險最高一成 {top.height:,} 列）"
    )
    print(
        f"  基礎率：20 日內再跌 15% {float(df['label'].mean()):.1%}、跌 20% {float(df['drop2'].mean()):.1%}"
    )
    for label, got, base in (
        ("跌 15%", "label", "c_drop"),
        ("跌 20%（副標籤）", "drop2", "c_drop2"),
    ):
        g, b = float(top[got].mean()), float(top[base].mean())
        print(f"  最高風險一成 {label}：{g:.1%} vs 配對 {b:.1%} → 倍率 {g / b:.2f}×")
    mean_d, t, nm = _block_t(top, "d_fwd")
    print(
        f"  決策價值：最高風險一成的 20 日報酬 平均 {float(top['fwd20'].mean()):+.2%}／中位 {float(top['fwd20'].median()):+.2%}，"
        f"配對組 {float(top['c_fwd'].mean()):+.2%} → 差 {mean_d:+.2%}（月區塊 t {t:+.2f}，{nm} 個月；負＝賣掉少賠）"
    )
    print(
        f"  賣掉錯過的：其中之後又漲 ≥ {REGAIN:.0%} 的 {float(top['regain'].mean()):.1%}（配對組 {float(top['c_regain'].mean()):.1%}）"
    )


def vault_scores(
    conn: psycopg.Connection, cfg: MlConfig, model_dir: pathlib.Path, mk: pl.DataFrame
) -> pl.DataFrame:
    """最終模型對保留段打分：特徵、標籤、大盤狀態、樣本篩選全用訓練時的同一組函數。"""
    meta = json.loads((model_dir / "meta.json").read_text("utf-8"))
    features = [str(f) for f in meta["features"]]
    booster = lgb.Booster(model_file=str(model_dir / "model.txt"))
    factor_feats = [f for f in features if not f.startswith("ctx.")]
    feats = read_factor_frames(conn, SCHEMA, factor_feats, VAULT[0], VAULT[1])
    px = mk.select("date", _S, "open", "close", "amount", "adj")
    if cfg.label.transform == "tail_up":
        labels = tail_up_labels(
            px, horizon=H, up=UP, adjusted=True, max_span_sessions=cfg.label.max_span_sessions
        )
    else:
        labels = down_probability_labels(
            px, horizon=H, drop=DROP, adjusted=True, max_span_sessions=cfg.label.max_span_sessions
        )
    labels = labels.filter(pl.col("date").is_between(*VAULT))
    frame = assemble(feats, labels)
    ctx_cols = [f for f in features if f.startswith("ctx.")]
    if ctx_cols:
        idx = read_index_close(conn, SCHEMA, "TAIEX", DATA_START, DATA_END)
        ctx = market_state_features(idx, mk.select("date", _S, "close"), window=cfg.context_window)
        frame = frame.join(ctx.select("date", *ctx_cols), on="date", how="left")
    assert cfg.sample_state is not None
    frame = frame.join(
        state_pool(px, cfg.sample_state, adjusted=True), on=["date", _S], how="inner"
    )
    frame = frame.filter(pl.col("date").is_between(*VAULT))
    for f in features:  # 登記後物化的因子若保留段整欄空，就是資料沒到齊——不得當 NaN 吞掉
        if f in frame.columns and frame[f].null_count() == frame.height:
            raise SystemExit(f"{model_dir.name}：保留段的特徵 {f} 整欄空，先補物化")
    scores = mlt.predict_scores(booster, frame, features)
    return scores.join(frame.select("date", _S, "label"), on=["date", _S])


def main() -> None:
    ap = argparse.ArgumentParser(description=str(__doc__).splitlines()[0])
    ap.add_argument(
        "--arm", nargs=3, action="append", metavar=("NAME", "CONFIG", "MODEL_DIR"), required=True
    )
    ap.add_argument("--vault", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    print(
        "**D-151：不設門檻、只報數字。**"
        + ("　保留段 2026-05-01 ~ 08-03（開一次）" if args.vault else "　walk-forward 樣本外")
    )
    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn:
        mk = load_market(conn)
        for name, cfg_path, model_path in args.arm:
            cfg = load_config(ROOT / cfg_path, MlConfig)
            model_dir = pathlib.Path(model_path)
            meta = json.loads((model_dir / "meta.json").read_text("utf-8"))
            features = [str(f) for f in meta["features"]]
            if args.vault:
                oos = vault_scores(conn, cfg, model_dir, mk)
            else:
                oos = pl.read_parquet(model_dir / "oos_scores.parquet")
            if oos.is_empty():
                raise SystemExit(f"{name}：沒有分數")
            if pl.Series(oos["date"]).max() > DATA_END:  # type: ignore[operator]
                raise SystemExit(f"{name}：分數越過 {DATA_END}")
            if cfg.label.transform == "tail_up":
                report_up(f"{name}（{model_dir.name}）", oos, mk, conn)
            else:
                report_down(f"{name}（{model_dir.name}）", oos, mk)
            broker = tuple(f for f in features if f.startswith("chip.broker"))
            _print_importance(model_dir, features, broker or PATTERNS)


if __name__ == "__main__":
    main()
