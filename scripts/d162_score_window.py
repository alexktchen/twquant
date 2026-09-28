"""D-162 事後檢驗：重訓到 2026-02-27 的 A20 最終模型對 2026-03-02 ~ 09-08 逐日打分。

    TWQUANT_PG_DSN=... uv run --all-groups python scripts/d162_score_window.py \\
        --model D:/project/twquant/data/models/<model_id> --out D:/project/twquant/data/tracks_d162/scores

特徵、大盤狀態、流動性與安靜池篩選逐字沿用 `scripts/a20_picks.py`；任一特徵某天整欄空 → 停下。
輸出 `<out>/meta.json`（複製自模型）與 `<out>/oos_scores.parquet`（date、stock_id、score），
再用 `twquant ml backfill-scores --model <out>` 寫進 ml_scores。另印選股力（D-162 §二）：
訊號日前 15 名「隔日開盤進場後 20 日內任一還原收盤 ≥ +20%」比例 vs 同日池內，只算 20 日窗 ≤ 09-08 的訊號日。
**價格一律只讀到 2026-09-08**（第二保留期 09-09 起，D-109）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib
import io
import json
import os
import pathlib
import shutil
import sys
from typing import Any

import lightgbm as lgb
import polars as pl
import psycopg

from twquant.config import MlConfig, load_config
from twquant.data.serving.ml_store import read_factor_frames, read_index_close
from twquant.ml import train as mlt
from twquant.ml.context import market_state_features
from twquant.ml.dataset import liquid_pool, state_pool

# 研究腳本不是套件；同 d159 的 `_sibling`，執行期才載入
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
_a20: Any = importlib.import_module("a20_picks")
_d151: Any = importlib.import_module("d151_report")
H: int = _a20.H
TOP_N: int = _a20.TOP_N
SCHEMA: str = _d151.SCHEMA
DATA_START: dt.date = _d151.DATA_START

ROOT = pathlib.Path(__file__).resolve().parents[1]
CFG = ROOT / "configs" / "ml_quiet_a20_2602.yaml"
TEST = (dt.date(2026, 3, 2), dt.date(2026, 9, 8))  # D-162 §一
HOLDOUT_START = dt.date(2026, 9, 9)
_S = "stock_id"


def hit_rates(px: pl.DataFrame, scores: pl.DataFrame, top_n: int, h: int) -> pl.DataFrame:
    """每個訊號日：前 top_n 名與全池「隔日開盤進場後 h 日內任一收盤 ≥ +20%」的比例；窗口越界的訊號日不算。"""
    cal = sorted(px["date"].unique().to_list())
    last_ok = cal[len(cal) - 1 - h]
    p = pl.col("close") / pl.col("adj")
    entry = (pl.col("open") / pl.col("adj")).shift(-1).over(_S)
    maxfwd = pl.max_horizontal([p.shift(-k).over(_S) / entry - 1.0 for k in range(1, h + 1)])
    fwd = px.sort(_S, "date").with_columns(hit=maxfwd >= 0.20).select("date", _S, "hit")
    ranked = scores.filter(pl.col("date") <= last_ok).with_columns(
        pl.col("score").rank("ordinal", descending=True).over("date").alias("rank")
    )
    j = ranked.join(fwd, on=["date", _S], how="left").drop_nulls("hit")
    return j.group_by("date").agg(
        pl.col("hit").filter(pl.col("rank") <= top_n).mean().alias("top"),
        pl.col("hit").mean().alias("pool"),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=str(__doc__).splitlines()[0])
    ap.add_argument("--model", required=True, help="重訓模型目錄（含 model.txt、meta.json）")
    ap.add_argument("--out", required=True, help="分數輸出目錄（絕對路徑）")
    args = ap.parse_args()
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    model = pathlib.Path(args.model)
    cfg = load_config(CFG, MlConfig)
    meta = json.loads((model / "meta.json").read_text("utf-8"))
    if meta["train_end"] >= TEST[0].isoformat():
        raise SystemExit(f"模型 train_end {meta['train_end']} 不早於測試起日 {TEST[0]}")
    features = [str(f) for f in meta["features"]]
    factor_feats = [f for f in features if not f.startswith("ctx.")]
    ctx_cols = [f for f in features if f.startswith("ctx.")]
    booster = lgb.Booster(model_file=str(model / "model.txt"))
    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn:
        mk = _a20._load_px(conn, TEST[1])
        feats = read_factor_frames(conn, SCHEMA, factor_feats, TEST[0], TEST[1])
        idx = read_index_close(conn, SCHEMA, "TAIEX", DATA_START, TEST[1])
    if mk["date"].max() >= HOLDOUT_START:
        raise SystemExit("讀到第二保留期的價格——停止")
    px = mk.select("date", _S, "open", "close", "amount", "adj")
    frame = px.filter(pl.col("date").is_between(*TEST)).select("date", _S, "close")
    for f in factor_feats:
        frame = frame.join(feats[f].rename({"value": f}), on=["date", _S], how="left")
    ctx = market_state_features(idx, mk.select("date", _S, "close"), window=cfg.context_window)
    frame = frame.join(ctx.select("date", *ctx_cols), on="date", how="left")
    pool = liquid_pool(
        px,
        adv_window=cfg.label.adv_window_sessions,
        min_adv_pctl=cfg.sample_min_adv_pctl,
        min_price=cfg.sample_min_price,
    )
    assert cfg.sample_state is not None
    frame = frame.join(pool, on=["date", _S]).join(
        state_pool(px, cfg.sample_state, adjusted=True), on=["date", _S]
    )
    for f in features:
        empty = frame.group_by("date").agg(pl.col(f).is_null().all().alias("e")).filter("e")
        if empty.height:
            raise SystemExit(
                f"特徵 {f} 在 {sorted(empty['date'].to_list())[:5]} 整欄空——先補物化再打分"
            )
    scores = mlt.predict_scores(booster, frame, features).select("date", _S, "score")
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy(model / "meta.json", out / "meta.json")
    scores.sort("date", _S).write_parquet(out / "oos_scores.parquet")
    n_days = scores["date"].n_unique()
    print(f"**D-162 打分**：模型 {meta['model_id']}（train_end {meta['train_end']}）")
    print(
        f"  {scores['date'].min()!s} ~ {scores['date'].max()!s}，{n_days} 個交易日、{scores.height:,} 列 → {out}"
    )
    hr = hit_rates(px, scores, TOP_N, H)
    top, base = hr.select(pl.col("top").mean(), pl.col("pool").mean()).row(0)
    print(
        f"  選股力（{hr.height} 個訊號日，20 日窗 ≤ {TEST[1]}）：前 {TOP_N} 名碰到 +20% 的 {top:.1%}　"
        f"同日池內 {base:.1%}　倍率 {top / base:.2f}×"
    )


if __name__ == "__main__":
    main()
