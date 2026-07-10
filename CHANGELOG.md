# CHANGELOG

- 2026-07-10 官方開休市日曆（TWSE market_holidays，T-003/T-024）：到 TWSE OpenAPI 抓官方
  「市場開休市日期」。新增 `data/sources/twse_holiday.py`（TwseHolidaySource）、
  `clean_market_holidays`（民國封裝碼 "1150101"→2026-01-01；closed 分類：名稱含「開始/最後
  交易」為交易日註記，非休市——端點夾帶此類純交易日列，全當休市會誤刪真實交易日）、schemas
  `market_holidays`（參考表，_all 單分區、無 available_at）。`calendar.official_calendar` 由
  官方休市日建「前瞻」交易日曆（平日−休市，不需先有行情即知未來開市日）、`cross_check_official`
  官方 vs 觀測（prices_daily）缺口核對。CLI `ingest-holidays`；資料後台一鍵「抓官方開休市
  日曆」鈕 +「官方開休市日曆」面板（開市日數／近期休市／官方 vs 觀測），`GET /api/holiday-calendar`。
  閘門全綠（mypy strict、frontend oxlint+build）＋實抓驗證（2026：243 開市日、2026-01-02~12-31、
  休市 24／交易日註記 3、與觀測一致）。tests/test_holiday_calendar.py（14 測，錄製 HTTP fixture）。

- 2026-07-10 資料後台「手動抓取歷史資料」表單（T-024）：資料集（白名單）+ 期間 → 沿用
  既有 `POST /api/ingest-jobs`（伺服器端跑 twquant CLI、單一併發、輸出尾行輪詢）與補抓
  工作進度面板。補上「主動指定期間」入口——漏抓清單只在既有涵蓋範圍內偵測，初次建置或
  往前延伸歷史過去只能敲 CLI。台股行情為全市場擷取（單日含所有個股），抓完期間後於個股
  頁即可查任一檔歷史 K 線。新增 `GET /api/ingest-tracks`（`jobs.fetchable_tracks` 白名單，
  與一鍵補抓同一真相源；unit 決定日/月輸入粒度）。修正 `needs_force` 在空資料湖無交易日曆
  時 500（改以平日數估交易日，D-015 護欄對初次建置仍生效）。閘門全綠（214 tests、mypy
  strict、frontend oxlint+tsc+build）＋對空資料湖手驗端到端（抓 2026-07-08 上市 1361／
  上櫃 4826 列，job running→success，個股 2330 K 線即查、熱力圖轉綠）。

- 2026-07-07 擷取層可插拔化（D-017 / T-010）：新增 `data/ingest/`（ports：唯寫 IngestSink +
  自足 SourcePort；registry：SourceSpec/register/list_specs，比照 factors；orchestrator：
  run_ingest 只依賴埠、可用假 sink/來源測試）。`cli._ingest_date` 每日迴圈改由註冊表驅動；
  既有 twse/tpex 以 FetchCleanSource 零改寫、Warehouse 零改動即滿足 IngestSink。首個新來源
  `TdccDispersionSource` + `clean_shareholding_dispersion`（`ingest-dispersion`，週頻，
  available_at=公布週六12:00、16/17 剔除）——實測 opendata id=1-5 覆蓋 6 碼證券（ETF/債券，
  50250 列/3350 檔），四碼普通股集保分散待接互動站來源。讀取/PitView 側不動。閘門全綠（194 tests）。

- 2026-07-07 監控整併（D-016）：退役 Streamlit `monitoring/dashboard.py`，改「React 前端 +
  唯讀 FastAPI」。新增 `monitoring/snapshot.py`（快照建構單一真相源，API 與 export_snapshot
  共用）、`monitoring/api.py`（GET /api/health·snapshot·stocks·stocks/{sid}·backtest·
  ingest-status，重運算以最新行情日快取，個股代號白名單防 SQL 注入）、CLI `serve`。前端改
  即時 fetch `/api`、API 不可達退回內嵌 data.json；補回 Streamlit 專屬互動（前 150 檔個股
  選單、擷取失敗明細、回測期間選擇）。docker-compose `dashboard`→`api`（:8000），frontend
  nginx 反代 `/api`。pyproject `dashboard`(streamlit)→`api`(fastapi/uvicorn)。閘門全綠（168 tests）。

- 2026-07-06 前端圖表換 TradingView lightweight-charts：K線+成交量+十字游標legend+拖曳縮放（台股紅漲綠跌），權益改區域圖；水平長條維持 SVG。Docker 重打包驗證瀏覽器渲染正常。

- 2026-07-06 前端儀表板（frontend/）：React 18 + Vite 5 + Tailwind，交易終端風格、台股紅漲綠跌、自繪 K線/權益/長條圖（hover+範圍切換+個股選擇）。用 Docker 打包成自包含單檔（繞開 Windows toolchain）；scripts/export_snapshot.py 由資料湖產快照。

- 2026-07-06 T-029/T-024（部分）容量分析 + 漂移偵測：backtest/capacity.py
  （estimate_capacity + CLI capacity）、monitoring/drift.py（tracking_error/drift_check）。
- 2026-07-06 T-027/T-028 洞悉層功能：daily_brief/explainer/advisor + proposals 生命
  週期（CLI insight-brief/proposal validate·approve；LLM 只提案不執行）。

- 2026-07-06 T-021 執行層：backtest/fill.decide_fill（回測與 PaperBroker 共用成交
  原語）、execution/（PaperBroker idempotency/HALTED、preflight/reconcile/run_session）。
- 2026-07-06 T-023（部分）每日 pipeline：run_pipeline（步驟編排+degraded）、
  heartbeat/watchdog（dead-man switch）+ CLI pipeline/watchdog。

- 2026-07-06 儀表板改版：側邊選單分頁（總覽/市場K線/個股K線/法人/月營收/因子健檢/
  策略回測），價格圖改 Altair K 線（等權市場指數 + 個股 OHLC，漲綠跌紅）。閘門全綠。

- 2026-07-06 修回測跨程序非決定性（重要）：combine group_by/選股 top_n/引擎成交
  排序原本依賴 Python set 與 polars group_by 順序，不同程序（PYTHONHASHSEED）結果
  不同（Sharpe 曾 0.68~1.43 亂跳）。全數加明確 sort（次序鍵 stock_id）→ 真值
  Sharpe 1.03、3 程序一致。新增跨 PYTHONHASHSEED 子程序決定性測試。
- 2026-07-06 儀表板加「策略回測」面板：權益曲線 + 成本後 Sharpe/CAGR/MDD/勝率/
  周轉，日期可選、快取。閘門全綠（124 tests）。

- 2026-07-06 T-020（部分完成）投組層：portfolio/（config/core）+ twquant/state.py。
  sleeve 組合+歸因、個股/周轉限額、條件式波動目標、四態 kill-switch、狀態原子寫+
  fail-safe（缺失/損毀→HALTED）。configs/portfolio.yaml、risk.yaml。閘門全綠（123 tests）。
- 2026-07-06 T-019 驗證工具：walk-forward/DSR/bootstrap/param-plateau。

- 2026-07-06 T-018（部分完成）回測引擎：backtest/（costs/engine/metrics）+ CLI
  backtest + costs.yaml。next_open 執行、整數張、價格相依成本、漲跌停/參與率
  unfilled、會計恆等。真實回測 sample 策略連續期成本後 Sharpe 1.12、CAGR +8.6%、
  MDD -4.2%。閘門全綠（104 tests）。

- 2026-07-06 T-017（部分完成）策略框架：strategy/（config/universe/combine/
  overlays/build）+ CLI targets + sample_revenue_trust.yaml。因子→宇宙過濾→zscore
  組合→top_n→等權→寬度 overlay→單股上限，用 T-003 日曆排週頻。真實資料：sample
  策略 2026-06-30 產出 40 檔等權目標（總曝險 82%）。待接：ic_weighted、處置股排除、
  index overlay。閘門全綠（95 tests）。

- 2026-07-06 T-003 交易日曆：data/calendar.py（TradingCalendar + 由 prices_daily
  聯集推導 + 單檔持久化 + 來源核對）、CLI calendar。缺口偵測改以「開市日」為準、
  禁用 weekday()：TPEX 缺口 18→0（假日不再誤判）、TWSE 顯示 101 個真實 ban 缺口。
  閘門全綠（86 tests）。

- 2026-07-06 T-015 P0 籌碼 + 基本面因子：chip（投信/外資買超占量比、投信連買、
  融資變化散戶代理）、fundamental（月營收 YoY/加速，公布日 as-of 映射日頻 PEAD）、
  factors/builtins.py 一次註冊全因子。真實 IC + 對抗驗證（連續期 vs 全期）：
  revenue_yoy 最穩健（t≈+8），trust_net_10d 真實反向（t=-11，非缺口假象），
  trust_streak 全期數字有缺口灌水。閘門全綠（81 tests）。

- 2026-07-05 抓資料狀態介面：ingest_log（記錄每次擷取的成功/失敗/列數/細節，
  append-only 分片）；ingest/daily/backfill/ingest-revenue 全數記錄；`twquant status`
  CLI 顯示各來源涵蓋、平日缺口、最近失敗、最後執行；儀表板加「擷取狀態」面板。
  完全靠 CLI+排程器+儀表板運作，不需 LLM。實測 status 清楚顯示 TWSE 缺口 119 日。
- 2026-07-05 T-013 IC 檢驗 + research_log：factors/ic.py（RankIC/t-stat/多空價差）、
  reporting/research_log.py。真實驗證動能在台股 5 日顯著反轉、20 日無效。

- 2026-07-05 Docker infra（D-013）：Dockerfile（python-slim + uv）、docker-compose
  三服務（app / scheduler / dashboard）共用映像，DuckDB/Parquet 掛 volume。
  CLI 加 daily（容忍非交易日）與 scheduler（每日觸發）。Streamlit 儀表板 v1
  （讀倉儲顯示資料狀態與法人流向）。實測：容器讀共用 volume 資料、儀表板
  8501 服務、排程器正常。scheduler/dashboard 完整版見 T-023/T-024。

- 2026-07-05 T-011（部分）倉儲核心：清洗管線（TWSE raw→typed）、Parquet 分區
  資料湖（原子寫/分區級替換/PIT 20:00 截點）、DuckDB SQL 查詢引擎、CLI ingest/sql。
  **資料已可分析**：ingest 2026-06-30 存入三表共 3970 列，SQL 重現投信季底作帳。

- 2026-07-05 資料路徑 T-002→T-004→T-006→T-007：conventions（時區/例外/配置）、
  資料合約（14 表 pandera）、HTTP 基礎設施（滑動窗限流/重試/raw 落地）、TWSE rwd
  轉接器。**第一次實盤抓到台股資料**：2026-06-30 的日K/三大法人/融資券三張表，
  台積電收盤 2410、投信買賣超 -89,595 股等真實值已驗。全程閘門綠（35 tests）。

- 2026-07-05 T-001 專案骨架：pyproject(uv/hatchling)、src/twquant 佈局、
  `python -m twquant check` 開發閘門（ruff + format + mypy strict + pytest）、
  IO 邊界靜態測試、GitHub Actions CI。閘門正反雙向驗證通過。第一份程式碼。

- 2026-07-05 補跑完成（v1.2 規格修訂）：
  - 文獻驗證 76/76 全數完成（65 確認、11 細節修正、0 查無）。兩篇作者/年份
    張冠李戴已修正：「Momentum in Taiwan: seasonality matters!」為 Fu & Wood
    2010（非 Du/Huang/Liao）、Faber TAA 統計出自 2013 修訂版。
  - 規格對抗審查完成（3 視角 × 全文件）：23 high + 34 medium + 15 low 發現，
    經作者逐條核對後屬實。本輪修正 47 項（連動 DECISIONS D-004~D-012），
    11 項留待實作時設計選擇。完整記錄見 docs/research/REVIEW.md。
  - 主要修正：波動目標唯一歸屬（投組層條件式）、四態 kill-switch、PIT 20:00
    截點與多波次擷取、資料湖寫入協定（原子寫/版本化/並行）、漲跌停檔位精算、
    會計恆等式、投信持股比資料表、參與率分母統一、IC 兩階段門檻、文件優先權、
    portfolio_state 移出可重建層、build_targets 簽名統一、TASKS 依賴修正。
  - 完整性檢查 16 缺口納入 STRATEGY §5a：投信訊號 ETF 污染、結構斷點、
    Sleeve C 成本存活性、本地因子基準、待評估策略族（T-029/T-030）。

- 2026-07-05 新增 SPEC-008 洞悉層、MODEL-POLICY.md（模型分工）、docs/STRATEGY.md
 （策略設計書，整合 6 組研究代理 / 76 篇文獻）；LOOP.md 增為四閉環；
  TASKS.md 加模型分級欄與 T-026~T-028。文獻逐篇驗證與規格對抗審查因
  session 上限未完成，待補跑。
- 2026-07-04 規格階段：建立文件體系（CLAUDE.md、ARCHITECTURE.md、LOOP.md、
  SPEC-000~007、TASKS.md、DECISIONS.md）。尚無程式碼。
