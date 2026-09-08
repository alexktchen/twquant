#!/usr/bin/env bash
# 2017 視窗線：只跑**不需要 ML 分數**的格子（D-102）。
#
# 兩條線共用 run_long_window.py 的原子認領，可同時跑同一個 set 而不重複。
#
# **這個檔案在跑的時候絕對不要編輯**——本輪已經因此壞掉兩次：bash 會重讀腳本，
# 讀到位移錯開的位置就會 unexpected EOF 或把函數本體當成頂層命令。要改就寫新檔。
set -u
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
export TWQUANT_DATA_DIR="D:/project/twquant/data"
LOG=logs/lw_2017.log
say() { echo "=== $(date +%F' '%T) $*" | tee -a "$LOG"; }
run() {
  say "開始 $1"
  uv run --all-groups python scripts/run_long_window.py \
    --set "$1" --scales 1x --no-dividends >>"$LOG" 2>&1
  say "$1 結束 rc=$?"
  bash scripts/refresh_report.sh >>"$LOG" 2>&1
  say "報告已刷新（$1）"
}

run public_all   # 補完 LW-MGALLm（其餘 10 格已完成）
run matrix_pub   # 30 格：五個公開因子基座 × 六條規則
say "2017 線全部結束"
