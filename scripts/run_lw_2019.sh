#!/usr/bin/env bash
# 2019 視窗線（D-102）：所有需要 ML 分數的格子，**外加公開因子的配對對照**。
#
# 起日 2019-05-02＝最晚的 dd 模型第一個分數日之後。三個模型共用一個視窗，
# 否則 ML 格彼此就不可比，而這批的重點正是互相比。
#
# **配對對照不是可選的**：ML 格（2019 起）與既有的公開因子格（2017 起）差的
# 不只是訊號，還少了 2018 Q4 那個壓力體制。沒有同視窗的 C1／B2／X1／B1／R2／F2／F3，
# 「ML 贏過 C1」就分不出是訊號還是年份。
#
# **這個檔案在跑的時候絕對不要編輯**——本輪已經因此壞掉兩次。要改就寫新檔。
set -u
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
export TWQUANT_DATA_DIR="D:/project/twquant/data"
LOG=logs/lw_2019.log
W=2019-05-02
say() { echo "=== $(date +%F' '%T) $*" | tee -a "$LOG"; }
run() {
  say "開始 $1（視窗 $W 起）"
  uv run --all-groups python scripts/run_long_window.py \
    --set "$1" --scales 1x --no-dividends --start "$W" --out-suffix _w2019 >>"$LOG" 2>&1
  say "$1 結束 rc=$?"
  bash scripts/refresh_report.sh >>"$LOG" 2>&1
  say "報告已刷新（$1）"
}

# 先跑配對對照——先有它，ML 的數字一出來就能立刻判讀，不必等
run public       # C1／B2／X1／C1abs
run risk         # R2／C4／B1
run ml           # M1／R1／B1ML／MG3
run revenue      # F 系列與營收濾網（含上一輪失敗的四格）
run revmom       # 營收 × 動能三格
run ml_all       # 其餘 ML 策略
run f1_timing    # F1 × Antonacci／Faber
run matrix_ml    # M1／F1 兩個 ML 基座 × 六條規則
say "2019 線全部結束"
