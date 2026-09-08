#!/usr/bin/env bash
# D-092 的排隊：D-091（跑中）→ P1 降周轉 → P4 擇時 → P5a 公開全部 →（等回填）→ P2 ML → P5b ML 全部。
# 序列跑：同一台機器同一個 PG，平行只會讓每格都慢。每一集寫不同目錄與 trial_scope。
set -u
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
export TWQUANT_DATA_DIR="D:/project/twquant/data"
DATA=D:/project/twquant/data
LOG=logs/lw_queue2.log
say() { echo "=== $(date +%F' '%T) $*" | tee -a "$LOG"; }
# 每跑完一組就重建報告資料與 HTML（使用者要求）。發布 artifact 需要 Claude，
# 故這裡只把檔案更新到最新，並在 log 印一行 REPORT-READY 讓監看器抓到。
refresh_report() {
  uv run --all-groups python scripts/build_lw_section.py >>"$LOG" 2>&1
  uv run --all-groups python scripts/timing_all.py >>"$LOG" 2>&1
  uv run --all-groups python scripts/build_two_window_report.py >>"$LOG" 2>&1
  uv run --all-groups python scripts/render_two_window_report.py >>"$LOG" 2>&1
  local n
  n=$(uv run --all-groups python -c "import json;d=json.load(open(r'$DATA/reports/lw_series.json',encoding='utf-8'));print(sum(1 for c in d['cells'] if c['metrics']))" 2>/dev/null)
  echo "REPORT-READY $1 完成，LW 已有 $n 格"
}
run_set() {
  uv run --all-groups python scripts/run_long_window.py --set "$1" --scales 1x --no-dividends >>"$LOG" 2>&1
  say "$1 結束 rc=$?"
  refresh_report "$1"
}

say "重啟（前一次因腳本被就地編輯而中止於 timing 之後）；已完成的格會跳過"

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
