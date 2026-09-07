#!/usr/bin/env bash
# 重建報告的資料與 HTML。發布 artifact 需要 Claude，故本腳本只把檔案更新到最新。
# 每跑完一組回測就執行（使用者要求），或手動執行。
set -u
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
export TWQUANT_PG_DSN="postgresql://twquant:twquant@localhost:5433/twquant"
uv run --all-groups python scripts/build_lw_section.py
uv run --all-groups python scripts/timing_all.py >/dev/null
uv run --all-groups python scripts/build_two_window_report.py >/dev/null
uv run --all-groups python scripts/render_two_window_report.py
