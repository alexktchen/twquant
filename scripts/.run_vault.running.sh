#!/usr/bin/env bash
# D-106 金庫期回測：8 支策略，2026-03-02 ~ 2026-08-31，價格報酬、成本 1×。
# **執行中絕對不要編輯**——bash 會重讀腳本，改到位移就會壞。
set -u
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
export TWQUANT_DATA_DIR="D:/project/twquant/data"
OUT="D:/project/twquant/data/tracks_vault_nodiv"
LOG=logs/vault.log
mkdir -p "$OUT" logs
say() { echo "=== $(date +%F' '%T) $*" | tee -a "$LOG"; }

run() {  # $1=代號 $2=配置
  say "開始 $1"
  uv run --all-groups python -m twquant backtest \
    --strategy "configs/strategies/$2.yaml" \
    --start 2026-03-02 --end 2026-08-31 \
    --capital 80000000 --costs costs.yaml --no-dividends \
    --out "$OUT/$1_$2_1x.json" >>"$LOG" 2>&1
  say "$1 結束 rc=$?"
}

run LW-C1        lw_c1_breakout
run LW-R2        lw_r2_c1_lvgate
run LW-B1        lw_b1_large_enhanced
run LW-B2        lw_b2_broad_capw
run LW-C1n50     lw_c1_n50
run LW-M1        lw_m1_ml_c1
run LW-F1        lw_f1_revpool
run LW-M1-faber  lw_m1_faber
say "金庫期 8 支全部結束"
