#!/usr/bin/env bash
# T-180 的後續鏈：補物化 2020 Q1 完成後 → 重訓三個模型 → 重回填分數 → 重跑 ML 格。
#
# **為什麼整條都要重來**：模型是在 2020 Q1 幾乎沒有特徵的資料上訓練的
# （h20 的 12 個特徵在 2020-01 只有 3 個），而那是三個壓力體制之一。
# 補完資料後模型會變，分數會變，凡是用到分數的格全部作廢。
#
# 舊模型不刪，改名保留——要能比對「補資料前後 IC 差多少」，那是這次修正值不值得的證據。
#
# **這個檔案在跑的時候絕對不要編輯**（本輪已因此壞掉兩次）。
set -u
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
export TWQUANT_DATA_DIR="D:/project/twquant/data"
LOG=logs/after_remat.log
MODELS=D:/project/twquant/data/models
say() { echo "=== $(date +%F' '%T) $*" | tee -a "$LOG"; }

say "等補物化完成（2020-01~03 的 12/12 特徵齊全）"
until uv run --all-groups python - <<'PY' >>"$LOG" 2>&1
import os, sys, psycopg
FEATS = [
    "fundamental.revenue_accel", "fundamental.revenue_yoy", "price.intraday_ret_20d",
    "price.mom_20d", "price.mom_60d", "price.overnight_ret_20d",
    "price.residual_reversal_5d", "price.reversal_5d", "price.rs_market_20d",
    "price.vol_20d", "volume.turnover_z_20d", "volume.vol_ratio_5d",
]
with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as c, c.cursor() as cur:
    cur.execute(
        "select date_trunc('month',date)::date, count(distinct factor) "
        "from serving.factor_values where factor = any(%s) "
        "and date between '2020-01-01' and '2020-03-31' group by 1 order by 1",
        (FEATS,),
    )
    got = cur.fetchall()
print("2020 Q1 逐月特徵數：", [(str(m), n) for m, n in got])
sys.exit(0 if got and all(n == len(FEATS) for _, n in got) else 1)
PY
do sleep 60; done
say "補物化完成"

# 舊模型改名保留（比對用），不刪
for m in lgbm_lw_net_h20_20260225_83890036 lgbm_lw_rev_h15_20260225_9d4a149c lgbm_lw_dd_h60_20260225_1bd7c6a0; do
  [ -d "$MODELS/$m" ] && [ ! -d "$MODELS/${m}_pre_t180" ] && mv "$MODELS/$m" "$MODELS/${m}_pre_t180" && say "保留舊模型 ${m}_pre_t180"
done

say "重訓三條線"
uv run --all-groups python -m twquant ml train --config configs/ml_h20_cost_aware_lw.yaml \
  --from 2015-09-01 --to 2026-08-28 --out "$MODELS" >>"$LOG" 2>&1
say "h20 重訓完 rc=$?"
uv run --all-groups python -m twquant ml train --config configs/ml_f_revenue_surprise_lw.yaml \
  --from 2017-01-03 --to 2026-08-28 --out "$MODELS" >>"$LOG" 2>&1
say "F 重訓完 rc=$?"
uv run --all-groups python -m twquant ml train --config configs/ml_r_drawdown60_lw.yaml \
  --from 2017-01-03 --to 2026-08-28 --out "$MODELS" >>"$LOG" 2>&1
say "R 重訓完 rc=$?"

say "重回填分數（月頻模型一律帶 --carry-forward-to，D-102 的教訓）"
for d in "$MODELS"/lgbm_lw_*; do
  case "$d" in *_pre_t180) continue;; esac
  case "$d" in
    *_rev_*|*_dd_*) uv run --all-groups python -m twquant ml backfill-scores --model "$d" --carry-forward-to 2026-02-28 >>"$LOG" 2>&1;;
    *) uv run --all-groups python -m twquant ml backfill-scores --model "$d" >>"$LOG" 2>&1;;
  esac
  say "回填 $(basename "$d") rc=$?"
done

say "全部完成——ML 格可以重跑了"
