#!/usr/bin/env bash
# D-149 第十一輪：回測 F1／F2／F3（同窗口 2017-01-03 ~ 2026-02-28、資本 80M）→ 只報不判。
# 不訓練新模型：三臂都用 tail_b 的 lgbm_tailb_tu_h60_20260430_7ab9b0f9。一次只跑一支（記憶體）。
set -u
REPO="D:/project/twquant/.claude/worktrees/quant-trading-strategies-268d12"
DATA="D:/project/twquant/data"
cd "$REPO" || exit 1
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
export PYTHONIOENCODING=utf-8
# CLAUDE.md §0.2：研究登記簿（DSR 試驗數）必須寫進正本，不是 worktree 自己的 data/
export TWQUANT_DATA_DIR="$DATA"
LOG="$DATA/logs/tail_round11_$(date +%Y%m%d-%H%M).log"
B="$DATA/tracks_tail11"
mkdir -p "$B"
say() { echo "=== $(date +%H:%M:%S) $*" | tee -a "$LOG"; }

bt() {  # name start end
  local dest="$B/${1}_1x.json"
  [ -f "$dest" ] && { say "$1 已存在，跳過"; return 0; }
  say "回測 $1（$2 ~ $3）"
  uv run --all-groups python -m twquant backtest --strategy "configs/strategies/${1}.yaml" \
      --start "$2" --end "$3" --capital 80000000 --costs costs.yaml --out "$dest" >>"$LOG" 2>&1
  local rc=$?
  say "$1 rc=$rc"
  return $rc
}
bt tail_f1 2017-01-03 2026-02-28
bt tail_f2 2017-01-03 2026-02-28
bt tail_f3 2017-01-03 2026-02-28

say "報告"
uv run --all-groups python scripts/d149_report.py --bundles "$B" 2>&1 | tee -a "$LOG"
say "第十一輪全部完成。日誌：$LOG"
