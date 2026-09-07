#!/usr/bin/env bash
# 第二條線：營收批結束後接手 P7 矩陣（使用者 2026-09-07 核可）。
#
# 兩條線會同時跑 matrix。**不事先把 42 格分兩半**——run_long_window.py 用檔案系統的
# 原子建檔認領每一格（_claim），任一條線提早結束或某格失敗，另一條自然接手。
# 分半的話某條線先跑完就只能閒著，而且沒人重新分配。
set -u
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
export TWQUANT_DATA_DIR="D:/project/twquant/data"
LOG=logs/lw_line2.log
say() { echo "=== $(date +%F' '%T) $*" | tee -a "$LOG"; }

say "等營收批（含 P8 營收×動能）跑完"
until grep -q "報告已刷新" logs/lw_revenue_runner.log 2>/dev/null; do
  # 營收批死掉也要往下走，不然這條線會永遠空等
  if ! powershell -NoProfile -Command "(Get-CimInstance Win32_Process -Filter \"Name='bash.exe'\" | Where-Object { \$_.CommandLine -match 'run_revenue' }).Count" 2>/dev/null | grep -q '[1-9]'; then
    say "營收批程序已不在——不再等，直接開始矩陣"
    break
  fi
  sleep 60
done

say "第二條線接手 P7 矩陣（與主線共用認領機制）"
uv run --all-groups python scripts/run_long_window.py --set matrix --scales 1x --no-dividends >>"$LOG" 2>&1
say "matrix 結束 rc=$?"
bash scripts/refresh_report.sh >>"$LOG" 2>&1
say "全部結束"
