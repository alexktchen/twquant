#!/usr/bin/env bash
# T-153／T-156：F 與 R 兩條事前登記研究線的 walk-forward 訓練。
# 序列跑（非平行）——兩者各需把 15 個因子的全史載進記憶體，平行會讓峰值加倍。
set -u
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
# T-160：讓 research_log（＝DSR 的試驗計數）也寫進主 checkout，而不是 worktree
export TWQUANT_DATA_DIR="D:/project/twquant/data"
FROM=2020-01-02
# CLAUDE.md §0.2：data_dir 是相對路徑，在 worktree 裡跑會寫進 worktree 自己的 data/。
# 模型是跨 worktree 共用的產物，一律明確指到主 checkout 的正本。
OUT=D:/project/twquant/data/models
TO=2026-08-28   # 金庫期邊界：與乾淨基準模型 lgbm_net_h20_20260225_6a798962 逐字相同

for cfg in ml_f_revenue_surprise ml_r_drawdown60; do
  log="logs/train_${cfg}_v2.log"
  echo "=== $(date +%F' '%T) 開始 ${cfg} ===" | tee -a "$log"
  uv run --all-groups python -m twquant ml train \
      --config "configs/${cfg}.yaml" --from "$FROM" --to "$TO" --out "$OUT" >>"$log" 2>&1
  echo "=== $(date +%F' '%T) ${cfg} 結束 rc=$? ===" | tee -a "$log"
done
