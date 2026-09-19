#!/usr/bin/env bash
# 2026-09-10 事故重放（docs/INCIDENT-2026-09-10-docker-reset.md）。
#
# 前提：已用 data/backup/twquant_serving_20260903.dump 還原 serving schema（單一次、乾淨）。
# 本腳本把 09-03 之後「只存在 PG」的工作依原本的先後順序重放一次。
#
# 規則：
# - **序列執行，一次只跑一個重運算**——這次事故就是從「兩個 materialize 併發」開始的。
# - 每一步都先驗證再寫入；任一步失敗即停（set -e）。
# - 可續跑：`START_STEP=N bash …` 從第 N 步開始（第 0 步前置檢查永遠執行）。
# - **執行中絕對不要編輯**：bash 會重讀腳本。一律跑快照副本 `.run_recovery.running.sh`。
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
export TWQUANT_DATA_DIR="D:/project/twquant/data"
export MSYS_NO_PATHCONV=1
MODELS="D:/project/twquant/data/models"
LAKE_REBUILD="D:/project/twquant/data/lake_rebuild"
LOG=logs/recovery.log
START_STEP="${START_STEP:-0}"
mkdir -p logs

# 互斥鎖：mkdir 是原子操作，已存在就失敗。**不用 `ps | grep`**——那會比對到呼叫端自己的
# shell（它的命令列就含這個檔名），第一次續跑就是這樣被自己擋下的。
# 被硬殺時鎖會留著：下一次會明確拒絕並說明，確認沒有重放在跑再手動刪掉即可（寧可擋錯也不併發）。
LOCK=logs/recovery.lock
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "另一個重放正在執行（$LOCK 存在）；確認沒有之後手動刪掉該目錄再跑" >&2
  exit 1
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

say() { echo "=== $(date +%F' '%T) $*" | tee -a "$LOG"; }
py() { uv run --all-groups python "$@" 2>&1 | tee -a "$LOG"; }
tw() { uv run --all-groups python -m twquant "$@" 2>&1 | tee -a "$LOG"; }
want() { [ "$START_STEP" -le "$1" ]; }

say "開始（START_STEP=$START_STEP）"

say "0 前置檢查：還原必須完整（到 2026-09-02）且乾淨（主鍵都建得起來＝沒有重複列）"
# 只看日期範圍攔不住「兩個 pg_restore 併發」那種還原：日期完全正確、列數卻是兩倍，
# 而主鍵因為重複鍵建不起來。所以主鍵存在與否才是乾淨還原的判據。
py - <<'PY'
import psycopg
KEY_TABLES = ("prices_daily", "institutional_daily", "margin_daily", "broker_trades",
              "stock_valuation", "monthly_revenue", "factor_values", "composite_daily", "ml_scores")
with psycopg.connect("postgresql://twquant:twquant@localhost:5433/twquant") as c, c.cursor() as cur:
    cur.execute("select min(date), max(date), count(*) from serving.prices_daily")
    lo, hi, n = cur.fetchone()
    print(f"prices_daily {lo} ~ {hi}，{n:,} 列")
    if hi is None or str(hi) < "2026-09-02":
        raise SystemExit("還原不完整：prices_daily 未到 2026-09-02，拒絕往下重放")
    cur.execute(
        "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
        "join pg_constraint k on k.conrelid = c.oid and k.contype = 'p' "
        "where n.nspname = 'serving' and c.relname = any(%s)",
        (list(KEY_TABLES),),
    )
    have = {r[0] for r in cur.fetchall()}
    missing = sorted(set(KEY_TABLES) - have)
    if missing:
        raise SystemExit(f"還原不乾淨：這些表沒有主鍵（多半是重複列讓主鍵建不起來）：{missing}")
    print(f"主鍵齊全：{len(have)}/{len(KEY_TABLES)} 張關鍵表")
PY

if want 1; then
  say "1 serving sync：湖 data/clean → PG（09-03 之後的每日資料）"
  tw serving sync
fi

if want 2; then
  say "2 下市股的 stock_meta（fa9a844）"
  py scripts/backfill_stock_meta_history.py --check
  py scripts/backfill_stock_meta_history.py --write
fi

if want 3; then
  say "3 prices 2015-06~2019：先以 2022 重疊區逐欄驗證，再只寫 2020 之前（fa9a844）"
  py scripts/load_finmind_history.py --verify-overlap --from 2022-01-01 --to 2022-12-31
  py scripts/load_finmind_history.py --load --from 2015-06-01 --to 2019-12-31
fi

if want 4; then
  say "4 財報 2019 起：staging 對湖比對（湖已有即不寫——PG 那一份由第 5 步補）"
  py scripts/load_finmind_statements.py --check
  py scripts/load_finmind_statements.py --write
fi

if want 5; then
  say "5 financial_statements／corporate_events 從 lake_rebuild 重灌進 PG"
  # 這兩張表的 2019 起權威資料**只在 lake_rebuild**（09-02 資料重建的湖），主湖 data/clean 沒有，
  # 所以第 1 步的 sync 對它們是 +0。第 4 步與第 10 步的載入器又都假設「湖 ⊇ PG」：
  # 湖已有就不寫、或只寫 2020 之前並拿 PG 的 2020 後來驗證——PG 被清空後這個假設就破了。
  # 只 rebuild 這兩張：全表 sync 對 lake_rebuild 會拿舊快照蓋掉第 2 步剛補的 stock_meta。
  TWQUANT_DATA_DIR="$LAKE_REBUILD" tw serving rebuild --tables financial_statements,corporate_events
  py - <<'PY'
import psycopg
with psycopg.connect("postgresql://twquant:twquant@localhost:5433/twquant") as c, c.cursor() as cur:
    cur.execute("select count(*), min(date), max(date) from serving.financial_statements")
    n, lo, hi = cur.fetchone()
    print(f"financial_statements {n:,} 列  {lo} ~ {hi}")
    if n != 4_512_123:
        raise SystemExit(f"financial_statements 應為 4,512,123 列（lake_rebuild 與 staging 比對一致的數字），得到 {n:,}")
    cur.execute("select count(*), min(date), max(date) from serving.corporate_events")
    n, lo, hi = cur.fetchone()
    print(f"corporate_events {n:,} 列  {lo} ~ {hi}")
    if n == 0:
        raise SystemExit("corporate_events 仍為 0 列")
PY
fi

if want 6; then
  say "6 來源表回補到 2015-06：institutional／margin／valuation（0bd6e08）"
  for t in institutional margin valuation; do
    py scripts/load_finmind_history_tables.py --table "$t" --verify
    py scripts/load_finmind_history_tables.py --table "$t" --load
  done
fi

if want 7; then
  say "7 財報回補到 2015-06（0bd6e08）；月營收已由第 1 步 sync 補齊，不重抓 MOPS"
  py scripts/load_finmind_history_tables.py --table statements --verify
  py scripts/load_finmind_history_tables.py --table statements --load
  py - <<'PY'
import psycopg
with psycopg.connect("postgresql://twquant:twquant@localhost:5433/twquant") as c, c.cursor() as cur:
    cur.execute("select count(*), min(date) from serving.monthly_revenue")
    n, lo = cur.fetchone()
    print(f"monthly_revenue {n:,} 列，最早 {lo}（事故前 227,237 列）")
    if n != 227_237:
        raise SystemExit("monthly_revenue 與事故前不符，需另跑 --table revenue")
PY
fi

if want 8; then
  say "8 index_daily：第 1 步 sync 已由主湖補回 2014-06 起，只驗證不重抓 FinMind"
  py - <<'PY'
import psycopg
with psycopg.connect("postgresql://twquant:twquant@localhost:5433/twquant") as c, c.cursor() as cur:
    cur.execute("select count(*), min(date), max(date) from serving.index_daily")
    n, lo, hi = cur.fetchone()
    print(f"index_daily {n:,} 列  {lo} ~ {hi}（事故前 5,890 列）")
    if str(lo) > "2014-06-30" or n < 5_890:
        raise SystemExit("index_daily 未回到事故前狀態，需另跑 load_index_history.py")
PY
fi

if want 9; then
  say "9 分割事件（T-151，09-05）——原本的順序在股利回補之前"
  py scripts/load_splits.py --check
  py scripts/load_splits.py --write
fi

if want 10; then
  say "10 corporate_events 回補到 2014（T-170，09-07；讀 staging，只寫 2020 之前）"
  py scripts/load_corporate_events_history.py
fi

if want 11; then
  say "11 factor_values 全期重物化 2015-06-01 ~ 2026-09-09（37 個因子，約 3 小時）"
  # 一次涵蓋 T-142（inf 修正）、T-149（2023 缺口）、T-167（長歷史）、T-180（2020 Q1 缺洞），
  # 比逐一重放那四次更一致。broker 三因子與 ml.* 不在內（見 materialize_long_history.py）。
  py scripts/materialize_long_history.py --from 2015-06-01 --to 2026-09-09
fi

if want 12; then
  say "12 ml_scores 回填（月頻模型帶 --carry-forward-to；*_pre_t180 與同 model_id 撞號，跳過）"
  for d in "$MODELS"/lgbm_*; do
    n=$(basename "$d")
    case "$n" in *_pre_t180) continue ;; esac
    case "$n" in
      *_rev_* | *_dd_*) tw ml backfill-scores --model "$d" --carry-forward-to 2026-02-28 ;;
      *) tw ml backfill-scores --model "$d" ;;
    esac
  done
fi

if want 13; then
  say "13 金庫期分數（D-106；available_at＝訊號日 19:00）"
  py scripts/open_vault_scores.py
fi

if want 14; then
  say "14 盤點：各表時間跨度，並與事故前的列數對照"
  py scripts/audit_history_span.py --skip-staging
  py - <<'PY'
import psycopg
# 事故前（2026-09-09 本 session 的 sync 輸出）記下的列數；factor_values／ml_scores 無事故前精確數字。
BEFORE = {"broker_trades": 57_273_750, "monthly_revenue": 227_237, "shareholding_dispersion": 151_110,
          "stock_valuation": 4_748_727, "financial_statements": 6_619_786, "index_daily": 5_890,
          "corporate_events": 18_366, "stock_meta": 2_185}
with psycopg.connect("postgresql://twquant:twquant@localhost:5433/twquant") as c, c.cursor() as cur:
    print(f"{'表':24s} {'事故前':>12s} {'現在':>12s}  差")
    for t, b in BEFORE.items():
        cur.execute(f"select count(*) from serving.{t}")
        n = cur.fetchone()[0]
        print(f"{t:24s} {b:>12,} {n:>12,}  {n - b:+,}")
PY
fi

if want 15; then
  say "15 立刻做一份新備份"
  stamp=$(date +%Y%m%d)
  docker exec twquant-pg-1 pg_dump -U twquant -d twquant -n serving -Fc -Z 6 -f /tmp/serving_post_recovery.dump
  docker cp twquant-pg-1:/tmp/serving_post_recovery.dump \
    "D:/project/twquant/data/backup/twquant_serving_${stamp}_post_recovery.dump"
  docker exec twquant-pg-1 rm -f /tmp/serving_post_recovery.dump
  ls -la D:/project/twquant/data/backup/ | tee -a "$LOG"
fi

say "重放完成"
