#!/usr/bin/env bash
# D-152：模型 A 的標籤門檻梯度——訓練 A20、A25（一次一個）→ 三臂 walk-forward 報告（A 用凍結模型）。
# 保留段**不開**（D-152 §二）；只用 walk-forward 樣本外。每一步記結束碼，失敗即停。
set -u
REPO="D:/project/twquant/.claude/worktrees/quant-trading-strategies-268d12"
DATA="D:/project/twquant/data"
A_DIR="$DATA/models/lgbm_quietA_tu_h20_20260430_8bdcd508" # D-151 §十五 凍結的 A，不重訓
cd "$REPO" || exit 1
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1 UV_LINK_MODE=copy
# CLAUDE.md §0.2：研究登記簿（DSR 試驗數）必須寫進正本，不是 worktree 自己的 data/
export TWQUANT_DATA_DIR="$DATA"
LOG="$DATA/logs/d152_$(date +%Y%m%d-%H%M).log"
say() { echo "=== $(date +%H:%M:%S) $*" | tee -a "$LOG"; }
step() {  # 名稱 指令...
  local name=$1
  shift
  say "開始：$name"
  "$@" >>"$LOG" 2>&1
  local rc=$?
  say "$name rc=$rc"
  [ $rc -ne 0 ] && { say "失敗，停止"; exit 1; }
  return 0
}
[ -d "$A_DIR" ] || { say "找不到凍結的 A：$A_DIR"; exit 1; }

# ── 1. 訓練（一次一個；資料窗與 A 相同）────────────────────────────────────
train() {  # 配置 起日
  step "訓練 $1（--from $2）" uv run --all-groups python -m twquant ml train \
    --config "configs/$1.yaml" --from "$2" --to 2026-08-31 --out "$DATA/models"
}
train ml_quiet_a20 2015-09-01
train ml_quiet_a25 2015-09-01

# ── 2. walk-forward 報告（只報不判；三臂同一次輸出）─────────────────────────
latest() { ls -1dt "$DATA/models"/lgbm_"$1"_h20_* 2>/dev/null | head -1; }
args=(--arm A configs/ml_quiet_a.yaml "$A_DIR")
for spec in "A20 ml_quiet_a20 quietA20_tu" "A25 ml_quiet_a25 quietA25_tu"; do
  set -- $spec
  dir=$(latest "$3")
  [ -z "$dir" ] && { say "找不到 $1 的模型（lgbm_$3_h20_*），停止"; exit 1; }
  say "$1 → $(basename "$dir")"
  args+=(--arm "$1" "configs/$2.yaml" "$dir")
done
say "walk-forward 報告"
uv run --all-groups python scripts/d151_report.py "${args[@]}" 2>&1 | tee -a "$LOG"
say "D-152 完成。日誌：$LOG"
