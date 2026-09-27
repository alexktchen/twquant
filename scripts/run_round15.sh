#!/usr/bin/env bash
# D-159 §四 第十五輪組合回測：quiet_t_ma／quiet_t_atr 以第十三輪 S0 的零股組設定（1,000 萬、--lot-size 1、
# 2017-10-02 ~ 2026-04-30）只換出場。最後重跑 quiet_s0 一次（結果包另存 *_r15.json），確認加了三種出場的引擎
# 對 S0 逐位元不變，與 data/tracks_round13/quiet_s0_10m_lot1.json 對照。一次一個（不並行，D-159 之前 Docker 當過）。
# 跑的時候不得改 src/twquant（CLI 最後才 lazy import reporting）。
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DATA="D:/project/twquant/data"
cd "$REPO" || exit 1
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1 UV_LINK_MODE=copy
export TWQUANT_DATA_DIR="$DATA"
LOG="$DATA/logs/round15_$(date +%Y%m%d-%H%M).log"
B="$DATA/tracks_round15"
mkdir -p "$B"
say() { echo "=== $(date +%H:%M:%S) $*" | tee -a "$LOG"; }
bt() { # name dest
  [ -f "$2" ] && { say "$1 已存在，跳過"; return 0; }
  say "回測 $1（1,000 萬・零股；2017-10-02 ~ 2026-04-30）→ $2"
  uv run --all-groups python -m twquant backtest --strategy "configs/strategies/${1}.yaml" \
    --start 2017-10-02 --end 2026-04-30 --capital 10000000 --lot-size 1 --costs costs.yaml \
    --out "$2" >>"$LOG" 2>&1
  local rc=$?
  say "$1 rc=$rc"
  return $rc
}
bt quiet_t_ma "$B/quiet_t_ma_10m_lot1.json" || exit 1
bt quiet_t_atr "$B/quiet_t_atr_10m_lot1.json" || exit 1
bt quiet_s0 "$B/quiet_s0_10m_lot1_r15.json" || exit 1
say "第十五輪組合回測完成。日誌：$LOG"
