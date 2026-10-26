#!/usr/bin/env bash
# 每日紙上交易：在 research/ms-paper-trade 分支上執行，產出 paper/ledger.csv 與 paper/FORWARD.md。
# 逐筆資料下載到暫存資料夾（不進版控）。commit／push 由呼叫端負責。
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
WORK="${WORK:-$(mktemp -d)}"
python3 research/momentum_scalping/scripts/forward_paper.py \
    --work "$WORK" \
    --ledger research/momentum_scalping/paper/ledger.csv \
    --report research/momentum_scalping/paper/FORWARD.md
