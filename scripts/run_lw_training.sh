#!/usr/bin/env bash
# D-088：長窗口 ML 三條線的 walk-forward 訓練。
#
# 三份配置與其短窗口版**逐字相同，只多 `sample_tag: lw`**——唯一的實質差別在
# 這裡的 `--from`。sample_tag 不是註記而是身分：沒有它三條線的 model_id 會與
# 短窗口版逐字相同（fingerprint 只看超參數不看窗口），而 ml_scores 按 model_id
# 索引，兩批分數會混進同一條線（T-172）。
#
# 序列跑（非平行）——每條線都要把 12~15 個因子的全史載進記憶體，而長窗口的
# 列數是短窗口的 2.5 倍；平行會讓峰值加倍。
set -u
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
# T-160：讓 research_log（＝DSR 的試驗計數）也寫進主 checkout，而不是 worktree
export TWQUANT_DATA_DIR="D:/project/twquant/data"
# CLAUDE.md §0.2：模型是跨 worktree 共用的產物，一律明確指到主 checkout 的正本。
OUT=D:/project/twquant/data/models
# 金庫期邊界與短窗口版逐字相同——長的是訓練歷史，不是驗證期。
TO=2026-08-28

# 起日＝各自特徵齊備日（D-088 表列）。基礎線 12 個特徵最晚齊備者是
# price.mom_60d（2015-08-26）；F／R 卡在 price.high_52w 的 52 週回看，
# 取 2017-01-03 與 D-087 的回測窗口起日對齊。
run() {  # run <config> <from>
  local cfg=$1 from=$2 log="logs/train_${1}.log"
  echo "=== $(date +%F' '%T) 開始 ${cfg}（--from ${from}）===" | tee -a "$log"
  uv run --all-groups python -m twquant ml train \
      --config "configs/${cfg}.yaml" --from "$from" --to "$TO" --out "$OUT" >>"$log" 2>&1
  echo "=== $(date +%F' '%T) ${cfg} 結束 rc=$? ===" | tee -a "$log"
}

run ml_h20_cost_aware_lw    2015-09-01
run ml_f_revenue_surprise_lw 2017-01-03
run ml_r_drawdown60_lw       2017-01-03
echo "=== $(date +%F' '%T) 三條線全部結束 ==="
