#!/usr/bin/env bash
# 營收線優先批：使用者指出報告裡沒有營收相關結果，而 F 系列排在主佇列很後面。
# 分數已回填（T-178），故可獨立跑。與主佇列並行——兩者都是讀多寫少，PG 撐得住。
set -u
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
export TWQUANT_DATA_DIR="D:/project/twquant/data"
LOG=logs/lw_revenue.log
echo "=== $(date +%F' '%T) 營收批開始" | tee -a "$LOG"
uv run --all-groups python scripts/run_long_window.py --set revenue --scales 1x --no-dividends >>"$LOG" 2>&1
echo "=== $(date +%F' '%T) 營收批結束 rc=$?" | tee -a "$LOG"
echo "=== $(date +%F' '%T) 接著跑營收×動能三格（P8）" | tee -a "$LOG"
uv run --all-groups python scripts/run_long_window.py --set revmom --scales 1x --no-dividends >>"$LOG" 2>&1
echo "=== $(date +%F' '%T) revmom 結束 rc=$?" | tee -a "$LOG"
bash scripts/refresh_report.sh >>"$LOG" 2>&1
echo "=== $(date +%F' '%T) 報告已刷新" | tee -a "$LOG"
