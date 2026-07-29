"""Vercel Python serverless function 進入點（T-059，D-030）。

把既有 FastAPI app（monitoring/api.py，唯讀 GET）原封不動掛上——Vercel 的 Python
runtime 直接吃模組層的 ASGI `app` 變數。路由不變（/api/...），故前端零改動。

環境變數（Vercel 專案設定）：TWQUANT_PG_DSN＝Supabase transaction pooler（:6543）。
資料湖降級路徑在雲端不可用（無 data/），故 configs/serving.yaml 的
`fallback_duckdb` 在雲端應為 false——PG 不可達時回 503 而非假裝有資料。
"""

from __future__ import annotations

import sys
from pathlib import Path

# repo 根的 src/ 佈局：Vercel 不跑 `uv sync`（只裝 requirements.txt），故手動掛路徑
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from twquant.monitoring.api import app  # noqa: E402

__all__ = ["app"]
