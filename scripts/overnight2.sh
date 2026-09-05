#!/usr/bin/env bash
# 第二段連續作業：G 收尾 → M1/M4/M5/M6 → MG 七格 → 判定 → 報告。
# 每步失敗不中斷後續；不同時開兩批回測（記憶體只夠一批 + 補資料）。
set -u
cd "$(dirname "$0")/.." || exit 1
export PYTHONIOENCODING=utf-8
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
LOG="D:/project/twquant/data/overnight.log"
D="D:/project/twquant/data"
say(){ printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$LOG"; }
run(){ say "▶ $1"; shift; "$@" >>"$LOG" 2>&1 && say "  ✓" || say "  ✗ exit $? — 繼續"; }
wait_for(){ local f="$D/$1_run.log" max=${2:-300} n=0
  say "⏳ 等 $1（上限 ${max} 分）"
  while ! grep -qE "次全部成功|失敗 [0-9]+ 次" "$f" 2>/dev/null; do
    sleep 120; n=$((n+2)); [ $n -ge "$max" ] && { say "  ⚠ 逾時，跳過"; return 1; }
  done
  say "  ✓ $(grep -E '全部成功|失敗 [0-9]' "$f" | tail -1)"; }
rebuild(){ run "重建報告（$1）" uv run --all-groups python scripts/report_bundle.py
           run "產生 HTML（$1）" uv run --all-groups python scripts/build_report.py; }

say "════ 第二段開始 ════"
wait_for tracks_g 200
rebuild "G 完成"

say "▶ 啟動 M1/M4/M5/M6（12 次）"
uv run --all-groups python scripts/run_tracks.py --tracks M1,M4,M5,M6 \
  --out "$D/tracks_m" > "$D/tracks_m_run.log" 2>&1
say "  M 系列結束：$(grep -E '全部成功|失敗 [0-9]' "$D/tracks_m_run.log" | tail -1)"
rebuild "M 完成"
run "D-071 判定" uv run --all-groups python scripts/d071_verdict.py

say "▶ 啟動 MG 七格（21 次，M1 基座）"
uv run --all-groups python scripts/run_tracks.py --tracks MG1,MG2,MG3,MG4,MG5,MGALL,MGALLM \
  --out "$D/tracks_mg" > "$D/tracks_mg_run.log" 2>&1
say "  MG 結束：$(grep -E '全部成功|失敗 [0-9]' "$D/tracks_mg_run.log" | tail -1)"
rebuild "MG 完成"
run "D-071 判定（含 MG）" uv run --all-groups python scripts/d071_verdict.py

run "資料稽核" uv run --all-groups python scripts/audit_data.py --since 2022-07-01 \
    --tables prices_daily --out docs/reports/data-audit.md
run "靜默失效檢查" uv run --all-groups python scripts/audit_silent.py --out docs/reports/silent-audit.md
run "閘門" uv run --all-groups python -m twquant check
say "════ 第二段結束 ════　報告：$D/reports/backtest_report.html"
