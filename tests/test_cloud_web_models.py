"""T-345：雲端網站顯示的三個模型要跟每日 pipeline 打分的模型同一組。

網站的 Pages Functions（TypeScript）把模型代號寫死在 frontend/functions/_lib/scores.ts。
換模型只改 configs/ml.yaml 的話，網站會靜默地讀不到分數（每張卡片都變「—」）；這條測試讓它當場紅。
"""

from __future__ import annotations

import re
from pathlib import Path

from twquant.config import MlConfig, load_config

ROOT = Path(__file__).resolve().parents[1]
SCORES_TS = ROOT / "frontend" / "functions" / "_lib" / "scores.ts"


def _web_models() -> dict[str, str]:
    text = SCORES_TS.read_text(encoding="utf-8")
    return dict(re.findall(r"\{ code: '(\w+)', model_id: '([^']+)' \}", text))


def test_ac8_web_models_match_ml_config() -> None:
    cfg = load_config(ROOT / "configs" / "ml.yaml", MlConfig)
    assert _web_models() == {
        "rank": cfg.production_ranking_model,
        "quiet": cfg.quiet_live_model,
        "risk": cfg.recommendation_buckets.risk_gate_model,
    }, f"{SCORES_TS} 的 MODELS 要跟 configs/ml.yaml 同步"
