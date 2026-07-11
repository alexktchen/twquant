# PRD-001 — 個股頁改版：看盤式個股資訊 + PostgreSQL 服務層

版本：1.0（2026-07-10）
狀態：待接受（接受後：TASKS.md 增 T-031~T-037、工程規格另立 SPEC-009-serving、
SPEC-000 配置白名單登記 `configs/serving.yaml`、SPEC-007 §5 加指向 SPEC-009 的掛點）
關聯：SPEC-007 §5（儀表板）、D-016/D-018（唯讀原則）、D-019（富邦DJ 券商來源）、
T-016（主力分點因子，todo）、STRATEGY.md §3（分點因子規格）、ARCHITECTURE §6（儲存逃生門）

---

## 0. 摘要與目標

把「個股因子」分頁升級為**看盤軟體等級的個股頁**：一頁看完 K 線、三大法人買賣超、
融資券、券商買賣超（分點）、集保股權分散、月營收，加上本系統獨有的因子計分卡與
因子歷史軌跡。同時以 **PostgreSQL 服務層**解決現有查詢效能問題（使用者裁決，
2026-07-10）。

- **使用者**：單一操作者（系統擁有者本人），盤後使用，唯讀。
- **核心價值**：「盤後 5 分鐘看懂一檔股票的籌碼 + 基本面 + 因子狀態」。
- **效能目標**：整頁首屏 < 1.5s；單一 API p95 < 200ms
  （現況：個股因子卡每次請求 10 秒級）。

## 1. 背景與問題

現有個股體驗（`frontend/src/App.tsx` 的 StockFactors 分頁）只有 K 線 + 因子體檢卡，
且有三個結構性痛點：

1. **全宇宙重算**：`GET /api/stocks/{sid}/factors` 無快取，每次請求重新計算
   全部 21 個因子 × 全市場，只為顯示一檔的分數。橫斷面量（z/pct）本質需要
   全宇宙，但應該「每日算一次、落地、查表」，而非「每請求現算」。
   架構中本就規劃 `data/feature/` 因子落地層，目前 API 未使用。
2. **分區方向與查詢方向垂直**：資料湖按 `date=YYYY-MM-DD` 分區，查「一檔股票
   的兩年歷史」= 掃描全部分區（prices_daily 現有 1,524 個 parquet 檔），每檔
   只取一列；index_daily 更有 2,715 個小檔共 11MB，開檔成本遠大於讀取成本。
3. **個股頁資訊貧乏**：法人、融資券、營收資料湖裡都有，但個股頁看不到；
   看盤軟體標配的籌碼面板全數缺席。

儲存決策：**遷移 PostgreSQL 作為儀表板查詢服務層**（使用者裁決）。
ARCHITECTURE §6 預留「抽象層允許之後換 PostgreSQL 不動上層」的逃生門，本 PRD
即該逃生門的行使——但**只行使在服務層**：Parquet 湖仍是唯一真相源與 PIT 依據，
PG 是可隨時全量重建的衍生讀取層（詳 §6）。

## 2. 使用情境

| 劇本 | 流程 | 主要面板 |
|---|---|---|
| 每日收盤掃持股 | 逐檔輸入持股代號 → 看當日法人動向、融資券變化、因子傾向有無轉向 | Header、P2、P3、P7 |
| 研究候選股 | 從因子健檢/回測分頁看到有趣標的 → 跳轉個股頁 → 全面檢視籌碼與基本面 | 全部 |
| 驗證因子直覺 | 「這檔投信連買，因子怎麼說？」→ 展開單因子 250 日軌跡 vs 股價疊圖 | P7、P8 |

## 3. 個股頁資訊架構

單一可捲動長頁（取代現有「個股因子」分頁內容，分頁名改「個股」），sticky 頁首 +
面板錨點導覽列。選股器沿用現有元件（datalist 全市場搜尋 + 熱門 chips +
前 150 液態股下拉）。配色沿用台股慣例：紅漲綠跌。

```
┌─ Header（sticky）────────────────────────────────────────────┐
│ 2330 台積電 · 半導體 · 上市    1,085 ▲15 (+1.40%)  成交 32,415張 │
│ 綜合因子分位 87 ▮▮▮▮▮▮▮▮▯▯   52週位置 92%   資料截至 2026-07-09 │
├─ P1 K線主圖（全寬）──────────────────────────────────────────┤
│ 日K + MA5/20/60 + 成交量副圖（lightweight-charts，沿用 RangeCandle）│
├─ P2 法人買賣超 ────────────┬─ P3 融資券 ─────────────────────┤
│ 外資/投信/自營 堆疊長條      │ 融資餘額(左軸) 融券餘額(右軸)     │
│ + 外資/投信 累計淨買超折線    │ + 券資比、日增減長條             │
├─ P4 月營收 ────────────────┬─ P5 集保股權分散【Phase 2】──────┤
│ 36個月營收長條 + YoY 折線    │ >400張大戶持股比折線 + 總股東人數  │
├─ P6 券商買賣超【Phase 2】────────────────────────────────────┤
│ 窗口切換 1d/5d/20d：買超前15券商表 | 賣超前15券商表            │
│ + 買賣家數差折線（T-016 落地後加集中度折線）                    │
├─ P7 因子計分卡 + 歷史 ───────────────────────────────────────┤
│ 21因子分類卡（沿用現有 ScorecardPanel 形狀）每列加 250日 z-sparkline │
│ 點擊因子 → 展開該因子 z/pct 時間序列與股價疊圖                  │
├─ P8 綜合分位趨勢 ────────────────────────────────────────────┤
│ composite pct 250日折線 + 股價淡色疊圖                        │
└──────────────────────────────────────────────────────────────┘
```

### 面板 → 資料 → 端點 → 計算方式對照

| 面板 | 資料表（PG 服務層） | 端點（皆 GET） | 計算 | Phase |
|---|---|---|---|---|
| Header | stock_meta、prices_daily、composite_daily | `/api/stocks/{sid}/overview` | PG 單列 lookup | 1 |
| P1 K線 | prices_daily | `/api/stocks/{sid}/ohlc?days=500` | PG range scan；MA 前端算 | 1 |
| P2 法人 | institutional_daily | `/api/stocks/{sid}/institutional?days=120` | PG scan；累計淨買超伺服器端算（窗口起點歸零） | 1 |
| P3 融資券 | margin_daily | `/api/stocks/{sid}/margin?days=120` | PG scan；券資比、日增減伺服器端算 | 1 |
| P4 月營收 | monthly_revenue | `/api/stocks/{sid}/revenue?months=36` | PG scan；MoM 伺服器端算 | 1 |
| P5 集保 | shareholding_dispersion | `/api/stocks/{sid}/dispersion?weeks=52` | PG scan；≥400 張級距聚合伺服器端算 | 2 |
| P6 券商 | broker_top_daily、broker_summary_daily（夜間預聚合） | `/api/stocks/{sid}/brokers?days=5` | 夜間預算每日 top15 買/賣 + 家數差；窗口聚合查詢時做 | 2 |
| P7 計分卡 | factor_values（最新日） | `/api/stocks/{sid}/factors`（payload 形狀不變） | **夜間預算**，查詢僅 lookup | 1 |
| P7 展開 | factor_values | `/api/stocks/{sid}/factors/history?factors=a,b&days=250` | 夜間預算，查詢 range scan | 1 |
| P8 綜合趨勢 | composite_daily | `/api/stocks/{sid}/composite-history?days=250` | 夜間預算 | 1 |

- **ETF / 非因子宇宙股票**（因子宇宙 = 四碼普通股）：P1–P5 照常渲染，
  P7/P8 顯示「非因子宇宙」空狀態，不報錯。
- **前端工程備註**：App.tsx 已 ~1,600 行，改版時一併把個股頁抽成
  `frontend/src/stock/` 元件目錄（每面板一元件），不動其他分頁（T-034）。

## 4. 資料需求：現有 vs 待補

以 2026-07-10 資料湖實測為準（`D:/project/twquant/data/clean/`）：

| 資料表 | 面板 | 現況 | 待補動作 | Phase |
|---|---|---|---|---|
| prices_daily | P1、Header | ✅ 上市+上櫃全市場，~1 年 | — | 1 |
| stock_meta | Header | ✅ 2,010 檔；name 100%、industry ~99% | — | 1 |
| institutional_daily | P2 | ✅ 2025-07-01~2026-07-08、146 交易日、1,381 檔（**僅上市 TWSE T86**） | 上櫃法人走 FinMind（T-010） | 1（上櫃缺口標註於 UI） |
| margin_daily | P3 | ✅ 同期間（僅上市 MI_MARGN） | 上櫃融資券走 FinMind（T-010） | 1（同上） |
| monthly_revenue | P4 | ✅ 上市+上櫃，24 個月分區 | — | 1 |
| index_daily | （resid_mom β 因子輸入） | ✅ | — | 1 |
| shareholding_dispersion | P5 | ⚠️ 僅 1 週快照（2026-07-03，3,350 檔，**實測含 2,952 檔四碼普通股**——程式註解「僅 6 碼」已過時） | 週頻 ingest 排入排程，歷史往前累積；TDCC opendata 無歷史回補 | 2 |
| broker_trades | P6 | ⚠️ PoC：1 日 × 4 檔 × 119 列。富邦DJ 來源（D-019）為**母公司層級** top-15 買賣超：`broker_id` = 券商中文名（無分點代號）、`avg_price` 恆 null | 全市場每日抓取 + 回補（禮貌限流）；真分點需新來源（見 §12-2） | 2 |
| inst_holding | Header 外資持股比 | ❌ schema only，無轉接器 | T-010 | 3 |
| corporate_events / disposition_stocks / taifex_institutional | （非本 PRD 範圍） | ❌ 未接 | — | — |

`available_at` 時點遵循 SPEC-001 §1（如 broker_trades = 次日 08:00），
物化與展示的時點語義見 §6.6。

## 5. API 設計

全部 GET、唯讀，維持 D-016/D-018 原則（僅既有 ingest-jobs 白名單可 POST）。
`sid` 驗證沿用現行 `[0-9A-Z]{2,6}` 白名單（防注入）。

**回應外殼**（所有新端點統一）：

```jsonc
{ "sid": "2330", "as_of": "2026-07-09", "source": "pg",  // "pg" | "duckdb"（降級觀測）
  "rows": [ ... ] }
```

各端點 `rows` 形狀（日期 `YYYY-MM-DD`、量一律「股」、金額 TWD、比率小數——
遵循 SPEC-000；前端自行換算張/億顯示）：

```jsonc
// GET /api/stocks/{sid}/overview   （單物件，非 rows）
{ "sid":"2330","name":"台積電","industry":"半導體","market":"TWSE",
  "close":1085.0,"chg":15.0,"chg_pct":0.0140,"volume":32415000,
  "pos_52w":0.92,                    // (close-低52w)/(高52w-低52w)
  "composite":{"signed_z_mean":1.21,"pct":0.87,"n_universe":2183} | null }

// GET /api/stocks/{sid}/institutional?days=120   rows[]
{ "date":"2026-07-09","foreign_net":1234000,"trust_net":-56000,"dealer_net":8000,
  "cum_foreign":45678000,"cum_trust":-1200000 }   // 累計自窗口起點歸零

// GET /api/stocks/{sid}/margin?days=120   rows[]
{ "date":"2026-07-09","margin_balance":123400000,"margin_chg":560000,
  "short_balance":4560000,"short_chg":-12000,"short_margin_ratio":0.0370 }

// GET /api/stocks/{sid}/revenue?months=36   rows[]
{ "month":"2026-06","revenue":263710000000.0,"yoy":0.268,"mom":-0.041 }

// GET /api/stocks/{sid}/dispersion?weeks=52   rows[]
{ "date":"2026-07-03","holders_total":1523000,
  "big400_ratio":0.712,"big1000_ratio":0.583 }   // 級距≥400張/≥1000張持股比

// GET /api/stocks/{sid}/brokers?days=5   （窗口聚合；如實反映母公司層級）
{ "window_days":5,
  "top_buy":[{"broker":"凱基","net_shares":2100000,
              "buy_shares":2500000,"sell_shares":400000}],   // 無 avg_price（來源不提供）
  "top_sell":[ ... ],
  "summary":[{"date":"2026-07-09","buy_brokers":18,"sell_brokers":22,
              "broker_count_diff":-4}] }

// GET /api/stocks/{sid}/factors   —— 沿用現有 Scorecard payload 形狀（向後相容），
//                                    僅資料來源從「現算」改「查 factor_values」

// GET /api/stocks/{sid}/factors/history?factors=chip.trust_net_10d,price.mom_20d&days=250
// rows[] 每因子一組
{ "factor":"chip.trust_net_10d",
  "series":[{"date":"2026-07-09","value":1200000.0,"z":1.85,"pct":0.963}] }

// GET /api/stocks/{sid}/composite-history?days=250   rows[]
{ "date":"2026-07-09","signed_z_mean":1.21,"pct":0.87 }
```

**相容性**：舊 `GET /api/stocks/{sid}`（OHLC）保留為 `/ohlc` 的別名一版，
前端切換完成後退役（記入 CHANGELOG）。`GET /api/stocks/{sid}/factors` payload
形狀不變（`test_ac4` 鎖定）。

## 6. PostgreSQL 服務層設計（SPEC-009 主體，此處為 PRD 級摘要）

**定位聲明（一句話）**：PG 是 Parquet 湖的**衍生讀取快取**——任何時刻可
`twquant serving rebuild` 全量重建；研究/回測/因子計算**禁止**讀 PG。
此禁令以 IO 邊界靜態測試強制：`factors/ strategy/ backtest/ portfolio/`
不得 import serving 模組或 psycopg（CLAUDE.md §4 既有機制的擴充）。

### 6.1 鏡射表（PG schema `serving`）

| PG 表 | 來源 | 主鍵 | 附加索引 | 估計量 |
|---|---|---|---|---|
| prices_daily | parquet 同名 | (stock_id, date) | (date) | ~1.7M 列/年 |
| institutional_daily | 同名 | (stock_id, date) | — | 小 |
| margin_daily | 同名 | (stock_id, date) | — | 小 |
| monthly_revenue | 同名 | (stock_id, month) | — | 極小 |
| shareholding_dispersion | 同名 | (stock_id, date, level) | — | 小（週頻） |
| stock_meta | 同名 | (stock_id) | — | 極小 |
| broker_top_daily | broker_trades 預聚合（每日 top15 買/賣） | (stock_id, date, side, rank) | — | ~30 列/股/日 |
| broker_summary_daily | broker_trades 預聚合（家數統計） | (stock_id, date) | — | 小 |
| factor_values | 夜間物化（PitView→compute→standardize），**value/z/pct 三欄** | (factor, stock_id, date) | (factor, date) | 21×~2,200×500d ≈ 23M 列（含索引 ~3GB） |
| factor_cross_stats | 同上（mean/std/n_universe） | (factor, date) | — | 極小 |
| composite_daily | 同上（signed_z_mean/pct） | (stock_id, date) | — | ~1M 列 |
| sync_state | 同步簿記（last_date/last_synced_at/row_count） | (table_name) | — | — |

設計決定：
- **原始 broker_trades 不整表進 PG**——面板只需 top-N 與家數統計，預聚合即可。
- factor_values 寫入時 z/pct 已定（橫斷面一天算一次），讀取零計算。
- 歷史全保留，`factor_history_days` 配置保留窗（預設 1100 ≈ 3 年）供未來裁剪。

### 6.2 同步機制

- **增量（每日）**：每日 pipeline（LOOP §3）新增最終波 `serving_sync`：
  1. 對每張鏡射表以 `sync_state` watermark 找出新 `date=` 分區，
     polars 讀 parquet → `COPY` 進 staging 表 → `INSERT … ON CONFLICT DO UPDATE`
     合併（冪等，重跑安全）；
  2. `materialize_factors(as_of=今日)`：經 **PitView** 對全宇宙計算 21 因子 +
     standardize + composite，**一天只算一次**（成本 ≈ 現況一次 scorecard 請求），
     寫入 factor_values / factor_cross_stats / composite_daily。
- **次日晨 catch-up**：`available_at` 晚到的資料（如 broker_trades 次日 08:00）
  於次日 pipeline 前置波補同步。
- **全量**：`twquant serving rebuild [--tables t1,t2] [--from --to]`：
  truncate 後依區間重灌；因子歷史回補**逐日推進 as_of** 呼叫 materialize，
  維持 PIT 語義（不得一次性用最新資料回填歷史值）。
- **資料修訂**（來源重述）：upsert 直接覆寫 → 展示永遠反映湖中最新。
  PRD 明載：**服務層是展示層，不是 PIT 重播工具**（重播一律回 PitView）。

### 6.3 程式碼位置（尊重 IO 邊界）

- `src/twquant/data/serving/`：`schema.py`（DDL）、`sync.py`（parquet→PG upsert）、
  `materialize.py`（呼叫 `factors.compute`——因子函數仍純，IO 全在 data 層）。
  data/ 是允許 IO 的白名單層。
- `src/twquant/monitoring/pg_store.py`：API 讀取 repository（psycopg3 +
  connection pool，不用 ORM）。
- CLI：`twquant serving sync / rebuild / status`。

### 6.4 配置與部署

- 新 `configs/serving.yaml`（pydantic、`extra="forbid"`；**需在 SPEC-000 配置
  白名單登記**）：`enabled`、`dsn_env: TWQUANT_PG_DSN`（DSN 本體放 `.env`）、
  `pool_min/max`、`factor_history_days: 1100`、`fallback_duckdb: true`。
- docker-compose 新增 `pg` 服務：`postgres:16-alpine`、**named volume**
  `pgdata`（勿 bind mount——Windows/Docker Desktop I/O 慢）、host port 5433、
  `pg_isready` healthcheck；`api`/`scheduler` 掛 `TWQUANT_PG_DSN`；
  `api` 對 pg 為**軟依賴**（有降級，不設 hard depends_on 條件）。
- **PG 不做備份**——可重建即災難復原（`serving rebuild` < 30 分鐘，§8）。

### 6.5 降級（fallback）

API 啟動時與每 60s 快取一次 PG 健康旗標；PG 不可用時，端點走現行
DuckDB/snapshot 路徑（scorecard 退回現算，慢但可用），回應標 `source:"duckdb"`；
資料後台頁顯示 serving 健康狀態列。

### 6.6 PIT 正確性聲明（寫進 SPEC-009 開頭）

因子計算上游的唯一讀路徑仍是 PitView（黃金規則 1 不變）；PG 只承接
「已依 as_of 算好」的輸出；純運算層以靜態測試禁止觸碰 serving；
PG 內容僅供展示，**永不作為因子/回測輸入**。Header 顯示「資料截至」徽章
（= factor_values 最新物化日），使用者可辨識晚到資料尚未補齊的狀態。

## 7. 因子展示設計

- **計分卡**（P7 上半）：payload 形狀沿用現有 Scorecard（factor/zh/category/
  value/z/pct/direction/signed_z + composite），資料源改查 factor_values 最新日。
- **sparkline**（P7 每列）：250 日 z 序列迷你圖，一眼看出因子傾向是新發生
  還是長期狀態。
- **展開視圖**：點擊因子 → 該因子 z/pct 時間序列與股價疊圖（雙軸）。
- **綜合分位趨勢**（P8）：composite pct 250 日折線 + 股價淡色疊圖。
- **原則**：橫斷面情境（z/pct/n_universe）由夜間 pipeline 對全宇宙一次算完寫入；
  前端與 API **永遠只做單股查詢**，任何請求路徑上不得出現全宇宙計算。

現有 21 因子清單（顯示分組依 category）：

| category | 因子（direction） |
|---|---|
| price（10） | mom_1d(+)、mom_20d(+)、mom_60d(+)、high_52w(+)、reversal_5d(−)、vol_20d(−)、resid_mom_20d(+)、overnight_ret_20d(−)、intraday_ret_20d(+)、residual_reversal_5d(−) |
| volume（2） | vol_ratio_5d(+)、turnover_z_20d(+) |
| chip（7） | trust_net_10d(+)、foreign_net_10d(+)、trust_streak(+)、foreign_streak(+)、inst_net_10d(+)、margin_velocity_5d(−)、short_margin_ratio(+) |
| fundamental（2） | revenue_yoy(+)、revenue_accel(+) |

T-016 的 `chip.concentration_*` 系列落地後自動併入（factor_values 按註冊表物化，
新因子零改動接入——插件式擴充原則）。

## 8. 效能目標（可驗收數字）

| 項目 | 目標 | 現況 |
|---|---|---|
| `GET /api/stocks/{sid}/factors` | p95 < 150ms | ~10s+（全宇宙現算） |
| 各時間序列端點 | p95 < 200ms | 不存在 / 掃全分區 |
| 個股頁首屏（Header+P1+P7） | < 1.5s | — |
| 夜間 serving_sync（含物化當日因子） | < 5 分鐘 | — |
| `serving rebuild` 全量 | < 30 分鐘 | — |
| PG 停機 | 降級 DuckDB 仍可出頁（可慢） | — |

## 9. 分期與任務拆解（建議；PRD 接受後入 TASKS.md）

### Phase 1 — 現有資料全上（K線/法人/融資券/營收/因子歷史 + PG 底座）

| ID | 任務 | 依賴 | 模型 |
|---|---|---|---|
| T-031 | PG 服務層底座：SPEC-009、docker `pg` 服務、configs/serving.yaml、`data/serving/`（schema/sync/rebuild CLI）、鏡射六張現成表、sync_state 冪等 | T-011 | A |
| T-032 | 因子物化：materialize.py、factor_values/factor_cross_stats/composite_daily、夜間 pipeline 掛點、歷史逐日回補、與 PitView 現算抽樣一致性測試 | T-031, T-012 | A |
| T-033 | 個股 API v2：overview/ohlc/institutional/margin/revenue/factors-history/composite-history 端點 + scorecard 改讀 PG + DuckDB 降級 + source 欄 | T-031, T-032 | B |
| T-034 | 前端個股頁改版：P1–P4、P7、P8；App.tsx 抽出 frontend/src/stock/ 元件目錄 | T-033 | B |

### Phase 2 — 籌碼補全

| ID | 任務 | 依賴 | 模型 |
|---|---|---|---|
| T-035 | broker_trades 全市場每日抓取＋回補（富邦DJ、禮貌限流）＋ broker_top_daily/broker_summary_daily 預聚合 ＋ `/brokers` 端點 ＋ P6 面板；集保週頻 ingest 排程 ＋ `/dispersion` 端點 ＋ P5 面板 | D-019, T-031 | B |

### Phase 3 — 深化

| ID | 任務 | 依賴 | 模型 |
|---|---|---|---|
| T-036 | 主力分點因子上板：T-016 的 concentration_* 落地後併入 factor_values/計分卡/P6 集中度折線 | T-016, T-032, T-035 | B |
| T-037 | inst_holding ingest（T-010 FinMind）＋ Header 外資持股比 ＋ 上櫃法人/融資券補源 ＋ TDCC 互動站備援來源評估 | T-010, T-031 | B |

**硬性資料前置期**：T-036 依賴 broker_trades ≥ 60 交易日歷史（隔日沖分點
rolling 標籤，STRATEGY §3.1），即 T-035 上線後約 3 個月才能啟動。

## 10. 驗收條件

- AC-1（rebuild 冪等）：`serving rebuild` 跑兩次，PG 各表列數與內容一致；
  中斷後重跑可完成且無重複列
- AC-2（增量正確）：注入新一日 parquet 分區 → `serving sync` 只同步該日；
  重跑 sync 列數不變（watermark + upsert 冪等）
- AC-3（物化一致性）：factor_values 抽樣（≥3 因子 × ≥5 股 × ≥3 日）與
  PitView 現算值一致，容差 1e-9；歷史回補的 as_of 逐日推進（以 fixture 驗
  月營收公布日前後值不同）
- AC-4（相容）：`GET /api/stocks/{sid}/factors` payload 形狀與現行完全一致
  （golden 比對）
- AC-5（降級）：PG 停機時各端點回 200 且 `source:"duckdb"`；PG 恢復後自動切回
- AC-6（面板渲染）：Phase 1 範圍（Header 與 P1/P2/P3/P4/P7/P8 六面板）以
  fixture 資料離線渲染（沿用 SPEC-007 AC-6 的離線啟動機制）
- AC-7（空狀態）：ETF（如 0050）個股頁 P1 照常、P7/P8 顯示「非因子宇宙」，
  無 500 錯誤
- AC-8（閘門）：`python -m twquant check` 全綠；IO 邊界靜態測試涵蓋
  serving 禁令（純運算層 import psycopg/serving → 測試紅）

## 11. 非目標

- 盤中即時報價、tick 級資料（SPEC-007 既有範圍外）
- 下單、任何寫入操作（唯讀原則 D-016 不變；ingest-jobs 白名單 D-018 除外）
- 多使用者、權限、行動版排版
- 研究/回測層讀 PG（研究一律 PitView/DuckDB）
- corporate_events 事件標記於 K 線（除權息旗標）——延後評估
- PG 高可用/備份（可重建即復原）

## 12. 風險與開放問題

1. **富邦DJ 免費源品質/覆蓋**：無官方 SLA、歷史回補深度未知（天花板待實測）、
   券商名正規化。緩解：品質閘門加「分點買賣總量 vs 成交量比值」檢查
   （SPEC-001 §5-5 已定義 [0, 2.2]）；P6 標「參考級」資料徽章。
2. **「分點」實為母公司層級**：D-019 來源無分店明細，STRATEGY §3.1 的
   「總公司帳戶排除」清洗步驟無法對映到此資料；集中度因子精度受限
   （母公司彙總會稀釋地緣分點訊號）。真分點需新來源——列 DECISIONS 待裁決
   （選項：付費 FinMind 分點 / 其他免費源評估）。
3. **TDCC opendata 無歷史**：集保分散只能從接上排程那天往前累積；
   P5 上線初期只有數週資料點。TDCC 互動站（有驗證碼風險）僅列 Phase 3 評估，
   依 SPEC-001 範圍外原則不自動化驗證碼站點。
4. **factor_values 體積**：宇宙 ~2,200 檔 × 21 因子 × 500 日 ≈ 23M 列（~3GB
   含索引）單機可負擔；因子數/宇宙擴張時靠 `factor_history_days` 裁剪。
   開放：已下市股是否保留（影響歷史橫斷面 pct 的可重現性）——v1 保留。
5. **物化時點 vs 晚到資料**：夜間物化 as_of=D 時，`available_at` 次日 08:00
   的表（broker_trades）尚不可見 → 次日晨 catch-up 補跑；Header 顯示
   「資料截至」徽章讓使用者可辨識。
6. **上櫃法人/融資券缺口**：institutional_daily/margin_daily 現僅上市
   （T86/MI_MARGN）；上櫃個股的 P2/P3 會空白，UI 需顯示「上櫃法人資料待接
   （T-010）」而非無聲空圖（黃金規則 5 的 UI 對應）。
7. **composite 定義**：v1 維持等權 signed_z 平均；「策略視角」加權分數
   （用 T-017 combine 權重）列開放問題。
8. **Windows Docker PG**：named volume 效能可接受；bind mount 明確禁止
   （效能陷阱寫進 SPEC-009 部署節）。
