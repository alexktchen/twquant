"""訓練（SPEC-010 §4）：walk-forward 訓練、模型落地、實驗登記。

邊界層——允許檔案 IO 與 lightgbm；純運算的資料集/切分在 dataset.py / splits.py
（受 IO 靜態閘門掃描，本檔不在其列）。
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import lightgbm as lgb
import polars as pl

from twquant.config import MlConfig, MlLgbmConfig, config_hash
from twquant.conventions import now_tw
from twquant.ml.splits import WalkSplit
from twquant.reporting.research_log import append_research_log

# 決定性旗標（SPEC-010 §0：同 config 同輸入 bit-identical）——非調參對象，故為常數非配置。
# num_threads 固定 4：LightGBM 在 deterministic+force_row_wise 下同執行緒數即可重現；
# 單執行緒對全史 walk-forward（百萬列 × 15+ 窗）慢到不可用，4 為固定值非調參。
_DETERMINISM: dict[str, object] = {
    "deterministic": True,
    "force_row_wise": True,
    "num_threads": 4,
    "verbosity": -1,
}


def _params(lgbm: MlLgbmConfig) -> dict[str, object]:
    return {
        "objective": "regression",
        "num_leaves": lgbm.num_leaves,
        "learning_rate": lgbm.learning_rate,
        "min_data_in_leaf": lgbm.min_data_in_leaf,
        "feature_fraction": lgbm.feature_fraction,
        "seed": lgbm.seed,
        **_DETERMINISM,
    }


def fit_ranker(frame: pl.DataFrame, feature_cols: Sequence[str], lgbm: MlLgbmConfig) -> lgb.Booster:
    """對 label 非 null 列訓練 LightGBM 回歸（目標＝橫斷面百分位）。特徵缺值原生處理。"""
    data = frame.filter(pl.col("label").is_not_null())
    if data.is_empty():
        raise ValueError("無可訓練列：label 全為 null")
    ds = lgb.Dataset(
        data.select(feature_cols).to_numpy(),
        label=data["label"].to_numpy(),
        feature_name=list(feature_cols),
    )
    return lgb.train(_params(lgbm), ds, num_boost_round=lgbm.n_estimators)


def predict_scores(
    booster: lgb.Booster, frame: pl.DataFrame, feature_cols: Sequence[str]
) -> pl.DataFrame:
    """對 frame 逐列推論。回傳 (date, stock_id, score)，列序同輸入。"""
    scores = booster.predict(frame.select(feature_cols).to_numpy())
    return frame.select("date", "stock_id").with_columns(
        pl.Series("score", scores, dtype=pl.Float64)
    )


def oos_rank_ic(scored: pl.DataFrame) -> float:
    """逐日 spearman(score, label) 的均值。輸入需含 (date, score, label)。"""
    per_day = (
        scored.filter(pl.col("label").is_not_null())
        .group_by("date")
        .agg(pl.corr(pl.col("score").rank("average"), pl.col("label").rank("average")).alias("ic"))
    )
    mean = per_day["ic"].drop_nulls().mean()
    if not isinstance(mean, int | float):
        raise ValueError("無可計算 RankIC 的日期（label 全 null 或單一樣本日）")
    return float(mean)


@dataclass(frozen=True)
class MlTrainResult:
    """walk-forward 產出：OOS 分數表（date, stock_id, score, label）與各窗 RankIC。"""

    oos_scores: pl.DataFrame
    window_ics: list[float]


def walk_forward_train(
    frame: pl.DataFrame,
    splits: Sequence[WalkSplit],
    feature_cols: Sequence[str],
    lgbm: MlLgbmConfig,
) -> MlTrainResult:
    """逐窗：以 ≤ train_end 訓練 → 對 [test_start, test_end] 推論（樣本外）。"""
    outs: list[pl.DataFrame] = []
    ics: list[float] = []
    for s in splits:
        train = frame.filter(pl.col("date").is_between(s.train_start, s.train_end))
        test = frame.filter(pl.col("date").is_between(s.test_start, s.test_end))
        booster = fit_ranker(train, feature_cols, lgbm)
        scored = predict_scores(booster, test, feature_cols).join(
            test.select("date", "stock_id", "label"), on=["date", "stock_id"], how="left"
        )
        outs.append(scored)
        ics.append(oos_rank_ic(scored))
    return MlTrainResult(oos_scores=pl.concat(outs).sort("date", "stock_id"), window_ics=ics)


def make_model_id(train_end: dt.date, cfg: MlConfig) -> str:
    """模型 id：演算法_h視窗_訓練截止_配置hash8——可回溯到 configs/ml.yaml（SPEC-010 §4）。"""
    return f"lgbm_h{cfg.horizon_days}_{train_end:%Y%m%d}_{config_hash(cfg)[:8]}"


def save_model(out_dir: Path, booster: lgb.Booster, meta: Mapping[str, object]) -> None:
    """model.txt + meta.json 落地（冪等覆蓋）。meta 必含 train_end（load_model 斷言用）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(out_dir / "model.txt"))
    (out_dir / "meta.json").write_text(
        json.dumps(dict(meta), ensure_ascii=False, sort_keys=True, indent=1), encoding="utf-8"
    )


def log_training_run(data_dir: str | Path, cfg: MlConfig, model_id: str, oos_ic: float) -> None:
    """research_log 登記一列（AC-8）；trial_scope 綁 horizon，trials 計數供 DSR。"""
    append_research_log(
        data_dir,
        [
            {
                "entry_type": "ml_train",
                "strategy": model_id,
                "factor": "ml",
                "params_hash": config_hash(cfg),
                "date": now_tw().date().isoformat(),
                "metric_name": "oos_rank_ic",
                "metric_value": oos_ic,
                "trial_scope": f"ml:h{cfg.horizon_days}",
            }
        ],
    )
