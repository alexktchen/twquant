# SPEC-009 — 供應層（PostgreSQL 讀取層 + 個股 API）

範圍：把儀表板查詢從「Parquet/DuckDB 每請求現算」改為 PostgreSQL 衍生讀取層；
個股頁所需的時間序列端點與因子物化。產品需求與版面見 [PRD-001](../PRD-001-stock-page.md)
（PRD 為 why/版面權威，本 SPEC 為介面/AC 權威）。關聯：SPEC-007 §5、D-016/D-018
（唯讀）、D-019（富邦DJ 券商來源）、ARCHITECTURE §6（儲存逃生門）。

## 0. 定位與不變量（黃金規則保全）

- PG 是**衍生、可重建、僅供展示**的讀取快取；Parquet 湖為唯一真相源與 PIT 基準。
- 因子計算的唯一讀路徑仍是 `PitView`（黃金規則 1）。PG 只存**已算好**的值，
  **永不**作為因子/回測/策略/投組的輸入。
- **IO 邊界擴充（CLAUDE.md §4 靜態閘門）**：`factors/ strategy/ backtest/ portfolio/
  insight/` 不得 `import psycopg`，亦不得 import `twquant.data.serving.*` 或
  `twquant.monitoring.pg_store`。此把「PG 不回饋純運算層」升級為機器可攔。
- serving 程式碼落點：`src/twquant/data/serving/`（允許 IO 的資料層：schema/sync/
  materialize）+ `src/twquant/monitoring/pg_store.py`（API 讀取 repository）。

## 1. `configs/serving.yaml`（權威欄位清單，pydantic `extra="forbid"`）

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `enabled` | bool | `false` | 總開關；`false` 時 API 一律走 DuckDB 路徑 |
| `dsn_env` | str | `TWQUANT_PG_DSN` | 存 DSN 的環境變數**名稱**；DSN 本體只在 `.env` |
| `pg_schema` | str | `serving` | PG schema 名（避開 pydantic `BaseModel.schema` 遮蔽） |
| `pool_min` | int | `1` | 連線池下限 |
| `pool_max` | int | `8` | 連線池上限 |
| `factor_history_days` | int | `1100` | factor_values 保留窗（≈3 年）；超出裁剪 |
| `fallback_duckdb` | bool | `true` | PG 不可用時是否降級 DuckDB（false 則回 503） |

秘密規則：`configs/serving.yaml` 只存 env 變數名，DSN 本體（含帳密）僅在 `.env`
（黃金規則 9）。`SystemConfig` 之外新增 `ServingConfig(BaseConfig)`（`config.py`）。

## 2. PG schema 與資料表（schema `serving`）

DDL 由 `data/serving/schema.py` 建立（`CREATE TABLE IF NOT EXISTS` +
`CREATE INDEX IF NOT EXISTS`，冪等）。型別對應：polars `Date→date`、`String→text`、
`Int64→bigint`、`Float64→double precision`、tz-aware `Datetime→timestamptz`
（值以 Asia/Taipei 存）。stock_id 一律 `text`（保前導零）。

### 2.1 鏡射表（欄位比照 `data/schemas.py`，含 `available_at`）
`prices_daily`、`institutional_daily`、`margin_daily`、`monthly_revenue`、
`shareholding_dispersion`、`stock_meta`。主鍵：
- prices/institutional/margin：`(stock_id, date)`，附 index `(date)`
- monthly_revenue：`(stock_id, year_month)`
- shareholding_dispersion：`(stock_id, date, level)`
- stock_meta：`(stock_id)`

### 2.2 券商買賣超（T-035，直接鏡射，實作簡化）
- `broker_trades` 直接列入 §2.1 鏡射表（PK `(stock_id, date, broker_id)`，附 `(date)` 索引）。
  富邦DJ 已是每檔每日 top-15±（~30 列/檔/日、量小），故**不另做 broker_top/summary_daily
  預聚合表**；`/brokers` 端點於查詢時對近 window 日彙總各券商淨買賣超、取前 15 買/賣 + 每日
  家數統計。`broker_id` 存券商**中文名**（D-019 母公司層級，無分點代號、`avg_price=null`）。
  （原設計的預聚合表若日後資料量成長再引入。）

### 2.3 因子物化表
- `factor_values(factor text, stock_id text, date date, value double precision,
  z double precision, pct double precision)`，PK `(factor, stock_id, date)`，
  附 index `(factor, date)`。`z/pct` 為橫斷面量（寫入時已定）。
- `factor_cross_stats(factor text, date date, mean double precision,
  std double precision, n_universe int)`，PK `(factor, date)`。
- `composite_daily(stock_id text, date date, signed_z_mean double precision,
  pct double precision)`，PK `(stock_id, date)`。

### 2.4 簿記表
- `sync_state(table_name text primary key, last_date date, last_synced_at timestamptz,
  row_count bigint)`。成敗真相仍以 `ingest_log`/資料湖為準，此表僅供 watermark 與觀測。

## 3. 同步（sync）與物化（materialize）

`data/serving/sync.py`、`data/serving/materialize.py`。CLI 掛既有 `python -m twquant`：
`serving sync` / `serving rebuild` / `serving status`。

- **`serving sync`（增量）**：對每張鏡射表以 `sync_state.last_date` watermark 找出
  更新的 `date=` 分區 → polars 讀 parquet → `COPY` 進 temp staging 表 →
  `INSERT … ON CONFLICT (pk) DO UPDATE`（冪等，重跑安全）→ 更新 `sync_state`。
  `broker_trades` 亦為鏡射表之一，同路徑同步（買/賣超彙總移至 `/brokers` 端點查詢時算）。
- **`materialize_factors(as_of)`**：`list_factors()` 全部因子 →
  `compute(view=Warehouse.view(as_of))` → `standardize`（winsorize_mad → zscore，
  per `(date, factor)`）→ `signed_z = z × direction`、composite（signed_z 均值 +
  宇宙百分位）→ upsert `factor_values`/`factor_cross_stats`/`composite_daily`。
  單日成本 ≈ 現行一次 scorecard 請求；一天只算一次。
- **`serving rebuild [--tables t1,t2] [--from --to]`**：truncate 指定表後依區間重灌；
  因子歷史**逐日推進 as_of** 呼叫 materialize，維持 PIT 語義
  （**禁止**用最新資料一次性回填歷史因子值）。
- **`serving status`**：印各表 `sync_state`（last_date / row_count / 落後交易日數）。
- **pipeline 掛點**：每日 pipeline（LOOP §3）尾波跑 `serving sync` + `materialize(今日)`；
  次日晨前置波做 catch-up（`available_at` 晚到者，如 broker_trades 次日 08:00）。

## 4. 個股 API（`monitoring/api.py` 擴充；`monitoring/pg_store.py` 讀）

全部 GET、唯讀（D-016/D-018 不變）。`sid` 白名單沿用 `[0-9A-Z]{2,6}`（防注入）。

- 端點與 payload 形狀以 **PRD-001 §5 為權威**：`overview`、`ohlc`、`institutional`、
  `margin`、`revenue`、`dispersion`、`brokers`、`factors`（相容現行 Scorecard）、
  `factors/history`、`composite-history`。
- **回應外殼**：`{ "sid", "as_of", "source": "pg", "rows": [...] }`（overview 為單物件不含 rows）。
- **只讀 PG（使用者裁決，2026-07-10）**：個股端點一律讀 PG，**不降級資料湖 DuckDB**——PG 有
  資料回列、無該資料回空列、連不上讓端點 503。移除原健康旗標/`fallback_duckdb` 降級路徑，避免
  「PG 與湖不一致」的混淆；衍生欄（累計/券資比/MoM/top 券商）抽成純函數單元測試。`source` 恆為
  `"pg"`。（`fallback_duckdb` 欄位保留於 config 但個股讀取路徑不再參考。）
- 舊 `GET /api/stocks/{sid}`（OHLC）保留為 `/ohlc` 別名一版，前端切換後退役
  （記 CHANGELOG）。`/api/stocks/{sid}/factors` payload 與現行 golden 完全相容。

## 5. 部署與依賴

- `pyproject.toml` 新增 dependency-group `serving`：`psycopg[binary]>=3.2`
  （視需要 `psycopg_pool`）。
- docker-compose 新增 `pg`：`postgres:16-alpine`、**named volume** `pgdata`
  （**禁 bind mount**——Windows/Docker Desktop I/O 陷阱）、host port `5433`、
  `pg_isready` healthcheck；`api`/`scheduler` 掛 `TWQUANT_PG_DSN` 環境變數；
  `api` 對 `pg` 為**軟依賴**（有降級，不設 hard `depends_on: condition`）。
- **PG 不做備份**——`serving rebuild` 即災難復原（目標 < 30 分鐘，PRD §8）。

## 6. 效能目標

見 PRD-001 §8。屬手動/負載驗收（非自動 AC）：scorecard p95 < 150ms、時序端點
p95 < 200ms、夜間 sync+物化 < 5 分、全量 rebuild < 30 分。

## 驗收條件

- AC-1（DDL 冪等）：`schema.py` 建表連跑兩次無錯；所有表與索引存在（查
  `information_schema`）。
- AC-2（sync 增量 + 冪等）：注入新一交易日 parquet 分區 → `serving sync` 只同步該日
  且 `sync_state.last_date` 前移；再跑一次 sync，該表 `row_count` 不變。
- AC-3（物化 PIT 一致）：抽樣 ≥3 因子 × ≥5 股 × ≥2 日，`factor_values.value` 與
  `PitView` 現算 `compute` 值一致（容差 1e-9）；對含月營收公布日的 fixture，
  `as_of=公布日-1` 與 `as_of=公布日` 物化的 `revenue_yoy` 不同（證明逐日推進、
  非最新回填）。
- AC-4（scorecard 相容）：`/api/stocks/{sid}/factors` 回應鍵集與型別與現行
  Scorecard golden 一致。
- AC-5（只讀 PG）：個股端點回應 `source=="pg"`；PG 有資料回列、無該股資料回空列、PG 連不上
  讓端點 503——不降級資料湖（以 derive 純函數單元測試 + PG 整合測試涵蓋）。
- AC-6（時序正確）：`derive_institutional`/`derive_margin`/`derive_revenue`/`derive_brokers`
  純函數：累計淨買超/日增減/券資比/MoM/top 券商 與手算一致（不需 PG）。
- AC-7（IO 邊界）：在 `factors/`（或其他純運算層）注入 `import psycopg` 或
  `import twquant.data.serving...` → IO 邊界靜態測試退出碼 1 並指出違規檔（含反向
  測試防恆真）。
- AC-8（閘門）：`python -m twquant check` 全綠；需要真 PG 的整合測試在無 DSN 時
  `skip`（不擋 CI），有 DSN 時執行。

## 範圍外

- 研究/回測/策略讀 PG（一律走 `PitView`/DuckDB）
- PG 高可用、備援、多節點、線上 schema migration 工具（重建即復原）
- 真分點（branch-level）券商資料與其清洗（母公司層級之外）——待 broker 來源決策
  （PRD §12-2；牽動 T-035/T-016）
- 盤中即時/tick 串流（SPEC-007 既有範圍外）
