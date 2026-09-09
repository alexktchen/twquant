#!/usr/bin/env bash
# D-104：分散度階梯三格。預測寫在 DECISIONS.md D-104，跑之前定案。
# 2017 視窗——與既有的公開因子格同視窗，可直接比（不需 ML 分數）。
set -u
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
export TWQUANT_DATA_DIR="D:/project/twquant/data"
LOG=logs/d104.log
say() { echo "=== $(date +%F' '%T) $*" | tee -a "$LOG"; }
say "D-104 開始"
uv run --all-groups python scripts/run_long_window.py --set diversify --scales 1x --no-dividends >>"$LOG" 2>&1
say "diversify 結束 rc=$?"

say "判定：各格相對 LW-C1 的 ΔMDD 與月度區塊 t"
D=D:/project/twquant/data
for f in "$D"/tracks_lw_diversify_nodiv/*_1x.json; do
  [ -f "$f" ] || continue
  uv run --all-groups python scripts/edge_tstat.py "$f" "$D/tracks_lw_nodiv/LW-C1_lw_c1_breakout_1x.json" 2>&1 | tee -a "$LOG"
done
bash scripts/refresh_report.sh >>"$LOG" 2>&1
say "全部完成"
