"""潛伏飆股（QUIET，D-163）的每日推論：安靜∩流動池 × 大盤狀態，與訓練同一組函數（D-151）。

通用的 `ml score` 對全宇宙打分、也不算大盤狀態特徵——拿它打安靜股模型，分數會落在錯的池子上，
而策略把「當日有分數的股票」當池子（`ml.lgbm_tail_up_20d`），池子錯了名單就錯了。所以另走這裡。
特徵、流動性門檻、安靜狀態的算法全部沿用訓練（`cli ml train`）與 `scripts/a20_picks.py`。
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import lightgbm as lgb
import polars as pl

from twquant.config import MlConfig
from twquant.conventions import ml_score_available_at, now_tw
from twquant.errors import DataError
from twquant.ml import train as mlt
from twquant.ml.context import market_state_features
from twquant.ml.dataset import attach_adjustment, cross_rank_pct, liquid_pool, state_pool

_S = "stock_id"
_CONTEXT_INDEX_ID = "TAIEX"


def model_features(model_dir: Path) -> tuple[str, list[str]]:
    """模型目錄的 (model_id, 特徵清單)——以 meta.json 為準，不以 yaml 為準（凍結的是訓練當時的清單）。"""
    meta = json.loads((model_dir / "meta.json").read_text(encoding="utf-8"))
    return str(meta["model_id"]), [str(f) for f in meta["features"]]


def inference_frame(
    px: pl.DataFrame,
    feats: dict[str, pl.DataFrame],
    index_close: pl.DataFrame,
    cfg: MlConfig,
    features: list[str],
    start: dt.date,
    end: dt.date,
) -> pl.DataFrame:
    """[start, end] 每日的推論列：只留流動 ∩ 安靜池，掛上個股特徵與大盤狀態。

    `px` 須含 (date, stock_id, open, close, amount, adj)，且往前涵蓋足夠的回看（安靜狀態 60 日、
    流動性 20 日）。任一特徵在某天整欄空 → raise：那是物化沒到齊，不是「沒有訊號」。
    """
    if cfg.sample_state is None:
        raise ValueError("inference_frame 只用於有 sample_state 的模型（安靜股，D-151）")
    frame = px.filter(pl.col("date").is_between(start, end)).select("date", _S)
    for name in (f for f in features if not f.startswith("ctx.")):
        if name not in feats:
            raise DataError("quiet_live", end, f"特徵 {name} 沒有讀到任何列")
        frame = frame.join(
            feats[name].select("date", _S, pl.col("value").alias(name)),
            on=["date", _S],
            how="left",
        )
    ctx_cols = [f for f in features if f.startswith("ctx.")]
    if ctx_cols:
        ctx = market_state_features(
            index_close, px.select("date", _S, "close"), window=cfg.context_window
        )
        frame = frame.join(ctx.select("date", *ctx_cols), on="date", how="left")
    pool = liquid_pool(
        px,
        adv_window=cfg.label.adv_window_sessions,
        min_adv_pctl=cfg.sample_min_adv_pctl,
        min_price=cfg.sample_min_price,
    )
    frame = frame.join(pool, on=["date", _S]).join(
        state_pool(px, cfg.sample_state, adjusted=True), on=["date", _S]
    )
    if frame.is_empty():
        raise DataError("quiet_live", end, f"{start} ~ {end} 安靜∩流動池是空的")
    for name in features:
        empty = frame.group_by("date").agg(pl.col(name).is_null().all().alias("e")).filter("e")
        if empty.height:
            days = sorted(empty["date"].to_list())[:5]
            raise DataError("quiet_live", end, f"特徵 {name} 在 {days} 整欄空——先補物化再打分")
    return frame.sort("date", _S)


def score_rows(
    frame: pl.DataFrame, booster: lgb.Booster, features: list[str], model_id: str
) -> pl.DataFrame:
    """推論並轉成 `ml_scores` 的列：分數、同日池內百分位、資訊可得時刻（T-228）。"""
    now = now_tw()
    scored = cross_rank_pct(mlt.predict_scores(booster, frame, features), "score", "rank_pct")
    stamps = {d: ml_score_available_at(d, now) for d in scored["date"].unique().to_list()}
    return scored.with_columns(
        pl.lit(model_id).alias("model_id"),
        pl.col("date")
        .replace_strict(stamps, return_dtype=pl.Datetime("us", "Asia/Taipei"))
        .alias("available_at"),
    ).select("date", _S, "model_id", "score", "rank_pct", "available_at")


def quiet_scores(
    conn: Any,
    schema: str,
    *,
    cfg: MlConfig,
    model_dir: Path,
    stock_pattern: str,
    start: dt.date,
    end: dt.date,
    lookback_days: int,
) -> pl.DataFrame:
    """讀 PG、對 [start, end] 每個交易日打分，回傳 `ml_scores` 格式的列（不寫入）。"""
    from twquant.data.serving.ml_store import (
        read_corporate_events,
        read_factor_frames,
        read_index_close,
        read_prices_barrier,
    )

    model_id, features = model_features(model_dir)
    lo = start - dt.timedelta(days=lookback_days)
    px = read_prices_barrier(conn, schema, lo, end).filter(
        pl.col(_S).str.contains(stock_pattern) & (pl.col("close") > 0)
    )
    px = attach_adjustment(px, read_corporate_events(conn, schema, end)).sort(_S, "date")
    feats = read_factor_frames(
        conn, schema, [f for f in features if not f.startswith("ctx.")], start, end
    )
    index_close = read_index_close(conn, schema, _CONTEXT_INDEX_ID, lo, end)
    frame = inference_frame(px, feats, index_close, cfg, features, start, end)
    booster = lgb.Booster(model_file=str(model_dir / "model.txt"))
    return score_rows(frame, booster, features, model_id)
