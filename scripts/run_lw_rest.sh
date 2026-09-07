#!/usr/bin/env bash
# P6（F1 × 兩條擇時）與 P7（六規則 × 七基座）——執行中的主佇列是 21:04 的版本，
# 不含這兩批（bash 啟動時就把整份腳本讀進記憶體了）。等主佇列結束後接著跑。
set -u
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
export TWQUANT_DATA_DIR="D:/project/twquant/data"
LOG=logs/lw_rest.log
say() { echo "=== $(date +%F' '%T) $*" | tee -a "$LOG"; }

say "等主佇列印出「全部結束」"
until grep -q "全部結束" logs/lw_queue_runner.log 2>/dev/null; do sleep 60; done
say "主佇列已結束，接著跑 P6／P7"

for s in f1_timing matrix; do
  say "開始 $s"
  uv run --all-groups python scripts/run_long_window.py --set "$s" --scales 1x --no-dividends >>"$LOG" 2>&1
  say "$s 結束 rc=$?"
  bash scripts/refresh_report.sh >>"$LOG" 2>&1
done
say "全部結束"
