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


# 模型用途由「它在預測什麼」決定，也就是標籤的 transform。這不是可調參數，
# 而是分類：選股訊號與風控閘門是不同用途，混用等於拿回撤模型當選股器。
_PURPOSE_BY_TRANSFORM = {
    "rank_pct": "ranking",
    "cost_adjusted_return": "ranking",
    "forward_drawdown": "risk_gate",  # D-075 的 R 系列：風控閘門，明文不得當選股訊號
    "revenue_surprise": "revenue_surprise",  # D-074 的 F 系列
}


def model_purpose(meta: dict[str, object]) -> str:
    """這個模型在預測什麼；未記錄標籤定義的舊 artifact 一律為 unknown。

    優先取 meta 明寫的 `purpose`（新模型應直接記錄），否則由標籤 transform 推斷。
    """
    explicit = meta.get("purpose")
    if isinstance(explicit, str) and explicit:
        return explicit
    label = meta.get("label")
    transform = label.get("transform") if isinstance(label, dict) else None
    if isinstance(transform, str):
        return _PURPOSE_BY_TRANSFORM.get(transform, "unknown")
    return "unknown"


def latest_model_dir(models_root: Path, as_of: dt.date, *, purpose: str | None = None) -> Path:
    """取 train_end < as_of 的最新模型目錄；無合格者或無法唯一決定 → raise。

    `purpose` 指定時只考慮該用途的模型（見 `model_purpose`）。

    **同一 train_end 有多個合格模型時一律 raise。** 舊版在此依目錄名排序靜默挑第一個；
    實務上八個模型的 train_end 全是 2026-02-25，於是每日推論挑到了 `lgbm_dd_h60`——
    D-075 的回撤風控模型——當成選股器（2026-09-06 實跑抓到）。要用哪一個必須由呼叫端
    明確指定，不能由排序決定。
    """
    eligible: list[tuple[dt.date, Path]] = []
    for meta_path in sorted(models_root.glob("*/meta.json")):
        meta: dict[str, object] = json.loads(meta_path.read_text(encoding="utf-8"))
        te = _train_end(meta_path)
        if te >= as_of:
            continue
        if purpose is not None and model_purpose(meta) != purpose:
            continue
        eligible.append((te, meta_path.parent))
    if not eligible:
        scope = f"、用途 {purpose}" if purpose else ""
        raise ValueError(f"{models_root} 無可用模型（需 train_end < {as_of}{scope}）")
    newest = max(te for te, _ in eligible)
    tied = [path for te, path in eligible if te == newest]
    if len(tied) > 1:
        names = "、".join(sorted(path.name for path in tied))
        raise ValueError(
            f"同一 train_end {newest} 有 {len(tied)} 個合格模型，無法自動選擇："
            f"{names}。請以 --model 指定目錄，或用 --purpose 縮小範圍（T-224）"
        )
    return tied[0]
