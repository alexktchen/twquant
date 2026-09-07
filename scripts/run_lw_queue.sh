#!/usr/bin/env bash
# D-092 的排隊：D-091（跑中）→ P1 降周轉 → P4 擇時 → P5a 公開全部 →（等回填）→ P2 ML → P5b ML 全部。
# 序列跑：同一台機器同一個 PG，平行只會讓每格都慢。每一集寫不同目錄與 trial_scope。
set -u
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
export TWQUANT_DATA_DIR="D:/project/twquant/data"
DATA=D:/project/twquant/data
LOG=logs/lw_queue.log
say() { echo "=== $(date +%F' '%T) $*" | tee -a "$LOG"; }
run_set() { uv run --all-groups python scripts/run_long_window.py --set "$1" --scales 1x --no-dividends >>"$LOG" 2>&1; say "$1 結束 rc=$?"; }

say "等 D-091 的最後一格（LW-B1）"
until [ -f "$DATA/tracks_lw_risk_nodiv/LW-B1_lw_b1_large_enhanced_1x.json" ]; do sleep 60; done
say "D-091 完成"

say "P1 降周轉";      run_set turnover
say "P4 擇時";        run_set timing
say "P5a 公開全部";   run_set public_all

say "等三個長窗口模型的分數回填"
until uv run --all-groups python scripts/check_lw_scores.py >>"$LOG" 2>&1; do sleep 120; done
say "回填齊了"

say "P2 ML 四格";     run_set ml
say "P5b ML 全部";    run_set ml_all
say "P6 F1 擇時對照"; run_set f1_timing
say "P7 六規則矩陣";   run_set matrix
say "全部結束"
