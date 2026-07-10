# ==============================================================
# twquant 資料集回補指令集 —— 每段可獨立複製貼上執行（PowerShell）
# --------------------------------------------------------------
# 前置：
#   docker compose up -d pg               # PG 容器（serving 用）
#   docker build -t twquant:latest .      # 只有程式更新時才需重建（在 worktree 目錄）
# 資料湖：D:\project\twquant\data  （真實資料在主 repo；worktree 內沒有 data\）
# 每條指令都用 docker run -v 把真實資料湖掛進容器，避免 worktree 空 data 的坑。
# 改日期就改 --from / --to。
# ==============================================================

$Data = "D:\project\twquant\data"
$DSN  = "postgresql://twquant:twquant@pg:5432/twquant"

# ── 【擷取到資料湖】寫 D:\project\twquant\data\clean\<表>\date=... ──────────

# 1) stock_meta（個股名稱 / 產業別）— 最新快照，無日期區間
docker run --rm -v "${Data}:/app/data" twquant:latest ingest-meta

# 2) prices_daily（行情 OHLC，TWSE+TPEX）
docker run --rm -v "${Data}:/app/data" twquant:latest backfill --from 2023-01-01 --to 2024-12-31 --tables prices_daily

# 3) institutional_daily（三大法人買賣超，TWSE T86）
#    ⚠️ 2024 以前的 T86 欄位格式與現在不同，個別舊日會解析失敗使整批中斷；
#       建議分年小段跑（如一次半年），2025 以後穩定。
docker run --rm -v "${Data}:/app/data" twquant:latest backfill --from 2025-01-01 --to 2025-06-30 --tables institutional_daily --no-skip-existing

# 4) margin_daily（融資券餘額，TWSE MI_MARGN）
docker run --rm -v "${Data}:/app/data" twquant:latest backfill --from 2025-01-01 --to 2025-06-30 --tables margin_daily --no-skip-existing

# 5) monthly_revenue（月營收，MOPS）— 月區間 YYYY-MM
docker run --rm -v "${Data}:/app/data" twquant:latest backfill-revenue --from 2024-01 --to 2024-12

# 6) index_daily（加權 / 櫃買指數）— 月區間 YYYY-MM
docker run --rm -v "${Data}:/app/data" twquant:latest backfill-index --from 2024-01 --to 2024-12

# 7) shareholding_dispersion（集保股權分散，TDCC）— 來源只有最新一週、無歷史
docker run --rm -v "${Data}:/app/data" twquant:latest ingest-dispersion

# 8) broker_trades（主力券商買賣超，富邦DJ 母公司層級 top-15±）
#    每檔每日一請求；全市場一天約 1 小時。先用 --stocks 小量試，留空=全市場。
docker run --rm -v "${Data}:/app/data" twquant:latest backfill-fubon-chip --from 2026-07-08 --to 2026-07-08 --stocks 2330,2317,2454 --no-skip-existing --yes
# 全市場（慢，~1hr/日）：
# docker run --rm -v "${Data}:/app/data" twquant:latest backfill-fubon-chip --from 2026-07-08 --to 2026-07-08 --no-skip-existing --yes

# ── 【同步到 PostgreSQL 供應層】需 pg 容器 + twquant_default 網路 + DSN ──────

# 9) 鏡射全部現成表（prices/法人/融資券/營收/集保/meta/broker）→ PG serving schema
docker run --rm --network twquant_default -e "TWQUANT_PG_DSN=$DSN" -v "${Data}:/app/data" twquant:latest serving sync

# 10) 各表同步狀態（列數 / 最新日）
docker run --rm --network twquant_default -e "TWQUANT_PG_DSN=$DSN" -v "${Data}:/app/data" twquant:latest serving status

# ── 【物化因子 → PG】走本機（原生 IO，比 docker bind-mount 快約 10 倍）─────────
# 在 worktree 目錄跑；連 docker pg 的對外埠 5433。
cd D:\project\twquant\.claude\worktrees\stock-factor-dashboard-prd-bf0024
$env:TWQUANT_PG_DSN = 'postgresql://twquant:twquant@localhost:5433/twquant'

# 11) factor_values / factor_cross_stats / composite_daily（全宇宙因子逐日物化）
uv run --group serving python scripts/backfill_factors.py D:/project/twquant/data                       # 全歷史（~2.5h）
# uv run --group serving python scripts/backfill_factors.py D:/project/twquant/data 2026-01-01 2026-07-08  # 指定區間
