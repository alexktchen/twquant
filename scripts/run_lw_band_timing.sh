#!/usr/bin/env bash
# D-095：降周轉基座 × 三條擇時規則。預測寫在 DECISIONS.md，跑之前就定案。
set -u
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
export TWQUANT_DATA_DIR="D:/project/twquant/data"
LOG=logs/lw_band_timing.log
echo "=== $(date +%F' '%T) D-095 開始" | tee -a "$LOG"
uv run --all-groups python scripts/run_long_window.py --set band_timing --scales 1x --no-dividends >>"$LOG" 2>&1
echo "=== $(date +%F' '%T) band_timing 結束 rc=$?" | tee -a "$LOG"
bash scripts/refresh_report.sh >>"$LOG" 2>&1
echo "=== $(date +%F' '%T) 報告已刷新" | tee -a "$LOG"
