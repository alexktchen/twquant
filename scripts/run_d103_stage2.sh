#!/usr/bin/env bash
# D-103 第二段：LW-F1c（籌碼修正版模型當池子）對照 LW-F1。
# 第一段已通過（F IC +0.0660 → +0.0673、R +0.4235 → +0.4242，正窗均不變）。
set -u
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
export TWQUANT_DATA_DIR="D:/project/twquant/data"
LOG=logs/d103_stage2.log
say() { echo "=== $(date +%F' '%T) $*" | tee -a "$LOG"; }

say "等 lwc rev 模型的分數回填完成"
until uv run --all-groups python -c "
import os, sys, psycopg
with psycopg.connect(os.environ['TWQUANT_PG_DSN']) as c, c.cursor() as cur:
    cur.execute(\"select count(*) from serving.ml_scores where model_id like 'lgbm_lwc_rev_h15%'\")
    n = cur.fetchone()[0]
print('lwc rev 分數列數：', n)
sys.exit(0 if n > 100000 else 1)
" >>"$LOG" 2>&1; do sleep 30; done
say "回填完成"

# 與 LW-F1 同視窗（D-102：ML 格一律 2019-05-02 起）
uv run --all-groups python scripts/run_long_window.py --set chip_feat --scales 1x \
  --no-dividends --start 2019-05-02 --out-suffix _w2019 >>"$LOG" 2>&1
say "LW-F1c 結束 rc=$?"

say "判定：LW-F1c − LW-F1 的月度區塊 t（甲 ≥ +2.0／乙 ≥ 0／丙 < 0）"
D=D:/project/twquant/data
uv run --all-groups python scripts/edge_tstat.py \
  "$D/tracks_lw_chipfeat_w2019_nodiv/LW-F1c_lw_f1c_revpool_1x.json" \
  "$D/tracks_lw_revenue_w2019_nodiv/LW-F1_lw_f1_revpool_1x.json" 2>&1 | tee -a "$LOG"
bash scripts/refresh_report.sh >>"$LOG" 2>&1
say "全部完成"
