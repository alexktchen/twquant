"""匯出儀表板資料快照 → frontend/src/data.json（自包含單檔的內嵌 fallback）。

即時資料改由 `twquant serve` 的 API 提供（React 前端逐次 fetch）；此腳本只在打包
離線單檔（docker build --target artifact）時，重建一份內嵌快照當作 API 不可達時的
fallback。與 API 共用同一組建構函數（twquant.monitoring.snapshot），保證兩者一致。

從 twquant repo 根執行（需 uv 環境與資料湖）：
    uv run python frontend/scripts/export_snapshot.py
"""

from __future__ import annotations

import json
from pathlib import Path

from twquant.config import SystemConfig, load_config
from twquant.data.warehouse import Warehouse
from twquant.monitoring.snapshot import CONFIGS_DIR, build_snapshot

cfg = load_config(CONFIGS_DIR / "system.yaml", SystemConfig)
data_path = Path(cfg.data_dir)
if not data_path.is_absolute():
    data_path = CONFIGS_DIR.parent / data_path  # data_dir 相對路徑以 repo 根為基準

out = build_snapshot(Warehouse(data_path))

dest = Path(__file__).resolve().parent.parent / "src" / "data.json"
dest.write_text(
    json.dumps(out, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8"
)
meta = out["meta"]
stocks = out["stocks"]
factors = out["factors"]
market = out["market"]
top = meta["top_stocks"] if isinstance(meta, dict) else None
print(f"寫出 {dest}  大小 {dest.stat().st_size / 1024:.0f} KB")
print(
    f"因子 {len(factors) if isinstance(factors, list) else 0}　"
    f"市場點 {len(market) if isinstance(market, list) else 0}　"
    f"個股 {list(stocks) if isinstance(stocks, dict) else top}"
)
