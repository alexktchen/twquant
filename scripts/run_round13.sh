#!/usr/bin/env bash
# D-153 第十三輪：載入 A20 樣本外分數（一次）→ 六個回測依序跑（一次一個）→ 報告（只報不判）。
# 順序 S0、S1、S3、S2、R0、R1；每一步記結束碼；已有結果包的臂跳過（重跑不覆寫）。
set -u
REPO="D:/project/twquant/.claude/worktrees/quant-trading-strategies-268d12"
DATA="D:/project/twquant/data"
MODEL="$DATA/models/lgbm_quietA20_tu_h20_20260430_655df6d7"
MODEL_ID="lgbm_quietA20_tu_h20_20260430_655df6d7"
cd "$REPO" || exit 1
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1 UV_LINK_MODE=copy
export TWQUANT_DATA_DIR="$DATA" # CLAUDE.md §0.2：研究登記簿寫進正本
LOG="$DATA/logs/round13_$(date +%Y%m%d-%H%M).log"
B="$DATA/tracks_round13"
mkdir -p "$B"
say() { echo "=== $(date +%H:%M:%S) $*" | tee -a "$LOG"; }

# ── 1. 分數載入（已有該 model_id 的列就跳過）────────────────────────────────
have=$(echo "select count(*) from serving.ml_scores where model_id='$MODEL_ID'" |
  MSYS_NO_PATHCONV=1 docker exec -i twquant-pg-1 psql -U twquant -d twquant -tA)
if [ "${have:-0}" -gt 0 ]; then
  say "ml_scores 已有 $MODEL_ID 的 $have 列，跳過載入"
else
  say "載入 A20 樣本外分數"
  uv run --all-groups python -m twquant ml backfill-scores --model "$MODEL" >>"$LOG" 2>&1
  rc=$?
  say "backfill-scores rc=$rc"
  [ $rc -ne 0 ] && exit 1
fi

# ── 2. 回測（一次一個；窗口＝A20 樣本外分數範圍，不進保留段）──────────────
bt() { # name
  local dest="$B/${1}.json"
  [ -f "$dest" ] && { say "$1 已存在，跳過"; return 0; }
  say "回測 $1（2017-10-02 ~ 2026-04-30）"
  uv run --all-groups python -m twquant backtest --strategy "configs/strategies/${1}.yaml" \
    --start 2017-10-02 --end 2026-04-30 --capital 80000000 --costs costs.yaml --out "$dest" >>"$LOG" 2>&1
  local rc=$?
  say "$1 rc=$rc"
  return $rc
}
for arm in quiet_s0 quiet_s1 quiet_s3 quiet_s2 quiet_r0 quiet_r1; do
  bt "$arm" || { say "$arm 失敗，停止"; exit 1; }
done

# ── 3. 報告 ────────────────────────────────────────────────────────────────
say "報告"
uv run --all-groups python scripts/d153_report.py --bundles "$B" 2>&1 | tee -a "$LOG"
say "第十三輪完成。日誌：$LOG"
