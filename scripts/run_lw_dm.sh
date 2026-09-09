#!/usr/bin/env bash
# 補跑 P7 矩陣的 Daniel-Moskowitz 五格：先前因 index_daily 只到 2015-06
# （2017-01-03 之前 393 天 < 488）而全部 raise；指數已回補到 2014-06（638 天）。
set -u
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
export TWQUANT_DATA_DIR="D:/project/twquant/data"
LOG=logs/lw_dm.log
echo "=== $(date +%F' '%T) 補跑 DM 五格" | tee -a "$LOG"
uv run --all-groups python scripts/run_long_window.py --set matrix_pub --scales 1x --no-dividends >>"$LOG" 2>&1
echo "=== $(date +%F' '%T) 結束 rc=$?" | tee -a "$LOG"
bash scripts/refresh_report.sh >>"$LOG" 2>&1
echo "=== $(date +%F' '%T) 報告已刷新" | tee -a "$LOG"
