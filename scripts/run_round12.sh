#!/usr/bin/env bash
# D-151 第十二輪：物化五支新因子 → 備份 → 訓練六個模型 → walk-forward 報告（只報不判）。
# 保留段（§八）**不在這支腳本裡**：walk-forward 報告與模型凍結 commit 之後，另外手動開一次。
# 一次只跑一個重工作；每一步都記結束碼，失敗即停（不帶著半套資料往下跑）。
set -u
REPO="D:/project/twquant/.claude/worktrees/quant-trading-strategies-268d12"
DATA="D:/project/twquant/data"
cd "$REPO" || exit 1
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1 UV_LINK_MODE=copy
# CLAUDE.md §0.2：研究登記簿（DSR 試驗數）必須寫進正本，不是 worktree 自己的 data/
export TWQUANT_DATA_DIR="$DATA"
LOG="$DATA/logs/round12_$(date +%Y%m%d-%H%M).log"
MARK="$DATA/logs/round12_materialized.ok"
NEW="price.bb_squeeze_120,price.ma_tangle,price.vp_poc_dist_60,price.vp_support_hold_20,price.vp_near_share_60"
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

# ── 1. 物化（單一程序；D-151 §十.3）──────────────────────────────────────────
if [ -f "$MARK" ]; then
  say "物化已完成過（$MARK），跳過"
else
  step "物化 5 支新因子 2015-06-01~2026-08-31" \
    uv run --all-groups python scripts/materialize_long_history.py \
    --from 2015-06-01 --to 2026-08-31 --factors "$NEW"
  touch "$MARK"
fi

# ── 2. 備份：大型 PG 工作之後先 dump（WHERE-IS-EVERYTHING §8）────────────────
stamp=$(date +%Y%m%d-%H%M)
dump="$DATA/backup/twquant_serving_${stamp}_post-remat-d151.dump"
step "pg_dump" env MSYS_NO_PATHCONV=1 docker exec twquant-pg-1 \
  pg_dump -U twquant -d twquant -n serving -Fc -Z 6 -f /tmp/r12.dump
step "複製備份" env MSYS_NO_PATHCONV=1 docker cp twquant-pg-1:/tmp/r12.dump "$dump"
step "清暫存" env MSYS_NO_PATHCONV=1 docker exec twquant-pg-1 rm -f /tmp/r12.dump
ls -la "$dump" | tee -a "$LOG"

# ── 3. 訓練（一次一個；同窗對照見 D-151 §九）───────────────────────────────
train() {  # 配置 起日
  step "訓練 $1（--from $2）" uv run --all-groups python -m twquant ml train \
    --config "configs/$1.yaml" --from "$2" --to 2026-08-31 --out "$DATA/models"
}
train ml_quiet_a 2015-09-01
train ml_hot_b 2015-09-01
train ml_quiet_a22 2022-03-01
train ml_quiet_ap 2022-03-01
train ml_hot_b22 2022-03-01
train ml_hot_bp 2022-03-01

# ── 4. walk-forward 報告（只報不判）──────────────────────────────────────────
latest() { ls -1dt "$DATA/models"/lgbm_"$1"_h20_* 2>/dev/null | head -1; }
args=()
for spec in "A ml_quiet_a quietA_tu" "A22 ml_quiet_a22 quietA22_tu" "Ap ml_quiet_ap quietAp_tu" \
  "B ml_hot_b hotB_dp" "B22 ml_hot_b22 hotB22_dp" "Bp ml_hot_bp hotBp_dp"; do
  set -- $spec
  dir=$(latest "$3")
  [ -z "$dir" ] && { say "找不到 $1 的模型（lgbm_$3_h20_*），停止"; exit 1; }
  say "$1 → $(basename "$dir")"
  args+=(--arm "$1" "configs/$2.yaml" "$dir")
done
say "walk-forward 報告"
uv run --all-groups python scripts/d151_report.py "${args[@]}" 2>&1 | tee -a "$LOG"
say "第十二輪 walk-forward 完成。日誌：$LOG（保留段待凍結 commit 後另開）"
