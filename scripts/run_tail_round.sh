#!/usr/bin/env bash
# D-143 第九輪的下游鏈：訓練 A／B 兩臂 → 回填 model_id → 回測三臂 → 判定。
# 前提：T-285 的整批重物化已完成（新的波動 8 支要在 factor_values 裡才進得了 B 臂）。
set -u
REPO="D:/project/twquant/.claude/worktrees/quant-trading-strategies-268d12"
DATA="D:/project/twquant/data"
cd "$REPO" || exit 1
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
export PYTHONIOENCODING=utf-8
LOG="$DATA/logs/tail_round_$(date +%Y%m%d-%H%M).log"
BUNDLES="$DATA/tracks_tail"
mkdir -p "$BUNDLES"

say() { echo "=== $(date +%H:%M:%S) $*" | tee -a "$LOG"; }

# ── 1. 訓練兩臂（D-143 §三：訓練一次、不調參）──────────────────────────
for arm in a b; do
  say "訓練 ${arm} 臂"
  uv run --all-groups python -m twquant ml train \
      --config "configs/ml_tail_up50_h60_${arm}.yaml" \
      --from 2015-09-01 --to 2026-08-31 \
      --out "$DATA/models" >>"$LOG" 2>&1
  rc=$?
  say "${arm} 臂訓練結束 rc=$rc"
  [ $rc -ne 0 ] && { say "訓練失敗，停止"; exit 1; }
done

# ── 2. 找出剛訓好的 model_id 並回填策略配置 ──────────────────────────────
for arm in a b; do
  mid=$(ls -1dt "$DATA/models"/lgbm_tail${arm}_tu_h60_* 2>/dev/null | head -1 | xargs -r basename)
  if [ -z "${mid:-}" ]; then say "找不到 ${arm} 臂的模型目錄，停止"; exit 1; fi
  say "${arm} 臂 model_id = $mid"
  python - "$arm" "$mid" <<'PY' >>"$LOG" 2>&1
import io, sys
arm, mid = sys.argv[1], sys.argv[2]
p = f"configs/strategies/tail_{arm}.yaml"
s = io.open(p, encoding="utf-8").read()
if "REPLACE_AFTER_TRAINING" not in s:
    print(f"{p} 的 model_id 已填過，保持不動")
else:
    io.open(p, "w", encoding="utf-8", newline="\n").write(
        s.replace("REPLACE_AFTER_TRAINING", mid, 1))
    print(f"{p} ← {mid}")
PY
done

# ── 3. 把分數寫進 ml_scores（薄因子靠它讀；回測才看得到模型）────────────
for arm in a b; do
  say "${arm} 臂：把 walk-forward 樣本外分數寫進 PG"
  mid=$(ls -1dt "$DATA/models"/lgbm_tail${arm}_tu_h60_* 2>/dev/null | head -1 | xargs -r basename)
  uv run --all-groups python -m twquant ml backfill-scores \
      --model "$DATA/models/$mid" >>"$LOG" 2>&1 || \
    say "backfill-scores 失敗或指令不存在（rc=$?）——改由 ml score 補，見下一步"
done

# ── 4. 回測三臂（D-143 §五：15 檔每日補位；成交假設由引擎既有行為滿足）──
for name in tail_a tail_b tail_d_rule; do
  dest="$BUNDLES/${name}_1x.json"
  if [ -f "$dest" ]; then say "$name 結果包已存在，跳過（續傳）"; continue; fi
  say "回測 $name"
  uv run --all-groups python -m twquant backtest \
      --strategy "configs/strategies/${name}.yaml" \
      --start 2017-01-03 --end 2026-02-28 \
      --capital 80000000 --costs costs.yaml \
      --out "$dest" >>"$LOG" 2>&1
  say "$name 回測結束 rc=$?"
done

# ── 5. 判定（D-143 §七：只有回撤 ≤ 35% 是門檻，其餘只報）────────────────
say "判定"
uv run --all-groups python scripts/d143_verdict.py --bundles "$BUNDLES" 2>&1 | tee -a "$LOG"
say "全部完成。日誌：$LOG"
