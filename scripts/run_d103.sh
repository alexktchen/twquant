#!/usr/bin/env bash
# D-103：F／R 加入 chip.foreign_streak 後重訓、回填、跑 LW-F1 對照。
# 判準寫在 DECISIONS.md D-103，跑之前定案。
set -u
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
export TWQUANT_DATA_DIR="D:/project/twquant/data"
LOG=logs/d103.log
MODELS=D:/project/twquant/data/models
say() { echo "=== $(date +%F' '%T) $*" | tee -a "$LOG"; }

say "第一段：重訓 F／R（加 chip.foreign_streak）"
uv run --all-groups python -m twquant ml train --config configs/ml_f_revenue_surprise_lwc.yaml \
  --from 2017-01-03 --to 2026-08-28 --out "$MODELS" >>"$LOG" 2>&1
say "F 重訓完 rc=$?"
uv run --all-groups python -m twquant ml train --config configs/ml_r_drawdown60_lwc.yaml \
  --from 2017-01-03 --to 2026-08-28 --out "$MODELS" >>"$LOG" 2>&1
say "R 重訓完 rc=$?"

say "回填分數（月頻模型一律帶 --carry-forward-to，D-102 的教訓）"
for d in "$MODELS"/lgbm_lwc_*; do
  [ -d "$d" ] || continue
  uv run --all-groups python -m twquant ml backfill-scores --model "$d" --carry-forward-to 2026-02-28 >>"$LOG" 2>&1
  say "回填 $(basename "$d") rc=$?"
done
say "第一段完成——判準見 D-103，第二段的配置待第一段通過後再建"
