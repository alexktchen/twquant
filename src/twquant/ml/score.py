"""推論（SPEC-010 §4）：模型載入（反前視斷言）與最新模型選擇。邊界層（允許 IO）。

本模組不 import dataset 的標籤函數——推論路徑禁觸標籤（SPEC-010 §2 邊界）。
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import lightgbm as lgb


def _train_end(meta_path: Path) -> dt.date:
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    raw = meta.get("train_end")
    if not isinstance(raw, str):
        raise ValueError(f"{meta_path} 缺 train_end：模型不可驗證，拒用（SPEC-010 §4）")
    return dt.date.fromisoformat(raw)


def load_model(model_dir: Path, as_of: dt.date) -> tuple[lgb.Booster, dict[str, object]]:
    """載入 model_dir 的模型；斷言 train_end < as_of，違反即 raise（AC-5）。"""
    meta_path = model_dir / "meta.json"
    if not meta_path.exists():
        raise ValueError(f"{model_dir} 缺 meta.json")
    train_end = _train_end(meta_path)
    if train_end >= as_of:
        raise ValueError(
            f"模型 {model_dir.name} train_end {train_end} ≥ as_of {as_of}："
            "以未來資料訓練的模型不得用於此日（前視違規，SPEC-010 AC-5）"
        )
    meta: dict[str, object] = json.loads(meta_path.read_text(encoding="utf-8"))
    return lgb.Booster(model_file=str(model_dir / "model.txt")), meta


def latest_model_dir(models_root: Path, as_of: dt.date) -> Path:
    """取 models_root 下 train_end < as_of 的最新模型目錄；無合格者 → raise。"""
    best: tuple[dt.date, Path] | None = None
    for meta_path in sorted(models_root.glob("*/meta.json")):
        te = _train_end(meta_path)
        if te < as_of and (best is None or te > best[0]):
            best = (te, meta_path.parent)
    if best is None:
        raise ValueError(f"{models_root} 無可用模型（需 train_end < {as_of}）")
    return best[1]
