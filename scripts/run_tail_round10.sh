#!/usr/bin/env bash
# D-145 第十輪：訓練 C／B′（2022-03 起）→ 回填分數 → 回測 E／C／B′ → 只報不判。
# 前提：T-286（slots 模式的每日曝險目標）已合入且閘門全綠。
set -u
REPO="D:/project/twquant/.claude/worktrees/quant-trading-strategies-268d12"
DATA="D:/project/twquant/data"
cd "$REPO" || exit 1
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
export PYTHONIOENCODING=utf-8
LOG="$DATA/logs/tail_round10_$(date +%Y%m%d-%H%M).log"
B="$DATA/tracks_tail10"
mkdir -p "$B"
say() { echo "=== $(date +%H:%M:%S) $*" | tee -a "$LOG"; }

# ── 1. 訓練 C 與 B′（同窗口 2022-03-01 起；D-145 §三）─────────────────────
for arm in c b2; do
  say "訓練 ${arm} 臂（--from 2022-03-01）"
  uv run --all-groups python -m twquant ml train \
      --config "configs/ml_tail_up50_h60_${arm}.yaml" \
      --from 2022-03-01 --to 2026-08-31 --out "$DATA/models" >>"$LOG" 2>&1
  rc=$?; say "${arm} 臂訓練結束 rc=$rc"; [ $rc -ne 0 ] && { say "訓練失敗，停止"; exit 1; }
done

# ── 2. 回填 model_id 到策略配置，並把樣本外分數寫進 PG ──────────────────────
for arm in c b2; do
  mid=$(ls -1dt "$DATA/models"/lgbm_tail${arm}_tu_h60_* 2>/dev/null | head -1 | xargs -r basename)
  [ -z "${mid:-}" ] && { say "找不到 ${arm} 臂模型，停止"; exit 1; }
  say "${arm} 臂 model_id = $mid"
  python - "$arm" "$mid" <<'PY' >>"$LOG" 2>&1
import io, sys
arm, mid = sys.argv[1], sys.argv[2]
p = f"configs/strategies/tail_{arm}.yaml"
s = io.open(p, encoding="utf-8").read()
if "REPLACE_AFTER_TRAINING" in s:
    io.open(p, "w", encoding="utf-8", newline="\n").write(s.replace("REPLACE_AFTER_TRAINING", mid, 1))
    print(f"{p} <- {mid}")
else:
    print(f"{p} 已填過")
PY
  uv run --all-groups python -m twquant ml backfill-scores --model "$DATA/models/$mid" >>"$LOG" 2>&1 \
    || say "backfill-scores rc=$?（若已存在同 model_id 的分數屬正常）"
done

# ── 3. 回測：E 臂全窗口；C／B′ 兩年窗（D-145 §三）───────────────────────────
bt() {  # name start end
  local dest="$B/${1}_1x.json"
  [ -f "$dest" ] && { say "$1 已存在，跳過"; return 0; }
  say "回測 $1（$2 ~ $3）"
  uv run --all-groups python -m twquant backtest --strategy "configs/strategies/${1}.yaml" \
      --start "$2" --end "$3" --capital 80000000 --costs costs.yaml --out "$dest" >>"$LOG" 2>&1
  say "$1 rc=$?"
}
bt tail_b_voltgt 2017-01-03 2026-02-28
bt tail_c        2024-03-01 2026-02-28
bt tail_b2       2024-03-01 2026-02-28

# ── 4. 只報不判 ───────────────────────────────────────────────────────────────
say "報告"
uv run --all-groups python scripts/d145_report.py --bundles "$B" 2>&1 | tee -a "$LOG"
say "第十輪全部完成。日誌：$LOG"
