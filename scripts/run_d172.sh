#!/usr/bin/env bash
# D-172／T-342：潛伏飆股分數門檻臂的開發期參考回測——與第十三輪 S0 零股組同設定（1,000 萬、--lot-size 1、
# 2017-10-02 ~ 2026-04-30，costs.yaml），只換 entry_threshold（＋ETF 資金流排除）。對照 data/tracks_round13/quiet_s0_10m_lot1.json。
# **只當「空手的代價」參考，不當通過依據**（開發期已污染，D-141）；正式判讀在第二保留期（configs/holdout2_quiet_threshold.yaml）。
# 一次一個；跑的時候不得改 src/twquant（CLI 最後才 lazy import reporting）。可續跑：結果包已存在就跳過。
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DATA="D:/project/twquant/data"
cd "$REPO" || exit 1
export TWQUANT_PG_DSN="${TWQUANT_PG_DSN:-postgresql://twquant:twquant@localhost:5433/twquant}"
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1 UV_LINK_MODE=copy
export TWQUANT_DATA_DIR="$DATA"
LOG="$DATA/logs/d172_$(date +%Y%m%d-%H%M).log"
B="$DATA/tracks_d172"
mkdir -p "$B"
say() { echo "=== $(date +%H:%M:%S) $*" | tee -a "$LOG"; }
bt() { # name
  local dest="$B/${1}_10m_lot1.json"
  [ -f "$dest" ] && { say "$1 已存在，跳過"; return 0; }
  say "回測 $1 → $dest"
  uv run --all-groups python -m twquant backtest --strategy "configs/strategies/${1}.yaml" \
    --start 2017-10-02 --end 2026-04-30 --capital 10000000 --lot-size 1 --costs costs.yaml \
    --out "$dest" >>"$LOG" 2>&1
  local rc=$?
  say "$1 rc=$rc"
  return $rc
}
# 先跑門檻臂（沒有排除），再跑排除臂；中間的門檻先跑，讓部分結果就有參考價值
for name in quiet_p40 quiet_p30 quiet_p50 quiet_p35 quiet_p45 quiet_p40x quiet_p30x quiet_p50x quiet_p35x quiet_p45x; do
  bt "$name" || say "$name 失敗（繼續下一支）"
done
say "D-172 開發期參考回測完成。日誌：$LOG"
