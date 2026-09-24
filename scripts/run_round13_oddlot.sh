#!/usr/bin/env bash
# D-153 §一 補充二：同六個配置以 1,000 萬・零股（--lot-size 1）再跑一次；結果包 *_10m_lot1.json。
# 在 run_round13_retail.sh（1,000 萬整張組）跑完之後才啟動；分數已在 ml_scores。一次一個。
set -u
REPO="D:/project/twquant/.claude/worktrees/quant-trading-strategies-268d12"
DATA="D:/project/twquant/data"
cd "$REPO" || exit 1
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1 UV_LINK_MODE=copy
export TWQUANT_DATA_DIR="$DATA"
LOG="$DATA/logs/round13_oddlot_$(date +%Y%m%d-%H%M).log"
B="$DATA/tracks_round13"
say() { echo "=== $(date +%H:%M:%S) $*" | tee -a "$LOG"; }
bt() { # name
  local dest="$B/${1}_10m_lot1.json"
  [ -f "$dest" ] && { say "$1（1,000 萬・零股）已存在，跳過"; return 0; }
  say "回測 $1（1,000 萬・零股；2017-10-02 ~ 2026-04-30）"
  uv run --all-groups python -m twquant backtest --strategy "configs/strategies/${1}.yaml" \
    --start 2017-10-02 --end 2026-04-30 --capital 10000000 --lot-size 1 --costs costs.yaml \
    --out "$dest" >>"$LOG" 2>&1
  local rc=$?
  say "$1 rc=$rc"
  return $rc
}
for arm in quiet_s0 quiet_s1 quiet_s3 quiet_s2 quiet_r0 quiet_r1; do
  bt "$arm" || { say "$arm 失敗，停止"; exit 1; }
done
say "報告（三組一起）"
uv run --all-groups python scripts/d153_report.py --bundles "$B" 2>&1 | tee -a "$LOG"
say "第十三輪 1,000 萬・零股組完成。日誌：$LOG"
