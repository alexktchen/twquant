#!/usr/bin/env bash
# 連續作業：等現有回測跑完 → 判定 → 更新報告；每完成一段就更新，中途失敗不中斷後續。
#
# 設計原則：
#   1. **每一步都不中斷後續**——單步失敗只記錄，不讓整晚白跑（黃金規則 5 的精神：
#      失敗要留下證據，但不該讓一個失敗吃掉其他已完成的工作）。
#   2. **報告每完成一段就重建**，所以隨時打開都是最新的；尚缺的筆數會照實標示。
#   3. 不啟動任何新的長時間回測——M/G/MG 三批已在跑，再開會搶記憶體拖垮 PG。
#
# 用法：bash scripts/overnight.sh   （建議 nohup 背景執行）

set -u
cd "$(dirname "$0")/.." || exit 1
export PYTHONIOENCODING=utf-8
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
LOG="D:/project/twquant/data/overnight.log"
DATA="D:/project/twquant/data"

say() { printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$LOG"; }
run() { say "▶ $1"; shift; "$@" >>"$LOG" 2>&1 && say "  ✓ 完成" || say "  ✗ 失敗（exit $?）——繼續下一步"; }

# 等某個 runner 的日誌出現終止訊息；最長等 max_min 分鐘後放棄（不中斷後續）
wait_for() {
  local f="$DATA/$1_run.log" max=${2:-300} n=0
  say "⏳ 等 $1（最多 ${max} 分）"
  while ! grep -qE "次全部成功|失敗 [0-9]+ 次" "$f" 2>/dev/null; do
    sleep 120; n=$((n+2))
    if [ $n -ge "$max" ]; then say "  ⚠ $1 逾時未完成，跳過等待"; return 1; fi
  done
  say "  ✓ $1 已結束：$(grep -E '全部成功|失敗 [0-9]' "$f" | tail -1)"
}

rebuild() {
  run "重建報告（$1）" uv run --all-groups python scripts/report_bundle.py
  run "產生 HTML（$1）" uv run --all-groups python scripts/build_report.py
}

say "════ 連續作業開始 ════"
rebuild "起始"

# ── 1. M2/M3 隔離對 → D-071 判定（ML 當池子 vs 動能當池子）─────────────
wait_for tracks_m23 240
run "D-071 判定" uv run --all-groups python scripts/d071_verdict.py
rebuild "M2/M3 後"

# ── 2. G 濾網矩陣 → 邊際效果進報告 ────────────────────────────────────
wait_for tracks_g 300
rebuild "G 矩陣後"

# ── 3. MG 疊加（排隊中，等前兩批釋出記憶體才會啟動）───────────────────
wait_for tracks_mg 360
run "D-071 判定（含 MG）" uv run --all-groups python scripts/d071_verdict.py
rebuild "MG 後"

# ── 4. 全部完成後的收尾檢查 ───────────────────────────────────────────
run "資料稽核（窗內）" uv run --all-groups python scripts/audit_data.py \
    --since 2022-07-01 --tables prices_daily --out docs/reports/data-audit.md
run "靜默失效靜態檢查" uv run --all-groups python scripts/audit_silent.py \
    --out docs/reports/silent-audit.md
run "閘門" uv run --all-groups python -m twquant check

say "════ 連續作業結束 ════"
say "報告：D:/project/twquant/data/reports/backtest_report.html"
