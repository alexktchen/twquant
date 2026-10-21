#!/usr/bin/env bash
# 用法（換 PG 映像、排序規則版本變了之後）：bash scripts/pg_reindex_collatable.sh；T-412 2026-10-20 實跑 114 個索引約 2 小時。
# 只重建含可排序文字欄的 btree 索引（musl → glibc 排序規則不同），一個一個來、關掉平行建索引（REINDEX DATABASE 在平行工作時 segfault）。
# 小的先；每個索引前先寫 log，再 segfault 就知道是哪一個。
set -u
mkdir -p data
LOG=${PG_REINDEX_LOG:-data/pg_reindex.log}
echo "start $(date '+%F %T')" > "$LOG"
PSQL="docker exec twquant-pg-1 psql -U twquant -d twquant -Atc"
$PSQL "select n.nspname||'.'||c.relname from pg_index i join pg_class c on c.oid=i.indexrelid join pg_namespace n on n.oid=c.relnamespace where i.indcollation::text ~ '[1-9]' order by pg_relation_size(c.oid)" > data/pg_reindex_list.txt
N=$(wc -l < data/pg_reindex_list.txt)
i=0
while read -r idx; do
  i=$((i+1))
  echo "[$i/$N] $(date '+%T') $idx" >> "$LOG"
  docker exec twquant-pg-1 psql -U twquant -d twquant -v ON_ERROR_STOP=1 -Atc "set max_parallel_maintenance_workers = 0; set maintenance_work_mem = '256MB'; reindex index \"${idx%%.*}\".\"${idx#*.}\"" >> "$LOG" 2>&1 || { echo "  FAILED $idx" >> "$LOG"; sleep 20; }
done < data/pg_reindex_list.txt
echo "done $(date '+%F %T')" >> "$LOG"
$PSQL "select pg_size_pretty(pg_database_size('twquant'))" >> "$LOG"
