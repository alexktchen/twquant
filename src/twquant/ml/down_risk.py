"""易跌標記（T-391／D-193）：D-192 的最終模型對當天大盤精選名單打「20 日內碰 −15%」的機率，機率最高的幾檔標起來。

D-192 §七已判定這個模型**不能當濾網**（剔掉它挑的 5 檔，平均報酬反而降）；D-193 讓它只當資訊：
只標大盤精選（唯一 AUC 贏過波動度的線）、不改名單、不進 ml_scores。純函數在這裡，PG 讀寫由 CLI 負責。
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

import lightgbm as lgb
import numpy as np
import polars as pl

from twquant.conventions import TZ
from twquant.errors import DataError

LINE = "CORE"
SCHEMA: dict[str, pl.DataType] = {
    "date": pl.Date(),
    "line": pl.Utf8(),
    "stock_id": pl.Utf8(),
    "model_id": pl.Utf8(),
    "p": pl.Float64(),
    "flagged": pl.Boolean(),
    "created_at": pl.Datetime("us", "Asia/Taipei"),
}


@dataclass(frozen=True)
class DownRiskModel:
    """最終模型＋meta.json 裡凍結的特徵順序與要標幾檔。"""

    model_id: str
    features: list[str]
    flag_top_k: int
    booster: lgb.Booster


def load_model(model_dir: Path) -> DownRiskModel:
    """讀 <dir>/meta.json 與 model.txt；缺檔 → FileNotFoundError（呼叫端決定要不要 raise）。"""
    meta = json.loads((model_dir / "meta.json").read_text(encoding="utf-8"))
    booster = lgb.Booster(model_file=str(model_dir / "model.txt"))
    return DownRiskModel(
        model_id=str(meta["model_id"]),
        features=[str(f) for f in meta["features"]],
        flag_top_k=int(meta["flag_top_k"]),
        booster=booster,
    )


def feature_matrix(
    picks: list[tuple[str, int, float | None]],
    raw: dict[tuple[str, str], tuple[float | None, float | None]],
    features: list[str],
    structural_missing: set[str] | None = None,
) -> tuple[np.ndarray, list[str]]:
    """名單列 (代號, 名次, 分數) ＋ (代號, 因子) → 原值 → 依 meta 特徵順序排成矩陣；缺值 NaN（LightGBM 自己處理）。

    `score`／`rank` 兩個特徵來自名單本身；其餘是因子名。整欄缺值的因子分兩種：`structural_missing`（當天整個市場都沒算，
    例：分點類三支本機與雲端都不算，訓練期曾經有）與型態類稀疏因子（箱型突破、旗形、頭肩頂……沒出現型態就是空，15 檔全空很正常）。
    兩種都交給模型當缺值。**超過一半**的因子整欄缺 → raise：那是物化沒到齊，不是「沒有訊號」。
    回 (矩陣, 整欄缺值的因子清單)，呼叫端寫進步驟摘要，不靜默。
    """
    skip = structural_missing or set()
    rows = []
    for sid, rank, score in picks:
        row: list[float] = []
        for f in features:
            if f == "score":
                row.append(float("nan") if score is None else float(score))
            elif f == "rank":
                row.append(float(rank))
            else:
                v = raw.get((sid, f), (None, None))[0]
                row.append(float("nan") if v is None else float(v))
        rows.append(row)
    x = np.asarray(rows, dtype=float).reshape(len(picks), len(features))
    all_missing: list[str] = []
    if len(picks):
        all_missing = [
            f
            for i, f in enumerate(features)
            if f not in ("score", "rank") and np.isnan(x[:, i]).all()
        ]
        unexpected = [f for f in all_missing if f not in skip]
        n_factors = sum(1 for f in features if f not in ("score", "rank"))
        if n_factors and len(unexpected) * 2 > n_factors:
            raise DataError(
                "pick_down_risk",
                None,
                f"當天名單股有 {len(unexpected)}／{n_factors} 個因子整欄缺值（{unexpected[:6]}…）——物化沒到齊，先補再標",
            )
    return x, all_missing


def score_picks(
    day: dt.date,
    picks: list[tuple[str, int, float | None]],
    raw: dict[tuple[str, str], tuple[float | None, float | None]],
    model: DownRiskModel,
    now: dt.datetime,
    structural_missing: set[str] | None = None,
) -> tuple[pl.DataFrame, list[str]]:
    """當天大盤精選每檔的機率；機率最高的 flag_top_k 檔 flagged。回 (表, 整欄缺值的因子)。名單空 → 空表。"""
    if now.tzinfo is None:
        raise ValueError("now 必須帶時區")
    at = now.astimezone(TZ)
    if not picks:
        return pl.DataFrame(schema=SCHEMA), []
    x, missing = feature_matrix(picks, raw, model.features, structural_missing)
    p = np.asarray(model.booster.predict(x), dtype=float)
    order = np.argsort(-p, kind="mergesort")
    flagged = np.zeros(len(p), dtype=bool)
    flagged[order[: model.flag_top_k]] = True
    return pl.DataFrame(
        {
            "date": [day] * len(picks),
            "line": [LINE] * len(picks),
            "stock_id": [s for s, _, _ in picks],
            "model_id": [model.model_id] * len(picks),
            "p": p.tolist(),
            "flagged": flagged.tolist(),
            "created_at": [at] * len(picks),
        },
        schema=SCHEMA,
    ), missing
