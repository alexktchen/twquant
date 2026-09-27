#!/usr/bin/env bash
# D-153 §一 補充：同六個配置以 1,000 萬（散戶規模、參與率不卡）再跑一次；結果包 *_10m.json。
# 在 run_round13.sh（8,000 萬組）跑完之後才啟動；分數已在 ml_scores，不再載入。一次一個。
set -u
REPO="D:/project/twquant/.claude/worktrees/quant-trading-strategies-268d12"
DATA="D:/project/twquant/data"
cd "$REPO" || exit 1
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1 UV_LINK_MODE=copy
export TWQUANT_DATA_DIR="$DATA"
LOG="$DATA/logs/round13_retail_$(date +%Y%m%d-%H%M).log"
B="$DATA/tracks_round13"
say() { echo "=== $(date +%H:%M:%S) $*" | tee -a "$LOG"; }
bt() { # name
  local dest="$B/${1}_10m.json"
  [ -f "$dest" ] && { say "$1（1,000 萬）已存在，跳過"; return 0; }
  say "回測 $1（1,000 萬；2017-10-02 ~ 2026-04-30）"
  uv run --all-groups python -m twquant backtest --strategy "configs/strategies/${1}.yaml" \
    --start 2017-10-02 --end 2026-04-30 --capital 10000000 --costs costs.yaml --out "$dest" >>"$LOG" 2>&1
  local rc=$?
  say "$1 rc=$rc"
  return $rc
}
for arm in quiet_s0 quiet_s1 quiet_s3 quiet_s2 quiet_r0 quiet_r1; do
  bt "$arm" || { say "$arm 失敗，停止"; exit 1; }
done
say "報告（兩組一起）"
uv run --all-groups python scripts/d153_report.py --bundles "$B" 2>&1 | tee -a "$LOG"
say "第十三輪 1,000 萬組完成。日誌：$LOG"
