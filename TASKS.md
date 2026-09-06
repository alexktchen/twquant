# TASKS — 實作任務佇列

> **最新交接**：[HANDOFF-2026-09-06-tracks-and-gates.md](docs/HANDOFF-2026-09-06-tracks-and-gates.md) — 進度、工作量、五個未解問題、產物清單。

規則見 CLAUDE.md §2；「模型」欄依 MODEL-POLICY.md §2（A=OPUS、B=SONNET、C=HAIKU）。
狀態：`todo | in_progress | done | blocked`。挑最小編號的、無未完成依賴的 todo。
完成時附上閘門輸出摘要。B/C 級任務合併前需 OPUS 級複審（見 MODEL-POLICY §2-2）。

**A 級任務測試先行（MODEL-POLICY §2-1，REVIEW H13）**：標「A」的任務拆成
`Txxx-t`（依 SPEC AC 撰寫並凍結測試，獨立 session）→ `Txxx`（實作通過凍結測試）；
Txxx 的 diff 不得包含 tests/ 變更（`git diff --stat` 檢查）。下表 A 級任務隱含此拆分。

| ID | 任務 | 規格 | 依賴 | 模型 | 狀態 |
|---|---|---|---|---|---|
| T-001 | 專案骨架：pyproject(uv)、src 佈局、`check` 閘門指令（含 IO 邊界靜態測試）、ruff/mypy/pytest、CI | CLAUDE.md §2/§4 | — | B | done |
| T-002 | conventions 模組：TZ、assert_tw_timestamp、例外樹、pydantic 配置載入器（全 config 清單見 SPEC-000） | SPEC-000 | T-001 | B | done |
| T-004 | 資料合約：schemas.py 全表 pandera 定義（含 inst_holding、limit_up/down_price、structural_breaks） | SPEC-001 §1 | T-002 | B | done |
| T-006 | http 基礎設施：session、滑動窗限流器、重試、raw 版本化落地 | SPEC-001 §2/§7 | T-002 | B | done |
| T-007 | 來源轉接器：twse_rwd（prices/T86/MI_MARGN） | SPEC-001 | T-006 | B | done |
| T-008 | 來源轉接器：tpex 上櫃行情（anti-bot headers）；法人/融資券改走 FinMind（D-014） | SPEC-001 | T-006 | B | done |
| T-009 | 來源轉接器：mops 月營收（done）；tdcc 股權分散、taifex 期貨（待補） | SPEC-001 | T-006 | B | 部分完成 |
| T-010 | 來源轉接器：finmind broker_trades、inst_holding | SPEC-001 | T-006 | B | todo |
| T-003 | 交易日曆：以人工核對靜態日曆檔為第一版（有 prices_daily 後改推導核對） | SPEC-000 AC-3 | T-002 | B | done |
| T-005 | golden fixtures：以 T-007~T-010 抓真實切片（30 檔 × 2 年，含除息/減資/漲停/處置/月營收/當沖案例）+ 一次性下載腳本（附來源 URL 與 hash）+ 凍結輸入層 | CLAUDE.md §4 | T-004, T-007, T-009 | B | todo |
| T-011 | 倉儲核心：Warehouse/PitView（20:00 截點）、寫入協定、DuckDB SQL、清洗管線；還原價/品質閘門/backfill 待補 | SPEC-001 §4/§7 | T-004 | A | 部分完成 |
| T-012 | 因子框架：@factor 註冊表（名稱格式驗證）、price.mom_20d、compute、standardize、assert_no_lookahead | SPEC-002 | T-011 | A | done |
| T-013 | IC 報告（純函數，落地在 reporting/）+ research_log schema；本地因子中性化待市值資料 | SPEC-002 §4 | T-012 | B | done |
| T-014 | P0/P1 價量因子 + 最小本地因子組（市場/SMB/HML/52週高/短反轉，作中性化基準） | STRATEGY §4/§5a | T-012 | B | 部分完成 |
| T-015 | P0 籌碼因子：投信買超（分主動/被動重驗 IC）、月營收動能（PEAD）、散戶密集度 | STRATEGY §4/§5a | T-012 | B | done |
| T-016 | 主力分點因子：清洗管線（總公司排除、隔日沖標籤、鉅額剔除）+ 集中度系列 | STRATEGY §3 | T-010, T-012 | B | in_progress（**程式已落地** `factors/broker.py`：三因子 + `daytrade_ratio` 屬性表；11 條測試先行全綠——集中度／家數差／持續性手算、窗不足無值、隔日沖比率 expanding 且記在賣出日、達門檻才排除且排除後值改變、門檻前 PIT 一致、三因子反前視。IC 階梯（PG，2022-02-01~2026-02-25）：**broker_concentration_20d RankIC −0.008／−0.011／−0.010（t −4.1／−6.0／−5.0），五分位價差 +0.22／+0.35／+0.65%**——|t| done（2026-09-03；程式 8331ba2／b198ebd，閘門 545 passed）。IC（全樣本≈OOS，宇宙 4 位數、h=5/10/20）：`broker_count_diff_5d` RankIC −0.014／−0.015／−0.017、t −11／−13／−16、五分位價差 −0.10／−0.15／−0.24%（**方向與先驗 +1 相反、IC 與價差同號**）；`broker_concentration_20d` −0.008／−0.011／−0.010、t −4～−6、價差 +0.22～+0.65%（U 型）；`broker_persistence_10d` −0.008／−0.007／−0.011、t −2.5～−3.7、價差 +0.17～+0.46%（弱、U 型）。結論：分點 top-15 買超偏散戶，三個因子都是反向；count_diff 以 −1 方向可過 D-006（t≥3）但屬規格變更、另立裁決；三個皆可進 LightGBM 候選，不得以 +1 進線性組合 |
| T-017 | 策略框架：配置、宇宙、combine、overlays（三態 regime 狀態機；**不做波動縮放**，D-004） | SPEC-003 + STRATEGY §2 | T-012 | B | 部分完成 |
| T-018 | 回測引擎：執行模型、價格相依成本、漲跌停旗標判定、會計恆等、指標 | SPEC-004 | T-017 | A | 部分完成 |
| T-019 | 驗證工具：walk-forward、deflated_sharpe(trial_sharpes)、plateau、bootstrap | SPEC-004 §5 | T-018 | B | done |
| T-020 | 投組層：sleeve 組合、**條件式波動目標**、限額、四態 kill-switch、data/state 持久化 | SPEC-005 | T-018 | A | 部分完成 |
| T-021 | PaperBroker（共用回測成交模組）+ 每日執行流程 + 對帳 | SPEC-006 | T-020 | A | done |
| T-022 | ShioajiBroker（dry_run 預設、雙開關） | SPEC-006 | T-021 | B | todo |
| T-023 | 每日 pipeline 多波次編排（ingest→gate→factor→signal）+ heartbeat/watchdog + 排程 | LOOP.md §3 | T-021 | A | 部分完成 |
| T-024 | 監控：日報、漂移偵測（tracking error）、告警、唯讀儀表板 | SPEC-007 | T-023 | 部分完成 |
| T-025 | Sleeve A（籌碼×營收動能）完整策略上模擬倉 | STRATEGY §2 + LOOP §1 | T-015, T-016, T-024 | B | todo |
| T-026 | 洞悉層基座：LLM client 封裝、configs/llm.yaml（含 pricing/匯率）、用量記錄與預算閘、prompt 回歸測試框架 | SPEC-008 §3 + MODEL-POLICY §4 | T-002 | B | done |
| T-027 | daily_brief + explainer（含離線降級） | SPEC-008 §2.1/2.3 | T-024, T-026 | done |
| T-028 | advisor + 提案生命週期（proposals/、validate/approve CLI、回饋迴路） | SPEC-008 §2.2/§4 | T-019, T-026 | 部分完成 |
| T-029 | 容量分析：各 sleeve 周轉 × ADV 分布 + 衝擊函數 → 容量上限，寫回 STRATEGY §2 | STRATEGY §5a | T-018 | 部分完成 |
| T-030 | 待評估策略族回測 + DECISIONS 決策：配對/統計套利、指數 ETF 換股、除權息、融券軋空 | STRATEGY §5a | T-018 | B | todo |
| T-031 | PG 供應層底座：ServingConfig+configs/serving.yaml、docker `pg` 服務、`data/serving/`（schema/sync/rebuild CLI）、鏡射六張現成表、sync_state 冪等、IO 邊界擴充（禁 psycopg/serving import） | SPEC-009 §1/§2/§5 + PRD-001 | T-011 | A | done |
| T-032 | 因子物化：`data/serving/materialize.py`、factor_values/factor_cross_stats/composite_daily、每日 pipeline 尾波掛點、歷史逐日回補（as_of 逐日推進）、與 PitView 現算抽樣一致性測試 | SPEC-009 §3 | T-031, T-012 | A | done |
| T-033 | 個股 API v2：overview/ohlc/institutional/margin/revenue/factors-history/composite-history 端點 + scorecard 改讀 PG + DuckDB 降級 + `source` 欄；`monitoring/stock_api.py` | SPEC-009 §4 + PRD-001 §5 | T-031, T-032 | B | done |
| T-034 | 前端個股頁改版：Header+P1–P4+P6+P7（含 sparkline）+P8 全數上線；元件抽出 `frontend/src/stock/` 目錄未做（純整理，另開任務） | PRD-001 §3 | T-033 | B | done |
| T-035 | broker_trades 全市場抓取+回補（富邦DJ，禮貌限流）+ `/brokers` 端點 + P6；集保週頻 ingest 排程 + `/dispersion` 端點 + P5 | SPEC-009 §2.2/§4 + STRATEGY §3 + D-019 | T-010, T-031 | B | 部分完成 |
| T-036 | 主力分點因子上板：T-016 的 chip.concentration_* 落地後併入 factor_values/計分卡/P6 集中度折線（依賴 broker ≥60 交易日史） | STRATEGY §3 + SPEC-009 §3 | T-016, T-032, T-035 | B | todo |
| T-037 | inst_holding ingest（FinMind）+ Header 外資持股比 + 上櫃法人/融資券補源 + TDCC 互動站備援來源評估 | SPEC-001 §1 + PRD-001 §4 | T-010, T-031 | B | todo |
| T-038 | 綜合分預測力研究（D-020）：滾動 PIT IC 加權(A) vs 實證有效子集(B) 樣本內估計＋樣本外驗證報告 → 回 D-020 裁決；C（前端「非預測訊號」標示）依裁決同批落地 | SPEC-009 §3 + D-020 | T-032 | B | done |
| T-039 | 行為錨定因子：`price.cgo_250d` + `gain/loss_overhang_250d`（精讀 An 2016 §2 定案公式 → 實作三因子 → IC 報告含與 52週高/動能/反轉的相關矩陣） | STRATEGY §4a + research/PAPERS-KEYLEVELS.md + D-021 | T-011（還原價）, T-012 | B | blocked（D-021 + 還原價） |
| T-040 | 策略選股池篩選器：`monitoring/screener.py` 五池（主力吸籌/集中×營收/趨勢×營收/三面向/出貨警示）+ `configs/screener.yaml` + `/api/screener` + 前端面板 | SPEC-007 §6 AC-7~11 | T-031, T-035 | — | done |
| T-041 | 選股池每日快照與回溯：`serving.screener_daily` 表 + CLI `screener-snapshot`（區間回補、冪等）+ `/api/screener?date=`（快照優先、無則回算）+ 前端日期選擇 | SPEC-007 §6 AC-12~14 | T-040 | — | done |
| T-042 | 型態因子基元＋首發：`factors/patterns.py` 樞紐點基元（confirm_bars 確認延遲反前視）+ `price.double_bottom_120d`（事件型稀疏輸出、active_days 持續窗）+ 合成序列單元測試 + assert_no_lookahead | STRATEGY §4b + D-027 | T-012 | B | done |
| T-043 | 型態因子補全＋事件研究：`price.hs_top_120d`、`price.box_breakout_60d` + ic.py 事件研究純函數（樣本數/事件後 5/10/20d 超額/勝率/成立日漲停占比；reporting 落地、SPEC-002 §4 同步）+ 與 52週高/動能相關矩陣 | STRATEGY §4b + D-027 | T-042, T-013 | B | done |
| T-044 | ic-report PG 讀取路徑：`--pg/--no-pg`（預設 PG，比照 materialize 的 PgTableSource；D-026 後本機資料湖已空）＋型態因子非正價防衛＋漲停占比旗標全缺改 NaN | SPEC-002 §4 + D-026 | T-031, T-043 | B | done |
| T-045 | 旗形有撐因子：`price.flag_hold_50d`（base→pole→flag→測撐成功日進場，非突破進場）+ 合成序列測試 + assert_no_lookahead + IC/事件研究/逐筆回測實證 | STRATEGY §4b.2 | T-042 | B | done |
| T-046 | 反轉×營收策略正式化（Sleeve C 原型）：`configs/strategies/reversal_revenue.yaml` + 回測引擎 PG 路徑（calendar TableSource 鴨型、PgTableSource cache、engine/build 型別放寬、CLI backtest --pg）+ 全期回測 + walk-forward | STRATEGY §2 Sleeve C + D-020/D-026 | T-018, T-031 | B | 部分完成（引擎數字被 D-029 資料品質作廢；事件級乾淨驗證通過） |
| T-047 | D-029 引擎防衛：`_fill` 跳價拒單（price_jump）+ 持倉跳價 min 價強制出場 + 宇宙衛生濾網（跳價/缺口近窗排除）+ costs.yaml data_guard + 測試 ×3 | D-029 裁決 B 部 + SPEC-004 §3 | T-018 | B | done |
| T-048 | D-029 治本（資料層）：prices_daily 品質閘門（非漲跌停跳價攔截）、停牌區間標記、2,776 筆 2× 跳價分類盤點（減資/復牌/鏡射缺口）、corporate_events/還原價（T-011 待補項）排程 | D-029 裁決 A 部 + SPEC-001 §7 | T-011 | A | todo |
| T-049 | Sleeve C 上畫面：`/api/backtest` 接 PG（TableSource）+ `strategy` 參數（列 configs/strategies）+ 前端策略回測頁策略下拉 + 部署 | SPEC-007 + T-046/T-047 | T-046, T-047 | B | done（部署待 merge+rebuild） |
| T-050 | Sleeve C 事件窗表達：崩跌×營收事件窗因子（active_days=20，比照型態因子）+ 日頻等權門檻策略——在現有引擎表達「事件進場、持有 20 日」；排名式週/月頻已證實 CAGR −17%~−20%（買到「相對最慘」而非「剛崩」+ 持有語意錯配），事件級 64% 勝率需事件持有語意 | STRATEGY §2 Sleeve C + T-046 診斷 | T-046, T-047 | B | todo |
| T-051 | 回測期末持倉顯示：engine `Holding`/`final_holdings`（SPEC-004 §4 持倉快照期末切片）+ API holdings 欄 + 前端策略回測頁期末持倉表（中文名對照） | SPEC-004 §4 | T-049 | B | done（部署待 merge+rebuild） |
| T-052 | 期末持倉批次明細：engine `_Lot` 追蹤（本輪首買日/加權均價/持有期最低收盤 MAE；加碼併入、歸零或強制出場即重置）→ Holding 加 entry_date/avg_cost/ret/max_loss + API + 前端欄位（買進日/均價/報酬率/最大虧損） | SPEC-004 §4 | T-051 | B | done（部署待 merge+rebuild） |
| T-053 | 【雲端 Phase 0】全史歸檔保命線：本機 PG 全庫 `pg_dump -Fc` → 上傳 Google Drive（rclone + service account，秘密只進 .env/GHA Secrets）+ **還原演練**（新空 PG 還原、抽查 broker_trades 列數與日期範圍一致）。AC：dump 檔在 Drive、還原後 `SELECT count(*)` 對得上、還原步驟寫成文件 | D-030/D-033 | — | B | done（2026-07-29 Drive 上傳完成，base dump MD5 c12d5683 兩端吻合；daily/ rclone check 過） |
| T-054 | 【雲端 Phase 0】每日增量歸檔：CLI `archive-daily --date`（dump 當日新增 raw 列，broker_trades 必含）→ 上傳 Drive；冪等（重跑同日覆蓋同檔）；檔案佈局支援日後合併進基底（D-033 選項 B）。AC：連跑兩日產生兩個日檔、重跑冪等、單日檔可還原進空表 | D-030/D-033 | T-053 | B | done |
| T-055 | 【雲端 Phase 1】Supabase 建置 + 滾動窗口：建專案/serving schema；先盤點 configs 全因子的最大價格回看天數（窗口硬下限）；CLI `serving prune --window-config`（prices 260 交易日/broker 明細 10 日/broker_flow 250 日/factor_values 60 日，入 configs/serving.yaml 不寫死）。AC：修剪後總量 < 400 MB、窗口 ≥ 最大回看、prune 冪等 | D-030 + SPEC-009 | T-053 | B | done（總量 446 MB：factor_values 實測遠超估算，過渡縮窗後低於 Free 500 上限但未達 400 目標 → D-034 裁決長期方案） |
| T-056 | 【雲端 Phase 1】GHA 每日 pipeline workflow：`daily-pipeline.yml`（cron UTC `5 11 * * 1-5` + `workflow_dispatch(date)`）；DSN/token 全走 GHA Secrets；步驟=pipeline → 增量歸檔上傳 → prune；失敗通知（GH issue 或 email）。AC：手動 dispatch 跑通一整日、cron 觸發一次成功、Secrets 無明文 | D-030/D-032 | T-054, T-057 | B | 部分完成（2026-07-29 手動 dispatch 全步驟成功並獨立驗證；待 cron 自動觸發驗收） |
| T-057 | 【雲端 Phase 1】pipeline 寫 Supabase 相容性：`store.py` DSN 支援 pooler :6543 + 關 prepared statements（psycopg `prepare_threshold=None`）；`_run_daily_pipeline` 在「無本機資料湖、純 PG」環境跑通（D-026 遺留的 parquet 路徑步驟盤點與繞開）；每日 pipeline 對滾動窗口資料計算因子正確（回看不足即 raise 不靜默） | D-030 + SPEC-009 | T-055 | A | in_progress |
| T-058 | 【雲端 Phase 1】平行運行比對：GHA pipeline 與本機 scheduler 同時跑 ≥5 個交易日，逐日比對兩邊 factor_values/composite_daily/screener_daily（bit-identical 或差異可解釋）；比對腳本落 scripts/。AC：連續 5 交易日全對帳通過才准進 Phase 2 切換 | D-030 | T-056 | B | todo |
| T-059 | 【雲端 Phase 2】Vercel 部署：FastAPI 掛 Python serverless function（`api/index.py` + vercel.json rewrite `/api`）、DSN 指 Supabase pooler；前端 `vite build` 同專案部署（nginx 反代退役）。AC：Vercel 上 `/api/health`/個股頁端點全通、前端頁面互動正常、冷啟 < 3s | D-030 | T-055 | B | 部分完成（程式碼+本機雲端模擬驗證done；待使用者在 vercel.com 連 repo + 設 TWQUANT_PG_DSN 環境變數） |
| T-063 | 【雲端 Phase 2】snapshot 系端點脫離資料湖：`monitoring/snapshot.py` 的 `market_index`/`market_indices`/`factor_reports`/`capacity`/`coverage` 仍走 `wh.sql()`（DuckDB over parquet），雲端無湖 → 大盤K線/因子IC/指數/容量面板留白。改讀 PG（比照 stock_api 的 PG 路徑）；`/api/snapshot` 重端點同步或明確標記雲端不支援。AC：空湖 + Supabase 下 `/api/market`/`/api/factors`/`/api/indices`/`/api/capacity` 皆有資料 | D-030 + SPEC-009 §4 | T-059 | B | todo |
| T-060 | 【雲端 Phase 2】長任務端點改 workflow_dispatch：`POST /api/pipeline-jobs`（+補跑日期）改為以 GitHub API 觸發 T-056 workflow；前端「一鍵跑今日全部資料」改輪詢 GHA run 狀態（run id + conclusion）。AC：前端按鈕→GHA 實跑→狀態回顯全鏈通 | D-030 + D-018 | T-056, T-059 | B | todo |
| T-061 | 【雲端 Phase 2】回測端點雲端化（依 D-031 裁決）：暫按選項 A——區間超出窗口回 400 + 前端提示文案；`/api/backtest` 在 serverless 時限內完成（超時則預先縮範圍或降級） | D-031 | T-059 | B | blocked（D-031） |
| T-062 | 【雲端 Phase 3】切換與本機退場：驗收清單（T-058 對帳綠、Vercel 端點全通、歸檔鏈連續無斷日、Supabase 保活 job 就位含長假情境）→ 停本機 compose；本機 Docker volume 與 dump 保留 ≥1 個月；memory/文件同步（deploy 佈局說明改雲端版） | D-030 | T-058, T-060, T-061 | B | todo |
| T-064 | 前端個股因子頁重新設計：元件抽出 `frontend/src/stock/`（T-034 遺留）＋跨頁共用層（theme/fallback/hooks/ui/charts）＋mobile-first 響應式（手機頂欄、橫捲 chip 列、響應式圖高、ZBars）＋終端風精煉（TPanel/方角 chip） | PRD-001 §3 | T-034 | — | done |
| T-065 | 【ML Phase 1】資料深度審計：本機湖/Drive 歸檔各表連續歷史盤點（分點/股權分散/月營收起點）、生存者偏誤檢查（stock_meta 是否含已下市）、訓練可用期間定案 → 回填 SPEC-010 §2 與 `split.train_min_years` | SPEC-010 §2 | — | B | done |
| T-066 | 【ML Phase 1】資料集組裝＋切分器（純函數）：open-to-open 超額標籤＋rank_pct 轉換、特徵×標籤 PIT 對齊（缺值留 null）、purged walk-forward（embargo/金庫期）＋hypothesis 屬性測試 | SPEC-010 §2/§3 AC-1~3 | T-065 | A | done |
| T-067 | 【ML Phase 1】訓練/推論 CLI：`ml train`/`ml score`、`configs/ml.yaml`（MlConfig）、`data/models/` 佈局＋meta.json、決定性測試、research_log 實驗登記；`uv add lightgbm` | SPEC-010 §1/§4 AC-4/5/8 | T-066 | B | done |
| T-068 | 【ML Phase 1】ml_scores 資料合約＋薄因子：pandera schema、PG 鏡射窗口（serving.yaml）、`ml.lgbm_rank_10d` 註冊＋`assert_no_lookahead`＋IC 對照報告（vs zscore_weighted 基準） | SPEC-010 §5/§6 AC-6/7/9 | T-067 | B | done |
| T-069 | 【ML Phase 1】評估階梯與部署裁決：OOS RankIC → 分位單調 → 成本後回測（換手率/平滑）→ deflated Sharpe（trials=登記簿）→ 金庫期最終評估 → DECISIONS.md 裁決是否部署 | SPEC-010 §7 AC-10/11 + D-038 | T-068 | B | done（研究失敗為合法產出：第 1-2 級通過；第 3 級 1× CAGR -14.29%／Sharpe -0.36，0× 也僅 +1.23%／+0.22，未達 D-038 gross Sharpe 0.5 早停。未做 EMA/buffer/DSR、金庫未開、不部署。canonical hash/family scope/model pin/成本診斷落地；完整閘門 ruff+format+mypy 綠、pytest 401 passed/22 skipped） |
| T-070 | 【ML Phase 1】每日 pipeline 推論步驟＋月頻 refit workflow：模型檔隨 Drive 歸檔遞送、`ml score` 排入 pipeline（供應層物化後）、refit 獨立 GHA workflow | SPEC-010 §4/§8 | T-069 | B | blocked（D-038：T-069 第 3 級早停，Phase 1 不部署） |
| T-071 | 【回測正確性】D-037 修正：衛生濾網缺口判準改用交易日（`prices_daily` 日期聯集為市場日曆，全市場休市不算缺口）＋宇宙為空時 `build_targets` 明確 raise（禁靜默回零、禁把無效試驗寫進 research_log）；`UniverseConfig.max_gap_days`→`max_gap_sessions` | D-037 | — | B | done（閘門全綠：ruff/format/mypy 152 檔無誤、pytest 409 passed；AC-1/2/3 對應 test_ac1_market_wide_holiday_is_not_a_gap／test_ac2_single_stock_suspension_still_excluded／test_ac3_empty_universe_raises_instead_of_silent_zero，先寫測試確認三條皆紅再實作） |
| T-072 | 【ML Phase 2】首個成本感知長週期候選：H20 open-to-open 超額報酬扣同成本模型的估計進出成本、下檔懲罰、穩定核心特徵、獨立 config/model family；purged walk-forward 僅跑既有開發期，vault 不開。AC：手算標籤/PIT ADV/缺值/決定性/config hash/舊 H10 相容性測試；產出 OOS 報告並依 D-039 早停 | SPEC-011 AC-1~8 + D-039 | T-069, T-071 | B | done（D-040 修正後重跑：OOS RankIC −0.0188→**+0.0193**、為正的窗 0/16→11/16、葉節點 |max| 5.99e36→0.091、|score|>1e10 100%→0%，根因證實；但**階梯第 1 級未通過**——對最強基準 B（D-020 子集 +0.0282）差 −0.0089，依 SPEC-010 §7 不進第 3 級，未跑 backfill-scores 與零成本回測。閘門全綠：ruff／ruff format／mypy 156 檔／pytest **432 passed**。AC-3 對應新測試 `test_ac3_invalid_entry_price_yields_null_and_does_not_poison_cross_section`，先確認為紅（`1101 被壞列汙染成 -inf`）再實作。缺陷 artifact 保存於 `data/models/_archive/…__pre-D040-inf-defect`。**使用者 2026-09-01 裁決：研究完成、候選失敗、不部署**，比照 T-069／D-038；金庫期未開封。後續方向見 SPEC-012／T-076） |
| T-076 | 【ML Phase 3】降周轉：凍結 Phase 1 模型 `lgbm_h10_20260225_2f5a21a9`，只在組合構建層加 buy/hold banding（事前承諾 entry `rank_pct >= 0.90`、exit `< 0.70`，**不得搜尋**）。不重訓、不換特徵、不改標籤，故不可能洩漏、不可能膨脹 IC。分數讀既有 `ml_scores`（1,737,542 列），不需重新 backfill。頭條指標改為 break-even 交易成本（>0.60%）與單邊月周轉（<50%）。AC：手算續抱/建倉/出場、非法門檻載入即失敗、純函數決定性與 IO 邊界、周轉嚴格下降、`ml_rank.yaml` 既有結果不變、診斷輸出兩個純量、唯一 `bt:ml_rank_h10_band` scope | SPEC-012 AC-1~7 + D-041 | T-072 | B | done（**候選失敗，不部署**。banding 在周轉上完全奏效：年化單邊周轉 2,874%→**1,519%**（−47.1%，落在 Novy-Marx & Velikov 報告的 33–55% 區間）、成本 58.50%→**34.59%** 資金、平均持有 7.5→**15.3** 交易日（2.04×）。**但毛訊號崩了**：同路徑 gross CAGR +4.49%→**−3.03%**、gross Sharpe +0.33→+0.02，掉幅大於成本節省，故淨 CAGR −14.29%→−15.57%、MDD −56.19%→−62.03%。兩個頭條指標皆未過：單邊月周轉 126.6%（門檻 <50%，超標 2.5×）；毛績效為負代表無 alpha 可支付成本，break-even ≤ 0。閘門：ruff（我方檔案）／mypy 158 檔／pytest **442 passed**；10 條測試涵蓋 AC-1~7，先確認紅燈（ModuleNotFoundError）再實作。AC-5 指標逐項重現 D-038 值，但 params_hash 因新增 optional 欄位而變動——見 T-078。單一 trial `bt:ml_rank_h10_band` = `85bac503d98ad674`） |
| T-078 | 【研究登記簿正確性】canonical `params_hash` 對「向後相容的 schema 新增」不穩定：`research_log.py:97` 的 `strategy.model_dump(mode="json", exclude={...})` 會納入值為 `null` 的 optional 欄位，故 T-076 為 banding 新增 `entry_rank_pct`／`exit_rank_pct` 後，**未修改的** `ml_rank.yaml` 其 hash 由 D-038 登記的 `ff18453792d14cc4` 變為 `81749904fc39f756`（指標逐項相同，已實測）。結果：`bt:ml_rank_h10` 顯示 3 個 distinct hash 但實際只有 2 個相異實驗，DSR trials 虛增。與 T-073 為同一缺陷類型（ML config 層）。AC：canonical dump 加 `exclude_none=True`（使 hash 對 None 預設的新欄位不變）；一次性重新基準化並以文件記錄新舊對照；補測試斷言「新增 None 預設欄位不改變既有 hash」 | D-038 §2 + SPEC-012 AC-5 | T-076 | B | todo |
| T-079 | 【資料完整性 · 最高優先】`serving.prices_daily` 有 **7 個全市場價格汙染日**，全部落在 T-069／T-076 的回測期內：2023-06-14、2023-09-26、2024-01-31、2024-04-16、2024-12-19、2025-02-05、2026-01-27。特徵：當日全市場 close 中位數暴跌 20~24%、**隔日完全回復**；約 62% 個股價格錯誤（38% 正常）；例：2330 台積電 1755→**227**→1790、2454 聯發科 1770→**287.5**→1780、1301 台塑 48.8→**94.7**→51.3。已排除「解析列位移」假說（各種位移 k 的吻合率均為雜訊，k=0 最佳）。**衝擊**：符合宇宙流動性門檻的個股在壞日有 **24.28%** 單日跌幅 >35%（正常日 0.1527%，**159 倍**），而 `engine.py` 對 >35% 跳動會 `_forced_exit` 以 `min(prev, new)` 成交——等於**用假價格強制平倉並把假損失實現**，隔日價格回復卻已出場。這 7 天（佔 886 日的 0.79%）貢獻了全期 >35% 跳動事件的 **50.8%**，平均假跌幅 **−58.7%**。AC：(1) 查明擷取層根因並修復或標記這些日期；(2) 加入品質閘門——單日跌幅超過台股 ±10% 漲跌幅限制在物理上不可能，應依黃金規則 5 **raise**，不得靜默流入；(3) `engine.py` 的跳動守衛不得把資料錯誤轉成已實現交易損失；(4) 修復後重跑 T-069／T-076 並與現有結果對照 | 黃金規則 5 + D-043 | — | A | 部分完成（**資料已修**：`prices_daily` 共 **43 天**（7 天 V 型崩塌 + 35 天快照重複 + 1 天迭代後浮現）已重抓並寫回；`institutional_daily` 修 2 天（2022-02-09 內容替換、**2025-01-24 為幽靈日期**——非交易日卻有 908 列，已刪除）。偵測器迭代三輪收斂：快照重複率 >5% 的日子 **35 → 2 → 0**（最大值 55.93% → **1.64%**），V 型偵測器 **7 → 0**。全表稽核：`broker_flow_daily` 乾淨；`margin_daily` 為偽陽性（正常相鄰日本有 15.6~17.8% 重複，融資餘額黏性）；`shareholding_dispersion` 僅 3 個日期無法評估。**未完成**：AC-1 擷取層根因（為何會寫入快照）、AC-2 品質閘門、AC-3 引擎跳動守衛、AC-4 重跑對照——見 T-080） |
| T-080 | 【因子與模型重建 · 阻塞所有策略結論】T-079 修復 43 個汙染交易日後，**`factor_values` 全數需重物化**。量化：回測期 886 日中，20 日窗因子受影響 **47.0%**、60 日窗 **67.8%**、252 日窗 **73.9%**；而模型 gain 前三名正是 `vol_20d`(15.5%/20 日窗)、`high_52w`(11.7%/252 日窗)、`mom_60d`(9.6%/60 日窗)，合計 36.8% gain。鏈為 factor_values → 訓練特徵 → 模型 → oos_scores → 回測，故**模型亦須重訓**（重訓前須先封存舊 artifact——T-073 的 model_id 不含程式碼/資料版本，會靜默覆寫）。AC：(1) 重物化 2020-01-01~2026-08-28 全期因子；(2) 封存後重訓 H10；(3) backfill-scores；(4) 重跑 T-069 階梯第 1~3 級與 0× 反事實，與 D-038／D-043 登記值對照；(5) 完成前不得動用 D-038 §4 的 5 個事前登記降周轉 trial | D-043 | T-079 | A | done（**AC-1~4 全數完成**。重物化：6 個成本平衡分片、419~461 分鐘、**1,620 行情日 = 1,620 因子日**。重訓 `lgbm_h10_20260225_b2348c46`（`ml_h10_repaired.yaml`，25 特徵 pin 死，與基準 `2f5a21a9` 不同 id 故未覆寫基準）。**OOS RankIC +0.0680 → +0.0796（+16.9%）**，16 窗全正、11 窗改善；最大單窗改善在窗 5（2023-02~05，+0.0826），位置與資料修復吻合。階梯第 1~2 級：ML RankIC +0.0796／IR 0.722／勝率 78.2%／t(NW,10) **9.13**／分位頭尾 +0.0819／**嚴格單調 是**；對最強基準（D-020 子集 +0.0339）勝出 +0.0457。第 3 級回測（2022-07-01~2026-02-28、886 交易日）與 D-038 登記值逐項對照：1× CAGR **−14.29% → −0.01%**、1× Sharpe −0.357 → +0.087、1× MDD −56.19% → **−35.92%**、同路徑毛 CAGR +4.49% → **+15.99%**、同路徑毛 Sharpe +0.327 → **+1.227**；0× CAGR +1.23% → **+21.90%**、**0× Sharpe +0.223 → +1.210**、0× MDD −51.84% → **−27.16%**。**結論**：模型的預測力是真的（毛報酬與 0× 皆大幅轉正、分位嚴格單調），但**年化周轉 2,824% 讓成本吃掉全部毛利**——成本 6,838,916 TWD ＝ 初始資金的 **68.39%**，毛 +15.99% 淨 −0.01%。故瓶頸已從「訊號無效」明確轉為「周轉過高」，正是 SPEC-012／D-041 的主線。**AC-5 遵守**：未動用 D-038 §4 的 5 個事前登記降周轉 trial。`configs/strategies/ml_rank_repaired.yaml` 與 `ml_rank.yaml` 逐字相同，只換 model_id，`trial_scope` 維持 `ml_rank_h10`（依 D-040，資料修復重跑不計為新 trial）。**兩項須知**：(1) 25 個 pin 特徵中 `chip.margin_velocity_5d`／`chip.short_margin_ratio` 因 PIT 截點而從未有值（見 T-088／D-045），基準模型同樣缺，故比較成立；(2) 另 5 個法人籌碼因子只覆蓋 219~228 日（資料僅 227 日）） |
| T-081 | 【權威交易日曆】`data/sources/twse_holiday.py` 已有官方轉接器（`openapi.twse.com.tw/v1/holidaySchedule`），但 `market_holidays` **從未落地**——clean 層與 PG 皆無此表，故全專案實際使用的仍是 `calendar.py` 由 `prices_daily` 日期聯集**推導**的日曆。這造成循環依賴：`prices_daily` 自身若含幽靈日期，`quality.phantom_dates` 就檢查不到（實例：`institutional_daily` 的 2025-01-24）。另：官方 OpenAPI 只回**當年度**（實測 27 筆／民國 115），歷史年度須改用 <https://www.twse.com.tw/zh/trading/holiday.html> 的帶年份查詢端點。AC：(1) 轉接器支援指定年度、回補 2020 起全部年度；(2) `market_holidays` 落地並同步進 PG；(3) `calendar.py` 改以官方日曆為權威、推導版降為交叉核對；(4) `quality.assert_no_phantom_dates` 接上官方日曆並納入擷取流程 | SPEC-000 AC-3 + T-079 AC-2 | — | B | todo（**端點與解析規則已驗證**：`https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule?date=YYYY0101&response=json`，回 `{stat,date,title,fields,data,queryYear,total}`，`data` 為 `[日期, 名稱, 說明]`。**解析陷阱（實測導出，勿重新踩）**：(a) 名稱不可信——2022-01-27／01-28 的名稱都是『農曆春節前最後交易日』，但說明寫『市場無交易，僅辦理結算交割作業』，**說明優先**；(b) 說明裡的日期子句可能指**別天**——2023-01-17 的說明是『1月18日及1月19日市場無交易』，指的不是本日。正確規則：若說明含『市場無交易』或『不交易』，先抽出說明中所有 `N月M日`；**有日期參照且不含本日 → 本日仍為交易日**，否則為非交易日；其餘則以名稱含『交易日』或『開始交易』判定。(c) 該端點**無 2020 年資料**（回 0 筆），2020 年須另尋來源。**以此規則核對 2021-01-01 起的 1,374 個交易日：幽靈日期 0 天、官方交易日卻無行情 0 天**，即修復後的 prices_daily 在**已列出的**假期上無異常。**但該核對有盲區——颱風停市**：官方 holidaySchedule 只含**預先公告**的假期，臨時停市（颱風）不在其中，故颱風日在上述邏輯中會被當成正常交易日；若某颱風日被塞入偽造資料，檢查抓不到。實測 2021-01-04~2026-09-01 區間內，「平日、無行情、且不在官方假期表」共 **7 天**，全部對應已知颱風停市：2023-08-03（卡努）、2024-07-24／07-25（凱米）、2024-10-02／10-03（山陀兒）、2024-10-31（康芮）、2026-07-10——無其他無法解釋的缺口，故資料完整。**設計要求**：權威日曆 ＝ 官方假期表 ∪ 臨時停市清單。臨時停市須有**獨立來源**（TWSE 停止交易公告，或行政院人事行政總處停班公告 open data）；用「資料缺口反推」是循環論證，不得作為驗證依據，只能作為建立初始清單的起點。上列 7 天可作為 2021~2026 的初始清單，但每筆須註明依據） |
| T-082 | 【供應層缺口】`index_daily` **從未有程式把它寫進 PG**——全庫 grep 不到任何 insert/upsert/copy，且它不在 `serving/schema.py` 的 `MIRROR_SPECS` 中，故 `serving sync` 不會同步它（實測 sync 輸出無此表）。`backfill-index` 只寫進 clean 資料湖，監控層 `snapshot.py` 也是直接讀資料湖。PG 現有的 104 列（2026-05-04~2026-07-16）來源不明。**後果**：`price.rs_market_20d` 宣告 `tables=[prices_daily, index_daily]` 且物化改讀 PG（D-026），故它只能在那 52 天算得出來——這正是該因子在 Phase 1 模型中 **split=0、gain=0.00%**（完全未被使用）的原因，也是 08-29 交接文件記載「只有 32 天覆蓋」的成因。而 Phase 2 的 12 特徵清單仍包含它，等於 12 個裡有 1 個是空欄。AC：(1) `index_daily` 加入 `MIRROR_SPECS`（含 schema 與主鍵 (date, index_id)）；(2) `serving sync` 能同步它，補測試；(3) 回補 2020-01 起的指數歷史並同步進 PG；(4) 重物化後確認 `rs_market_20d` 的覆蓋率與非空比例 | D-026 + SPEC-009 | — | B | done（AC-1~3 完成：`index_daily` 加入 `MIRROR_SPECS`，pk `(date, index_id)`、額外索引 `(date)`，欄位與既有 PG 表及 clean 層 parquet 一致；`serving sync` 已能同步（實測 **+1,619 日**，TAIEX 1,619 列／TPEX 1,513 列，涵蓋 2020-01-02~2026-09-01，與行情日數 1,619 完全相符）；指數歷史已由 `backfill-index --from 2020-01 --to 2026-06` 回補（151 個月-來源成功）。測試先行：`test_t082_index_daily_is_mirrored_so_rs_market_20d_can_compute` 先確認為紅（`assert 'index_daily' in MIRROR_TABLES` 失敗）再實作，並同步更新 `test_serving_schema.py` 的 `_MIRROR` 集合。閘門全綠：mypy 160 檔／pytest 449 passed。**AC-4 待重物化完成後驗證**——併入 T-080） |
| T-083 | 【效能 · 因子物化 O(n²)】`lookback_days` 在 `@factor` 宣告了，但 `compute.py` **完全沒有引用它**——每個交易日都重讀整個歷史（1,619 分區、921 萬列）而非只讀回看窗。實測：讀 PG 約 **100 秒/交易日**（全期 45 小時）、讀本機資料湖 **53~84 秒/日**（29 小時）；最大回看窗僅 460 日（`revenue_accel`），多數為 20~60 日，故理論上有 3~30 倍改善空間。目前以 5 分片並行迴避（ETA 約 4 小時）。AC：(1) 視圖依該次 compute 的最大 `lookback_days` 裁切日期下界；(2) 補測試斷言裁切後結果與未裁切**逐列相同**（不得改變任何因子輸出）；(3) 量測改善倍數並記錄 | SPEC-002 | — | B | todo（**2026-09-02 實測：不可直接照 `lookback_days` 裁切，會改變因子值**。以真實重建湖原型驗證（四碼普通股宇宙、2022/2024/2026 三個年代）：邊際 ×2 時加速 2.2~5.6 倍，但仍有 6~11 個因子輸出不同；邊際 ×3 更慢（19.8~20.9s vs ×2 的 11.3~16.5s）且差異不減。**根因**：因子用的是**列基**位移（`shift(n)`／`rolling_*` over stock_id），不是日期基。交易稀疏的個股在 N 個交易日的窗內拿不到 N 列，於是產出 null；全歷史下它反而會拿到很久以前的那一列。故裁切後多數差異是「少了 1~9 檔稀疏股」（共用個股的值 **逐位元相同**），但 `volume.turnover_z_20d` 例外——邊際 ×2 與 ×3 都有 **111~223 檔值不同**，代表它的 `lookback_days=25` 相對其真實需求**宣告不足**。**另有量測修正**：瓶頸不在掃描（60 日窗 scan 0.06s vs 全歷史 0.16s，檔案裁剪更只要 0.01s），而在因子函數對 921 萬列做 sort/shift（單因子 2.96s）。**故 AC-2「逐列相同」在現行因子實作下無法達成**，本任務須先決定：(a) 改因子為日期基窗（會改變歷史值，須裁決）；或 (b) 只對可證明安全的因子套用；或 (c) 放棄裁切、改用跨因子共用已排序框架。原型碼在對話紀錄，未進 repo）→ **done（2026-09-05）**。選了上述 (b)：`compute(..., trim_lookback=True)` 用**邊際 ×2 + 30 天緩衝**（`_TRIM_SAFETY` / `_TRIM_BUFFER_DAYS`，非魔術數字），並把有效回看取 `max(fdef.lookback_days, params['window'])`——原先只看宣告值，故 `vol_20d(window=60)` 這種以參數加長窗口的呼叫會被裁太短。AC-2 由 `tests/test_lookback_trim.py` 六條測試把關，含**對全部已註冊價格因子**逐一斷言裁切前後相同，以及一條**反向測試**（故意裁太短要被抓到）——沒有反向測試的話，「相同」可能只是因為裁切根本沒生效。**量測修正了本任務的前提**：`trim_lookback` 單獨開啟**幾乎沒有加速**，因為瓶頸不在計算而在 **PG 讀取**——`PgTableSource.scan()` 整表讀出，裁切是在讀出**之後**才於 polars 過濾，省了計算沒省 I/O。真正的加速來自把日期條件推進 SQL（`since=`）加上 `cache=True`：**100 → 3.8 秒/日，約 26 倍**。AC-3 的量測即此。同一個缺陷在 broker 讀取上仍在（T-150） |
| T-084 | 【開發體驗 · 陷阱】`configs/system.yaml` 的 `data_dir: data` 是**相對路徑**，從 worktree 執行任何指令都會讀寫 worktree 的 `data/` 而非主資料夾；且 `TWQUANT_DATA_DIR` 環境變數**未被 `load_config` 採用**（.env 註解卻寫著它可覆寫）。2026-09-02 一夜絆到四次：backfill 寫到 worktree、`serving sync` 顯示 +0 日、`research_log` 落在 worktree（兩度須手動併回主登記簿）、`ml train` 的 artifact 需 `--out` 才對。症狀都是「指令回報成功但主資料夾沒東西」，**不會報錯**。AC：(1) `load_config` 支援 `TWQUANT_DATA_DIR` 覆寫（或移除該 .env 註解）；(2) `data_dir` 為相對路徑時解析為 **repo 根**而非 cwd；(3) 補測試 | SPEC-000 | — | C | todo |
| T-085 | 【資料衛生】未交易列（OHLC 全 0、volume 0）讓六個價格因子產生 Infinity。`serving.prices_daily` 有 **1,185 列 close=0**（四位數宇宙 727 列），集中於 2021-01-04~02-26 的 33 天，OHLC 與 volume 皆為 0——那是「當日未交易」的表示法，不是真實價格 0。價格比值因子（`close_t/close_{t-n}-1`）因此除以零：實測 `residual_reversal_5d` 17,952 列、`mom_60d` 1,200、`mom_20d` 952、`reversal_5d` 550、`mom_1d` 450、`rs_market_20d` 19，合計 **21,123 列 Infinity**（佔 3,600 萬列的 0.06%）。**與 T-079 是同一批來源列**。**急迫性低**：`standardize.py` 的 MAD winsorize 在 z-score 前把 inf 截到有限值，而模型讀的是 `z` 欄（`ml_store.py:32`），故模型從未見到 Infinity（實測 z 為 inf 者 0 列、範圍 [2.17, 4.22]）。但語意仍錯——當日未交易的股票不該得到 +4 sigma 的動能分數。AC：(1) 在讀取或宇宙層排除 OHLC 全 0 且 volume=0 的列（一處修正勝過六個因子各自防護）；(2) 補測試斷言此類列不產生任何因子值；(3) 確認修正後全表無 Infinity；(4) 因需重物化，宜與下一次全期重建合併執行 | 黃金規則 5 + T-079 | — | B | todo（**2026-09-02 補充：來源與既有修復皆已查明**。那 1,185 列**不是台股的表示法，是 FinMind 的編碼慣例**——FinMind 以 `close/high/low=0, volume=0` 表示無交易/無效價，而非 null。來自一次遺留的 FinMind 價格回補，範圍恰為 2021-01-04~02-26。**曾經修過但已退化**：當時的處置是把 `prices_daily.close` 收緊為嚴格 `> 0` 並讓 `clean_prices`／`clean_tpex_prices` 濾掉 `close<=0`；現行程式碼**兩者皆不存在**（`schemas.py:48` 仍是 `_nonneg`；`clean.py:54/108` 只有 `drop_nulls`）。而 `clean.py:178/193` 的 `clean_index_*` **已有** `filter(pl.col("close") > 0)` ——故本任務是**把同檔案下方既有的寫法套用到上方**，非新設計，風險遠低於原評估。另注意：當年那次直接 drop `close<=0` 曾誤刪約 240 個 ETF/債券交易日（`volume>0` 但無有效價），故條件宜為 `volume=0 且 close=0` 而非僅 `close<=0`。資料湖當時已修（0 列殘留），但**PG 從未同步該修復**，故 PG 仍有全部 1,185 列） |
| T-086 | 【供應層衛生】`serving.factor_values` 含 **2,028,026 列（5.1%）已不存在於註冊表的因子**：`chip.concentration_{5,10,20,60}d`、`chip.concentration_delta_5d`、`chip.main_force_net_5d`、`chip.main_cost_gap`、`chip.broker_count_diff`、`chip.stability_10d`（皆僅 2026-01-02~07-16 共 127 天）與 `price.resid_mom_20d`（2025-07-01~2026-07-09 共 250 天，對應 D-020 的 rs_market_20d→resid_mom_20d 改名）。`list_factors()` 現回 30 個，皆不含這些。物化只寫入註冊因子，故它們是**舊版本的殘留**，不會被覆寫也不會被刪除（materialize 是 upsert 非 replace）。**風險**：走 `features: []`（＝全部）的訓練路徑，其特徵解析來自註冊表、資料讀取來自 `factor_values`，兩者口徑不一致時可能吃到殘留；且殘留只涵蓋 127~250 天，會製造時間斷層。T-080 的重訓已用 `configs/ml_h10_repaired.yaml` pin 死 25 個特徵而迴避。AC：(1) 物化後清除註冊表外的因子列（或明確保留並記錄理由）；(2) 加閘門斷言 `factor_values` 的 distinct factor ⊆ `list_factors()`；(3) 確認清除不影響任何既有回測的可重現性 | SPEC-009 | — | B | todo |
| T-087 | 【資料完整性】**單一來源整條腿缺席**：`prices_daily` 有 **8 個交易日只有 TWSE、完全沒有 TPEX（上櫃）**——2023-09-25、2023-11-27、2024-09-18、2024-09-27、2024-12-18、2025-02-21、2025-06-23、2025-12-05。實測列數 1,189~1,308，鄰近正常日 5,846~6,736（差 4~5 倍）；以 `stock_meta.market` 拆解，壞日為 `{TWSE: 1004, TPEX: 0}`，正常日為 `{TWSE: 1009, TPEX: 776}`。缺的 774~839 檔上櫃股在該日**完全沒有價格**，而該日仍被記為擷取成功。**為何三年沒被發現**：T-079 的 `duplicate_snapshot_dates` 抓的是「多出來的列」（同一份快照寫兩次），對「少掉的列」完全無感；`status` 指令的來源缺口檢查依賴湖裡的 `part-<source>.parquet` 檔名，而重建湖由 PG 匯出、檔名一律 `part-pg.parquet`，來源出處已遺失。**發現經過**：因子重物化發車前的清點才看到列數分布異常。AC：(1) 重抓那 8 天的 TPEX；(2) 加「列數塌陷」偵測器並以真實資料驗收；(3) 查明擷取層為何會漏掉整條腿卻回報成功 | 黃金規則 5 + T-079 AC-2 | — | A | 部分完成（**AC-1 done**：8 天全數重抓成功，湖與 PG 皆已 upsert（4,607~5,743 列/日），修復後 `{TWSE: 1009, TPEX: 774}`、重複 stock_id 0 列。**AC-2 done**：`quality.sparse_dates`／`assert_no_sparse_dates`，測試先行（5 條，含「宇宙隨年份成長不得誤報早期」與「鄰近窗不足時不判定」）。基準取**鄰近 ±window 日含當日的列數中位數**而非全期中位數——股票宇宙 2020 年約 2,000 檔、2026 年約 6,000 檔，用全期基準會把早期整段誤報；含當日是為了讓宇宙規模跳階處的中位數不被前後兩種水位對半拉扯。**真實資料驗收**：對修復前的資料（只讀 `part-pg.parquet`）正好抓出那 8 天且無誤報，對修復後資料抓 0 天。**AC-3 未做**：擷取層為何漏腿仍不明——`run_ingest` 對 TPEx 的 DataError 在 `tolerant=True`（每日排程）下只記 failed 不中斷，可能是當時 TPEx 端點暫時性失敗後就再也沒補。重抓時 8 天中有 2 天首次回「回應中找不到上櫃行情資料表」、重試即成功，佐證是**暫時性失敗未被重試**而非該日無資料） |
| T-088 | 【因子 · 結構性缺陷】`chip.margin_velocity_5d` 與 `chip.short_margin_ratio` **在 `factor_values` 的整張表歷史中從未產出任何一列**。資料是好的（2026-09-01 有 1,297 列、1,231 檔 `margin_balance>0`），根因是 `margin_daily.available_at=21:00` 晚於 D-008 的 20:00 訊號截點——第 d 天的融資餘額在 `as_of=d` 依定義不可見（正確的 PIT），但因子把值標成融資列自己的日期，而 `materialize` 只留 `dates=[as_of]`，於是永遠被濾光。對照 `institutional_daily` 的 `available_at=18:00`，法人因子正常。**影響**：Phase 1 基準 `2f5a21a9` 的 25 個 pin 特徵裡有 2 個全空（連同當時因 T-082 全 null 的 `rs_market_20d` 共 3 個）。本次重訓沿用同一份 pin 清單，兩邊同樣缺這 2 個，故比較仍成立。AC：(1) 依 D-045 裁決結果修正；(2) 補測試斷言該因子在「來源 available_at 晚於截點」時仍能以前一交易日的資料產出值；(3) 加閘門：pin 在任何模型配置裡的特徵若在 `factor_values` 覆蓋率為 0，訓練須 raise 而非靜默以全 null 特徵訓練 | D-008 + SPEC-002 | D-045 | A | done（AC-1/2：`_to_next_session` 以 PitView 內 prices_daily 的交易日序列把值前移到下一交易日（join_asof forward），計算仍在融資自己的日期序列上；`short_margin_ratio` tables 加 prices_daily。測試先行四條：as_of=d 用 d−1、第一天無值、週五標到下週一、velocity 在 as_of 當天有值、兩因子過 assert_no_lookahead；test_chip_fundamental_factors 10 passed。**AC-3 ✓** `ml.dataset.assert_feature_coverage`（任一特徵無列或非空比例 ≤ 門檻即 raise、回傳各特徵非空比例；預設門檻 0 即「覆蓋率為 0 就拒絕」，刻意不加 MlConfig 欄位以免改動 params_hash／已登記 model_id），接在 `ml train` 的 assemble 之前並印出覆蓋最低 5 個特徵；生產 `ml score` 不掛（單日缺某因子屬正常）。四條測試，test_ml_dataset 14 passed） |
| T-089 | 【策略 · 排程】新增 `rebalance.schedule: biweekly`——ISO 偶數週的第一個「星期 ≥ day」開市日（週一休市順延，與 weekly 同規則）。D-046 候選 2、4 需要。AC：(1) `signal_dates` 對 biweekly 回傳每兩週一日，且是 weekly 訊號日的嚴格子集；(2) 跨年 ISO 週編號連續（2024-12-30 是 2025 W01）不重不漏；(3) 既有 daily/weekly/monthly 輸出逐位元不變 | SPEC-003 §1 + D-046 | — | B | done（`config.py` Literal 加 `biweekly`；`build.signal_dates` 以 ISO 偶數週篩週訊號日，週一休市順延規則同 weekly。測試先行四條 AC 全過；首版把 2025-03-03～05-30 的偶數週數錯記為 6（實為 7），是測試期望值錯、實作正確。閘門全綠 484 passed） |
| T-090 | 【因子 · ML 分數平滑】`ml.lgbm_rank_10d` 加參數 `smooth_days`（預設 0＝不平滑，既有輸出逐位元不變），對同一 model_id 的分數按 stock_id 做 span=N 的 EMA。**PIT**：只用 ≤ 訊號日的分數（讀自 PitView 已保證），且 EMA 是列基——分數缺日不得補 0。D-046 候選 3、4 需要。AC：(1) `smooth_days=0` 與現行輸出 `frame_equal`；(2) `assert_no_lookahead` 對 smooth_days=10 通過；(3) 手算 3 日 EMA 案例逐值相符；(4) 分數缺日的股票不產生虛假平滑值 | SPEC-010 §4 + D-046 | — | B | done（`ml.lgbm_rank_10d`／`ml.lgbm_net_20d` 加 `smooth_days`（預設 0，輸出逐位元不變），對每檔 rank_pct 做 span=N、adjust=False 的列基 EMA，缺日不補 0 也不輸出；`assert_no_lookahead` 加 `params` 覆寫讓 smooth_days=3 路徑也被反前視護欄檢查。四條 AC 全過，手算案例 0.2/0.8/0.5 → 0.2/0.5/0.5、缺日案例正確得 0.6（補 0 會得 0.5）。閘門全綠 484 passed） |
| T-091 | 【研究 · D-046 降周轉 4 候選】依登記順序跑 `ml_rank_m` → `ml_rank_2w` → `ml_rank_ema10` → `ml_rank_ema10_2w`，每個 1× 與 0×，scope `bt:ml_rank_h10_turnover`；四個全跑完才對照 D-038 §5 門檻並做 DSR。平行：`lgbm_net_h20` 修復重跑（D-040，不佔額度）。AC：(1) 8 筆 run 全數登記且 params_hash 各不相同；(2) 逐候選輸出 1×/0× CAGR、Sharpe、MDD、單邊周轉、四段報酬、逐筆勝率／賺賠比；(3) 對照表與晉升判定寫入 DECISIONS（不在本任務內裁決部署） | D-046 | T-089, T-090 | A | done（**AC-1 ✓**：8 run、8 distinct hash。**AC-2 部分**：CAGR/Sharpe/MDD/周轉已登記（表見 D-046 結果段）；**AC-2 ✓** 逐筆勝率／賺賠比／EV／中位持有／四段報酬已記入 D-046 結果段——四候選 1× 四段皆僅 2/4 正，再失一關；2025 對每個候選都是負的。**AC-3 ✓**：對照表與判定已寫入 D-046——四候選皆未過 §5（共同卡 1× Sharpe ≥ 0.5，最佳月頻 0.39；候選 4 過 4/6）。Phase 1 不部署；下一代候選見 D-047 待裁決） |
| T-092 | 【資料 · 籌碼回補】依 D-044 以 FinMind 免費層回補 `institutional_daily`（`TaiwanStockInstitutionalInvestorsBuySell`）與 `margin_daily`（`TaiwanStockMarginPurchaseShortSale`）2020-01-01 起全期。現況：兩表全期僅 227／205 日，2020、2021、2023 全空。工具：`scripts/backfill_finmind.py`（每檔一 parquet、可續傳、402 自動等額度、進度寫 ingest_log）；映射 `sources/finmind.py` 已對 2026-08-28 全市場 25/25 逐位元驗證。AC：(1) 兩個 dataset 各 2,064 檔抓齊（空檔留佔位）；(2) 以 `institutional_net`／`margin_balances` 映射後寫入湖與 PG，映射前先對既有官方資料日期抽驗一致；(3) `quality.sparse_dates` 對新表通過；(4) 重物化 7 個籌碼因子（需 T-094 的因子子集物化，否則全表 7 小時）；(5) 回補後 `institutional_daily` 覆蓋 ≥ 1,600 日 | D-044 + D-047 | — | A | done（2026-09-03 07:46）：FinMind 法人 2,064 檔／融資 2,064 檔（129 檔空）全期 2020-01-02～2026-09-02；重疊比對法人 235,138 列、融資 213,106 列皆 0 差異（法人 508 列先依 TWSE 原始修正，見 T-102）；載入 +1,393／+1,415 日，兩表 1,620 日、`sparse_dates` 0；興櫃日合併 `Dealer` 類別映射修正（478e268） |
| T-093 | 【因子 · 評價因子驗證】`fundamental.earnings_yield`／`book_yield`／`dividend_yield` 已物化全期（T-010），但從未跑過 IC 階梯，也未進任何模型。AC：(1) 對三因子跑 `ic` 報告（RankIC、NW t、分位單調）並登記 `ic:<factor>` scope；(2) 與 D-020 有效子集對照；(3) 若任一過 D-006 promote 門檻，另行事前登記「25 特徵 + 該因子」的重訓為新候選（不在本任務內執行） | T-010 + D-047 | — | B | done（**AC-1 ✓** 全樣本 2020-01-01~2026-02-25、宇宙四碼、1,471~1,486 日；**AC-2 ✓** D-020 子集同設定重跑對照。RankIC（5/10/20 日）與 t：earnings_yield +.034/+.036/+.036（t 15/15/13）、**book_yield +.038/+.048/+.061（t 14/18/22，隨 horizon 上升）**、dividend_yield +.040/+.039/+.034（t 17/15/12）；對照 residual_reversal_5d −.047/−.033/−.029（t −20/−15/−14）、reversal_5d −.045/−.026/−.023（t −14/−9/−8）、revenue_yoy +.022/+.025/+.024（t 11/13/12）。三者皆遠過 D-006 promote（t≥3）。**關鍵發現：earnings_yield 與 dividend_yield 的五分位多空價差為負**（E/P −0.10/−0.25/−0.60%、殖利率 −0.41/−0.82/−1.70%）——RankIC 正但最便宜一組跑輸最貴一組，價值陷阱形狀；book_yield 兩者同向（+0.07/+0.15/+0.32%）；revenue_yoy IC 中等但價差最大（+0.51/+0.89/+1.47%）。結論：三者可進 LightGBM（非線性），**不得**進線性 composite 或單獨當訊號。**AC-3**：已由 D-048 事前登記為 28 特徵候選（T-096）。**OOS 期（2022-01-14 起，973~988 日）同號同量級**：E/P +.036/+.038/+.039（t 12/12/11）、B/P +.034/+.042/+.053（t 11/14/17）、殖利率 +.043/+.043/+.041（t 15/14/12）；價差符號亦同（E/P、殖利率為負，B/P 為正）。T-093 **done**。附帶：T-095 修好 ls_spread 的 NaN 後才拿到這些價差） |
| T-094 | 【供應層 · 因子子集物化】`serving materialize` 一律算全部註冊因子（30 個、每日 53~145 秒），籌碼回補後只需重算 7 個籌碼因子卻得重跑全表 7 小時。AC：(1) `materialize_day` 支援 `factors=` 子集；(2) 子集模式**不得**改寫 `composite_daily`／`factor_cross_stats` 中非子集因子的列（composite 若含子集因子則以 PG 既有的其他因子 z 合併重算，或明確拒絕含 composite 因子的子集）；(3) 測試斷言子集模式對非子集因子的 `factor_values` 逐位元不變 | SPEC-009 + T-083 | — | B | done（`_compute_universe`／`_materialize_on`／`materialize_day`／`materialize_range` 加 `factors=`；子集內未註冊名字在 try 之外 raise（否則會「成功地什麼都沒算」）；子集含 composite 因子 → raise；子集模式只 upsert factor_values 與 factor_cross_stats、不碰 composite_daily。`scripts/backfill_factors.py` 第 4 個位置參數為逗號分隔子集。測試先行四條（子集只算子集、預設全部不變、未知因子 raise、composite 衝突 raise、只寫因子表），7 passed） |
| T-095 | 【研究工具 · 除以零第四例】`factors/ic.py` 的 `forward_returns` 是 `fut / close − 1`，不擋 `close > 0`。T-085 的 `close=0` 列（FinMind 對無交易的編碼）讓前向報酬出現 `−1.0` 與 `inf`——`drop_nulls` 濾不掉；RankIC 走 rank 僥倖存活，但 `_quintile_spread` 對含 inf 的分位取均值被毒成 NaN，實測 `ic-report` 對三個評價因子全印「多空價差 +nan」。AC：(1) 分母與分子皆 > 0 否則 null；(2) 測試斷言 0 價位不產生非有限值、多空價差仍有限 | SPEC-002 §4 + T-085 | — | B | done（測試先行兩條：`[-1.0, inf, …]` 紅 → 防護後綠；test_ic + test_ml_factor 22 passed。六個因子的 IC 報告以修復後程式重跑以取得乾淨的 ls_spread） |
| T-096 | 【模型 · 第二代候選】D-048：`ml_h10_v2.yaml`（25 + 3 評價因子 = 28 特徵）於籌碼補齊後重訓；`ml_rank_v2.yaml` 只換 model_id。AC：(1) 前提 T-088／T-092／T-093／T-094 全過；(2) 訓練 → backfill-scores → ml eval → 回測 1×/0×，登記 `bt:ml_rank_h10`；(3) 與 `ml_rank_repaired` 逐項對照並記入 D-048；(4) 特徵重要度報告：籌碼 7 個與評價 3 個的 gain 佔比 | D-048 | T-088, T-092, T-093, T-094 | A | done（2026-09-03 09:20；閘門見鏈 B 日誌）：OOS RankIC +0.0887（repaired +0.0796）、階梯 1 級過；回測 1× CAGR −4.76%／Sharpe −0.18／周轉 2,883%，0× +13.47%／0.77（repaired 0× +21.9%／1.21）；籌碼 gain 13.8%、評價 21.1%；**依 §5 不晉升**，結論記 D-048 |
| T-097 | 【資料 · 財報三表（品質維度）】FinMind 免費層實測可取 `TaiwanStockFinancialStatements`（損益表 17 科目）、`TaiwanStockBalanceSheet`（101 科目）、`TaiwanStockCashFlowsStatement`（27 科目），季頻、`date`＝季底、欄位 `type/value/origin_name`。現況：schema 18 表無任何財報欄位，模型的「品質」維度整個不存在。AC：(1) 三表回補 2020 起全期（`scripts/backfill_finmind.py`，每表 2,064 檔，額度約 10.5 小時，排在籌碼之後）；(2) 新表 `financial_statements`（長表：date=季底、stock_id、statement、type、value、available_at、available_at_estimated）——**PIT**：`available_at` 用法定申報期限（Q1 5/15、Q2 8/14、Q3 11/14、年報 3/31）22:00 估算並標 estimated=True，比照 monthly_revenue；(3) 因子（先 5 個，皆需 `assert_no_lookahead`）：ROE（近四季淨利／平均權益）、毛利率、營益率、負債比、FCF yield（營運現金流−資本支出 ÷ 市值代理：股價×股本）——市值無欄位，先用 PBR×淨值反推或暫以 FCF／總資產；(4) IC 階梯（`ic-report`）與 D-020 子集對照；(5) 不進模型——進模型須另行事前登記 | D-044 + D-048 | T-092 | A | in_progress（**AC-2 ✓** `financial_statements` 長表 schema（clean 19 表 + PG 鏡射，PK 含 statement）、`finmind.statement_rows`（_per 剔除、缺值 raise、available_at＝法定申報期限 22:00、非季底 raise）、`scripts/load_finmind_statements.py`（重疊鍵只比對不覆寫）。**AC-3 ✓（程式）** `factors/quality.py` 七個因子：gross_profitability／roe／accruals／asset_growth／sue／leverage／log_market_cap，17 條測試先行全綠——含 PIT 邊界（年報 3/31 20:00 不可見、4/1 可見）、現金流 YTD 拆季、TTM 四季須連續、缺季不得回退到舊季（`_map` 映射後才 drop null）、SUE 前 8 個同比差不足即 null、七因子 assert_no_lookahead。實測：損益表單季值、現金流量表累計 YTD、資產負債表時點值；損益表與資產負債表同名科目 EquityAttributableToOwnersOfParent 分別是淨利與權益。**AC-1 執行中**（三表回補排在籌碼之後）；**AC-4 待資料**） |
| T-098 | 【資料 · 還原價（除權息層）→ **標籤總報酬**】依 **D-054** 重新界定：這是下一代避雷模型的**擋路者**（實測 6–8 月 h=20 有 38.2% 樣本換分位、原「最差一成」有 17.9% 是除息假象；全年 6.8%）。**AC-1 已達成**——`TaiwanStockDividendResult` 已於 2026-09-03 22:20 抓完，12,149 筆事件閒置於 `data/staging/finmind_dividend_result/`，不需再呼叫 API。AC：(1) ~~回補~~ **已完成**；(2) `corporate_events` 落地（**沿用既有宣告的表**，不得新表——`test_schemas.py:71` 釘死 `len(SCHEMAS)==19`；`adj_factor = after/before`；`available_at` = 除息日 18:00 Asia/Taipei，**不得省略**，否則 `warehouse.py:61` 會跳過 PIT 過濾）；(3) **標籤**改總報酬（`ml/dataset.py:28` 與 `:116`；`dataset.py` 在 IO 靜態閘的純檔清單內，股利資料須由 `cli.py` 邊界讀入後以參數傳進）；(4) 還原前後的標籤對照數字記入 D-054（**已完成**，見該條表格）；(5) 六個資料陷阱逐一有測試（D-054 列出：價格落差非現金股利／上市櫃兩套詞彙／`reference_price` 對股票股利不可用／9 筆負值與零值／~5.75% 除息日無行情列須 as-of join 不得內連接／事件表含 ETF） | D-054 | T-092 | **A** | done |
| | **完成註記（2026-09-04）**：閘門全綠（572 passed，+15 條測試）。`corporate_events` 已載入湖與 PG：12,149 筆、1,880 檔、2019-01-02 ~ 2026-09-02，現金 10,216／合併 1,001／配股 932，`adj_factor` 0.2406~1.0152（5 筆 >1 為增資價高於市價），`available_at` 無 null。**端到端驗證**：完全走生產函數（`read_corporate_events` → `attach_adjustment` → `forward_open_returns(adjusted=True)`）重算 D-054 的表，與獨立的 scratch 腳本逐格吻合（h=20 6–8 月換分位 38.24%、最差一成救出率 17.92%）。**設計決定**：`MlLabelConfig.total_return` **不給預設值**（None ＝ 未表態，`ml train` 直接拒絕）——給預設值的話，忘了寫的新配置會靜默沿用被污染的標籤。四個既有配置已明寫 `false`，誠實記錄凍結模型的配方。成本仍用真實成交價（`test_ac3_cost_model_uses_raw_price_not_adjusted` 把關）。 | | | | |
| T-113 | 【回測 · 引擎收股利現金】D-054 的另一半：因子端與引擎端。引擎用**原始可成交價**成交＋股利**現金入帳**（`engine.py` 的 `state.cash` 目前只有三處買賣結算，無任何收益路徑），維持 `equity == cash + positions_value` 恆等式。**待決點（實作前須裁決）**：純股票股利／權息（1,933 筆、15.9%）無法拆成現金——是配股數（破壞 1,000 股整股與可成交性）還是按 `adj_factor` 折算現金（非真實現金流但總報酬正確）。另需處理 `Holding.ret`（`engine.py:352-366` 以成本基礎計算，現金入帳不會反映，除息會顯示為虧損）與 `PaperBroker`（`broker.py:130,133` 同樣無收益路徑，`daily.py:34` 的 `tol_cash=100.0` 會被任何有意義的配息撐破，擋住實盤）。**因子端被 D-055 擋住**（還原價錨定規則未定）。 | D-054, D-055 | T-098 | B | blocked |
| T-114 | 【資料 · corporate_events 新鮮度與更新路徑】複查發現：事件表沒有任何 ingest 或排程接線，最大日期凍在 2026-09-02，而 `read_corporate_events` 讀到什麼算什麼、不檢查涵蓋是否及於訓練窗尾端——之後每個除權息季會**靜默**未還原，且不會有任何錯誤。來源是逐檔全期抓取（2,064 檔、約 3.5 小時），不是每日端點，故不能直接塞進 `_run_daily_pipeline`。AC：(1) 決定更新機制（增量抓最近 N 檔？月頻全量？）並記入裁決；(2) 載入時寫入水位（`serving.sync_state` 或等效），因為「最後事件日」在淡季與「資料過期」無法區分；(3) `ml train` 在 total_return=true 時檢查水位涵蓋訓練窗尾端，不足即 raise；(4) 一鍵跑今日全部資料的按鈕要涵蓋它（見既有慣例） | D-054 | T-098 | B | todo |
| T-115 | 【閘門穩定性】`tests/test_serving_sync.py::test_ac2_incremental_and_idempotent` 於 2026-09-04 的四次全套執行中失敗一次（AssertionError），其餘三次與單獨執行皆通過。**2026-09-05 補注**：T-132 在 `ingest_log`／`research_log` 找到「時間戳檔名碰撞→靜默覆蓋」的根因；本條指的是**另一條測試**（`test_serving_sync`），是否同源尚未查證，不得直接套用該結論。時點緊接在 `corporate_events` 加入 `MIRROR_SPECS` 之後——`sync_all` 因此多迭代一張表，而該測試在共用的測試 schema 上跑 `_drop` → `sync_all` → 斷言。偶發的閘門紅燈會侵蝕「全綠才算完成」這條規則的效力，故不當作雜訊。AC：(1) 穩定重現（迴圈跑全套並保留失敗輸出）；(2) 找出跨測試的狀態互擾來源；(3) 修好或明確隔離 schema | — | T-098 | C | todo |
| T-116 | 【資料 · 歷史回補到 2005】拉長樣本是唯一能鬆動 DSR 門檻的動作：實測 DSR ≥ 0.95 在現有 885 個 OOS 日下需要年化 Sharpe **2.03**（歷來最佳 1.27 且是 0× 幻想值）；OOS 拉到約 2,200 日降到 1.71、4,500 日降到 1.54。**存活者偏誤是本任務的核心風險**：`backfill_finmind.py:_stocks()` 取自 `prices_daily WHERE date >= 2020`，只含活到 2020 年的公司——實測漏 951 檔，其中 **563 檔在 2020 年前已下市**。故改用 `TaiwanStockInfo ∪ TaiwanStockDelisting` 的 3,007 檔（`staging/universe_historical.txt`）。**各資料集起點不同**（實測：行情 2005-01-03、法人 2012-05-02），不得補值。**建議訓練起點 2015-06-01**——台股漲跌幅當日由 7% 放寬到 10%，之前的資料與 `fill.py` 的漲跌停閘、滑價半價差、宇宙衛生濾網（`max_jump_ratio 0.35`）的假設不同。AC：(1) 8 個資料集 × 3,007 檔抓取完成（約 41 小時，可續傳）；(2) 清洗與六道品質閘門；(3) 載入湖與 PG；(4) 制度變更清單與其對程式碼假設的影響逐條確認；(5) 因子重物化（受 T-083 的 O(n²) 影響，成本 ∝ 日數²）；(6) 重訓並重跑既有候選，與現有結論對照 | D-038 §5 | — | **A** | in_progress |
| T-117 | 【資料 · 中文名編碼損壞】`serving.stock_meta.name` 有 **14 檔**含替換字元 U+FFFD，全部都是含「碁」（U+7881）的名稱——**含 2353 宏碁**，另有 2432 倚天酷碁-創、3046 建碁、6174 安碁、6285 啟碁、6690 安碁資訊、6776 展碁國際、6811 宏碁資訊、6908 宏碁智醫、7794 宏碁通信、8077 洛碁、8111 立碁、8349／8349A。來自原始 TWSE ISIN 擷取的 Big5→UTF-8 轉換；T-111 的 `reconcile_stock_meta` 刻意不覆蓋既有列，故被保留。**只影響顯示**（分析一律以 stock_id 為鍵），但任何對外報表都會印出亂碼。AC：(1) 測試重現（以已知損壞名稱斷言偵測函數會抓到）；(2) 找出 `clean_stock_meta` 的解碼點並修正；(3) 以 FinMind `TaiwanStockInfo` 的名稱修補既有 14 列（該來源名稱正確，T-111 已驗證）；(4) 加一條閘門：`stock_meta.name` 不得含 U+FFFD | — | — | C | done |
| | **完成註記（2026-09-05）**：根因確認——`twse_isin.py` 用 `decode("big5", errors="replace")`，而「碁」（U+7881）**不在標準 Big5 的映射內**（只在 CP950／Big5-HKSCS 擴充裡）。實測「宏碁」的 cp950 位元組 `a7bbf9d6` 用 big5 解得 `宏��`、用 cp950 解得 `宏碁`。`errors="replace"` 讓它靜默替換，是這個缺陷存活四年而無人察覺的原因。**修法**：(a) 新增 `decode_isin_html` 改用 cp950 且 **errors 為 strict**——無法對映即 raise；(b) `clean_stock_meta` 加閘門，中文名含 U+FFFD 即 raise；(c) `reconcile_stock_meta.py` 新增 `--repair-names`，以 FinMind 名稱修補既有列。**14 檔全部修好**（2353 宏碁、2432 倚天酷碁-創、3046 建碁、6174 安碁、6285 啟碁、6690 安碁資訊、6776 展碁國際、6811 宏碁資訊、6908 宏碁遊戲-創、7794 宏碁智新、8077 洛碁、8111 立碁、8349 恒耀、8349A 恒耀甲特），PG 現在 0 檔含 U+FFFD。 | | | | |
| T-118 | 【指標 · 年化分母寫死 252】`backtest/metrics.py:8` 的 `_TRADING_DAYS = 252.0` 是寫死常數（黃金規則 8），且與台股實際約 244 不符——repo 自己的 `MlSplitConfig.days_per_year: 244` 就承認這件事。後果：`years = n/252` 偏小 → **CAGR 系統性高估**（實測 `ml_net_h20_m` 14.57% vs 日曆年化 13.95%），Sharpe 亦被 √(252/244) 放大約 1.6%。所有既有數字同口徑，故相對比較不受影響；但**與外部基準比較時會製造假差距**，尤其當基準序列的列數與策略不同（0050 缺 66 日，見 T-114）。AC：(1) 移入 configs；(2) 決定用交易日數或日曆年數並記入裁決；(3) 一次性重新基準化並記錄新舊對照 | — | — | B | todo |
| T-119 | 【策略 · 宇宙品質濾網】依 **D-059** 事前登記：`UniverseConfig` 新增 `min_roe`，對 `ml_net_h20_m` 加上 ROE > 10% 的硬濾網（外部先驗，取自 finlab 公開文章），其餘逐字相同、不重訓。這是宇宙層唯一沒動過的維度——現有濾網只有流動性、價格、衛生三項。AC：(1) 測試先行（門檻生效、null ROE 一律排除、關閉時逐位元不變、PIT 一致）；(2) 實作於 `universe_ids`（濾網屬宇宙層，且須在因子標準化之前作用）；(3) 跑 1×／0×／1.5×，scope `bt:ml_net_h20_quality`；(4) 對照 D-038 §5 ＋ D-058 新條件寫下判定；(5) 證偽條件：1× Sharpe 改善 < +0.15 即關閉此方向，**不得再掃其他門檻或指標** | D-059 | — | **A** | done |
| | **完成註記（2026-09-05）**：**證偽條件觸發、方向關閉**。1× Sharpe 0.319（母體 0.644，要求 ≥ 0.79）——不是改善不足而是倒退 0.325；CAGR +14.20% → +4.74%。唯一改善是 MDD −40.78% → −38.14%，但不值得（記錄於此以免日後有人只看回撤重開此路）。**機轉已量出**：(a) ROE 在 top-20 之內不帶報酬資訊——通過組平均月報酬 +2.28%、被濾掉組 +2.30%；(b) 但濾網把投組推到模型排名的兩倍深（9.5 → 18.8），20 檔中換掉 9.2 檔。故損失來自**排序稀釋**而非品質維度。可推廣原則：硬濾網只有在其資訊量大於排序稀釋成本時才划算，而稀釋與它移除 top-N 的比例成正比。這也解釋了為何 finlab 的樣本內結論相反——其基礎是排序能力弱得多的純動能。研究登記簿因 T-084 的相對路徑寫進 worktree 分支，已併回主庫（594 → 618 列）。 | | | | |
| T-120 | 【宇宙 · 絕對門檻在長歷史上會漂移】`UniverseConfig` 的三道濾網有兩道半是**絕對值**：`min_adv_twd: 30_000_000`、`min_price: 10.0`，以及 T-119 新增的 `min_roe: 0.10`。實測選擇性已在四年內漂移：流動性砍幅 2022 年 70%、2024 年 60%；ROE 砍幅 31% → 54%。**對 T-116（回補到 2005）是硬問題**——3,000 萬 TWD 在 2005 年與 2026 年是完全不同的門檻（台股成交金額二十年間成長數倍），固定門檻會讓早期宇宙被砍到不成比例地小、且愈早期愈嚴，使「早期 vs 晚期」不可比；而我們正是要用長歷史解 DSR 的統計檢定力問題。對照：finlab 用**相對門檻**（剔除 60 日均額後 50%），定義上不漂移。AC：(1) 量化 2005~2026 各年在固定門檻下的宇宙大小；(2) 裁決改用相對門檻（如成交額百分位）或以通膨/市值縮放；(3) 若改動，既有結論須在新宇宙下重跑對照。**須在長歷史訓練之前決定** | T-116 | — | **A** | AC-1 done（2026-09-05；`scripts/universe_drift.py`，22 年 × 每月取樣年度平均，報表 `docs/reports/universe-drift.md`）。**本任務的前提被自己的量測推翻**：留存率 vs 年份的相關係數 **+0.023**、前 5 年 30.1% vs 後 5 年 30.5%——**沒有早晚漂移**，「以通膨縮放」這條路可以關閉。但量到另一個沒預期到的問題：留存率 vs 當年 ADV 中位數的相關係數 **+0.900**、極差 19.2%~40.1%（20.9pp），**宇宙大小是行情的函數**；壓力年（2008 23.5%、2011 25.9%、2015 22.2%）宇宙比多頭年小四成，而 D-038 §5 正是用最大回撤在判晉升。AC-2／AC-3 轉 **D-062 待裁決** | 
| T-099 | 【因子 · 產業／資金流】(1) `price.industry_mom_6m`（Moskowitz–Grinblatt 1999：所屬產業過去 126 日等權報酬、跳過最近 5 日，指派給成員，+）；(2) `price.industry_rs_20d`（產業 20 日報酬 − TAIEX 20 日報酬，+）；(3) `chip.industry_foreign_flow_10d`（Choi–Sias 2009：Σ產業外資 10 日淨買超 ÷ Σ產業 10 日成交金額，+；需 T-092）。產業別取 `stock_meta.industry`（TWSE 約 30 類）；概念股／供應鏈對照無來源，不做。市場層資金流（整體法人買賣表）無橫斷面差異，只能作擇時 overlay，另立。AC：各因子 `assert_no_lookahead`、手算案例、IC 階梯、與 residual_reversal_5d 對照 | SPEC-002 | T-097（順序）, T-092（因子 3） | B | done（2026-09-03；程式 83f6575，閘門 545 passed）。IC：`industry_mom_6m` 全樣本 −0.005／−0.005／−0.003（t −1.4／−1.5／−0.9）、OOS −0.008／−0.008／−0.005（t −2.1／−2.4／−1.5），價差 +0.26～+0.89% 但不單調；`industry_rs_20d` +0.003／+0.002／−0.006（t 1.1／0.8／−1.8）。**兩者在 5～20 日 horizon 無可用訊號**（論文是月頻 1～12 月），不進 v3；若要用須改月頻或 h≥60 另立裁決 |
| T-100 | 【雲端 · 執行期排除】`cloud.exclude_factors`（T-016 三個分點因子：expanding 統計需全歷史、雲端 broker_trades 只留 10 日）目前只有 test_cloud 把關，**執行期未強制**：GHA daily-pipeline 的 `pipeline --pg-signals` 對雲端 DSN 跑 `materialize_day` 時仍會用 10 日窗算這三個因子並寫入（看似正常的錯數字）。AC：(1) materialize 在目標為雲端時跳過 exclude_factors（需可辨識目標：ServingConfig 加 `target` 或 GHA 設環境變數，不得靠 DSN 字串猜）；(2) 測試斷言雲端目標不計算、本機目標計算；(3) 若依「改地端」方向退役雲端計算，改為雲端只鏡射本機 factor_values 並關閉 GHA 物化 | D-030 + T-016 | — | B | **paused**（使用者 09-03 02:50「這題先暫停」。設計已定：`--target cloud` + `cloud_factor_subset(cfg)`；T-094 子集規則改為「完整包含 composite → 照算、部分包含 → 拒絕」。兩條先行測試存於對話 scratchpad `t100_tests_pending.patch`，恢復時套回 `tests/test_serving_materialize.py`） |
| T-101 | 【維運 · 本機備份例行】D-049 後本機 PG 是唯一研究庫。AC：(1) `pg_dump -n serving -Fc` 週全量 + 每日 `archive-daily`（既有）；(2) rclone 到 Drive（沿 docs/OPS-archive.md）；(3) 一次實際 `pg_restore` 演練到空 schema 驗證可還原；(4) 排程（本機 scheduler 容器或 Windows 工作排程）| D-049 | — | B | 部分完成（**首份全量備份已完成**：`pg_dump -n serving -Fc -Z 6`，02:26→02:34 共 8 分鐘，**2.40 GB**（27 GB 壓縮 11×），`pg_restore --list` 確認含 18 張表資料。落點 `data/backup/twquant_serving_20260903.dump`；還原：`docker run --rm -v D:/project/twquant/data/backup:/backup --network container:twquant-pg-1 -e PGPASSWORD=twquant postgres:16-alpine pg_restore -h 127.0.0.1 -U twquant -d twquant -n serving --clean --if-exists /backup/<dump>`。AC-2 rclone Drive、AC-3 還原演練、AC-4 排程未做） |
| T-102 | 【資料 · 擷取正確性】**官方 T86 擷取產生欄位錯位列**：`institutional_daily` 有 508 列（2022-01-13～2026-09-01 共 41 日、每日 1～55 檔、全為 TWSE 上市股、`available_at` 皆 18:00）與 TWSE T86 原始 JSON 逐列不符，FinMind 508/508 相符。簽名：湖 `foreign_net` = 外陸資＋投信、`trust_net` = 自營商賣出(自行)、`dealer_net` = 自行買賣超——像資料列在「外陸資買賣超」之後多 3 欄、名稱對位錯 3 格；**2026-08-31 仍有 55 列**，每日排程現在還在產。AC：(1) 用 `tests/fixtures/http/` 錄下一個出事日（如 2026-08-31）的 T86 原始回應，重現錯位並定位 `clean.py`／`sources/twse.py` 的原因（欄位對名、CSV/JSON 分歧、多欄列）；(2) 修正後該 fixture 逐列等於原始；(3) 對全 PG 歷史重跑比對（官方 vs FinMind staging，`scratchpad/verify_inst_mismatch.py` 的邏輯進 `data/quality.py`），差異為 0；(4) 品質閘門加「法人三欄與 FinMind 交叉比對」或「三大法人合計 = 三欄和」的每日檢查。已處置：09-03 以 FinMind 值覆寫湖與 PG 各 508 列（`repair_inst_rows.py`，前值留檔 `inst_mismatch_verified.parquet`） | SPEC-002 | — | B | done |
| | **完成註記（2026-09-05）**：**根因不是剖析器**。重抓 2026-08-31 的 T86 原始回應（fixture 存於 `tests/fixtures/http/twse_rwd/institutional_20260831.json`），以現行程式碼剖析後與 FinMind **1,331 檔逐列相符、0 差異**；該回應 19 欄、1,335 列全部長度正確。出事的 41 天在 2022-01~2026-09 之間**分散**而非連續，指向上游暫時性地回傳 fields 與 data 不對應的回應，而非 schema 變更。故 AC-1 的結論是：**沒有可修的剖析缺陷**，防線只能是資料的自我一致性。**AC-4 已落地**：`clean.assert_t86_consistent` 檢查七條 T86 內部恆等式（三大法人 = 四項和、自營合計 = 自行＋避險、各類別買−賣 = 買賣超），實測在 2026-08-31 全 1,335 列成立；以觀察到的真實簽名（欄名正確但值自 index 5 起前移 3 格）重建後，閘門確實 raise。連一條恆等式都驗不了時同樣 raise——安靜地不檢查比沒有閘門更危險。**AC-2／AC-3 通過**：fixture 逐列等於原始；PG 全歷史 vs FinMind staging **重疊 2,511,618 列、值不同 0 列**。 | | | | |
| T-103 | 【資料 · PIT 假設未文件化】財報只有 FinMind 的**最新版本**：湖裡沒有 vintage（第一次公布值 vs 重編後值），而因子以「法定申報期限可見」對映到每個交易日——重編過的季別等於用了後來才知道的數字（輕度前視）。另 `load_finmind_statements.py` 對重疊鍵值變動是硬失敗（raise），下次全量重抓時只要有公司重編就會停在那裡。AC：(1) 在 DECISIONS.md 明確裁決此假設（接受並記錄影響範圍，或改存 vintage）；(2) 載入器對「重疊鍵值不同」區分「映射錯」與「來源重編」兩種情況（後者可帶 `--allow-restatement` 更新並記一筆 ingest_log），不得靜默覆寫；(3) 若接受現況，於 SPEC-002／SPEC-00x 註明財報因子含重編前視 | SPEC-002 | T-097 | B | todo |
| T-104 | 【因子 · lookback_days 宣告不足】`quality.py` 宣告 460／850 天，但 TTM 需 4 季（≈460 天勉強）、SUE 需 13 季（≈1,200 天）、`asset_growth` 需 5 季（≈580 天）。目前 `compute.py` 不讀 `lookback_days`（T-083），故無影響；一旦 T-083 落地做視窗裁切，這些因子會**靜默變 null**。AC：(1) 逐因子重算實際需要的日曆天並更正宣告值；(2) 加測試：以宣告值裁切後的結果與全歷史逐位元相同 | SPEC-003 | T-083 | C | todo |
| T-105 | 【模型 · 第三代候選】D-050：財報品質因子依 R1–R4 規則自動納入 → `configs/ml_h10_v3.yaml` → 訓練 → backfill-scores → ml eval → 回測 1×／0×／1.5× → 逐筆與四段 → DSR。AC：(1) 特徵清單由 `select_v3_features.py` 機械產生，且 `meta.json` 的 features 與配置逐字相同；(2) 1× 與 0× 各登記一筆 `bt:ml_rank_h10`；(3) 對照 v2 與 repaired 逐項；(4) 依 D-038 §5 判定晉升與否並記入 D-050；(5) 無因子通過納入規則時不訓練、不消耗 trial | D-050 | T-097, 鏈 C | A | done（2026-09-04 00:39）：規則自動納入 `fundamental.sue`／`fundamental.roe` → 30 特徵，`lgbm_h10_20260225_a48b262c` OOS RankIC +0.0868（v2 +0.0887、repaired +0.0796）；回測 1× CAGR −4.73%／Sharpe −0.177／MDD −42.3%／周轉 2,803%（0／6），0× +14.97%／0.839（3／6），1.5× −11.85%（0／6）；逐筆 1× EV −0.048%、0× +0.629%；**依 §5 不晉升**，結論記 D-050：特徵層飽和，轉目標函數。保留：D-051 的死特徵是本結論的最大未知數（T-107） |
| T-106 | 【特徵集 · 重複特徵】`price.mom_20d` 與 `price.rs_market_20d` 的**逐日橫斷面 Spearman = 1.000**——後者 = 個股 20 日報酬 − 大盤 20 日報酬，而大盤那一項在同一天對所有股票是常數，減常數不改變排序；模型吃的是橫斷面 z，兩者攜帶**完全相同**的資訊。v2 的 gain 因此被拆成兩半（mom_20d 4.0%、rs_market_20d 3.9%），且 `feature_fraction 0.8` 的抽樣會讓兩者互相稀釋。AC：(1) 以 D-050 的相關性工具對 v2／v3 全特徵集做一次兩兩 |rho| 掃描並列出所有 > 0.95 的對；(2) 開新裁決決定移除哪一個（移除屬規格變更，須另行事前登記，不得混進既有候選）；(3) 若保留 rs_market_20d，改成對**產業**或 beta 調整後的相對強弱才具增量資訊 | D-050 | — | B | todo |
| T-107 | 【標準化 · 稀疏因子被歸零】D-051：`winsorize_mad` 在 MAD = 0（超過一半股票值相同，稀疏因子的常態）時，上下界同為 median → `clip` 把整個橫斷面壓成常數 → z 全為 0。28 個 pin 特徵中 7 個受害，投信兩個 100% 的日子失效，**T-092 補的投信資料從未進入任何一代模型**。AC：(1) 先寫測試重現（稀疏欄位 > 50% 為 0 時，現行實作使 z 全 0）；(2) 依裁決（建議 B：MAD = 0 時跳過截尾）修正，並加測試證明 MAD > 0 時輸出逐位元不變；(3) 對 7 個受影響因子重物化，並比對修正前後的 z 分布；(4) 以修正後資料重跑 v2 與 repaired，確認 T-096 的結論是否改變（投信因子復活後 gain 是否仍為 0） | D-051 | — | A | todo |
| T-108 | 【標準化 · D-051 修法】依 D-052 建議(甲)：稀疏因子改走 **rank／pct 轉換**而非 option B。理由：option B 在 MAD=0 日完全不截尾，|z| 中位約 10、p99 17–25；rank 轉換給出**完全相同的 IC**（Spearman 不變）且無此副作用，且 `factor_values.pct` 欄位已存在。AC：(1) 先寫測試重現現況（稀疏欄 >50% 為 0 時 z 全 0）；(2) 實作並證明密集因子逐位元不變；(3) 重物化 4 個復活因子（`chip.trust_net_10d`／`trust_streak`／`foreign_streak`／`short_margin_ratio`）；(4) 以修正後資料重跑 repaired／v2／v3，確認 D-050 的「特徵層飽和」結論是否改變。**注意** `trust_net_10d` 原始 IC 為 −0.0187（t −13.9），與註冊 direction +1 相反，屬反向訊號——LightGBM 可自行學符號，但若要進線性組合須另立裁決 | D-051, D-052 | T-107 | A | todo |
| T-109 | 【模型 · 事件型因子的標準化】`price.box_breakout_60d`／`hs_top_120d`／`flag_hold_50d` 是**另一個缺陷**（非 D-051）：`materialize` 對它們在僅 5–28 檔的事件窗內做橫斷面標準化，`patterns.py` 註解與 STRATEGY 4b.3 本身就說事件型稀疏因子不該這樣。實測：`box_breakout_60d` 事件內 IC −0.067（t −7.6，流動子集 −0.19）而註冊 direction 為 +1；事件股的 10 日超額 box_breakout +0.17%（t 3.6）、hs_top −0.35%（t −8.1）、flag_hold −0.35%（t −2.5）。AC：(1) 決定事件型因子的表示法（事件旗標 + 事件內排名，或不標準化直接給 0/1）；(2) 修正 direction 與文件；(3) 重物化並重跑 IC | D-052 | — | B | todo |
| T-110 | 【策略 · 唯一未試的組合】依 D-052 建議(乙)：`cost_adjusted_return` 標籤 × 月頻。`configs/strategies/ml_net_h20_m.yaml` = `ml_net_h20.yaml` **只改 `schedule: monthly`**，scope `bt:ml_net_h20_turnover`（不佔 ml_rank 額度），**不需重訓**。依據：ml_net_h20 的 top-20 超額 +0.55%／10 日（現況 +0.08% 的 7 倍）、月頻周轉約 879%（成本 5.7pp／年 vs 現在 21pp／年）；兩者單獨皆已失敗（週頻 Sharpe 0.13、rank 月頻 0.39），組合從未測過。AC：(1) 事前登記後再跑；(2) 回測 1×／0×／1.5×；(3) 逐筆與四段；(4) 依 D-038 §5 判定並記入 D-052。**證偽**：1× Sharpe 仍 < 0.5 → 目標函數×周轉這條路關閉，轉換架構 | D-052 | — | A | done |
| | **完成註記（2026-09-04）**：回測本身早已跑完（結果在 ledger），但**從未對照 D-038 §5 寫下判定**；本次補齊尚未計算的三項（四段／median fold Sharpe／DSR）與線性基準比較，判定寫入 D-052。**九條過七條，卡 MDD −40.78%（門檻 ≤30%）與 DSR 0.173（門檻 ≥0.95）→ 不晉升**。但證偽條件（1× Sharpe < 0.5）**未觸發**（實測 0.644），故該路徑不關閉、(丙) 不啟動。重跑逐項重現 ledger 值，確認決定性。數字取自 T-111／T-098 之前的資料，修後是否成立見 T-112。 | | | | |
| T-111 | 【資料 · stock_meta 缺退市股】D-053：`clean.py:247` 把 `delist_date` 寫死 null，且 `stock_meta` 來源（ISIN）只列現存上市公司，故 **2,056 檔有價格的股票中有 64 檔不在 `stock_meta`**（實測：71 檔最後交易日落在 2020–2025，價格面**有**保留、非存活者偏誤）。依賴 stock_meta 的三個因子（`price.residual_reversal_5d`＝25 個 pin 特徵之一且為 composite 因子、`price.industry_mom_6m`、`price.industry_rs_20d`）對這 64 檔靜默無值，而它們正是後來下市的左尾樣本。AC：(1) 測試重現（造一檔有價格無 meta 的股票，證明三因子靜默略過）；(2) `stock_meta` 以 `prices_daily` 的實際清單為準補列，缺欄留 null；(3) `delist_date` 由最後交易日推導，不再寫死 null；(4) 重物化三因子並比對受影響檔數 | D-053 | — | A | done |
| | **完成註記（2026-09-04）**：閘門全綠（557 passed）。**AC-3 實作與原文不同**：不用「最後交易日推導」，改用 FinMind `TaiwanStockDelisting` 的權威下市日——兩者實測差 5～218 天（2499 最後交易 2020-04-06、實際下市 2020-11-10），推導值會讓下市前的停牌期被當成仍在交易。**AC-2 有一項未達成**：`market` 是必填欄位，FinMind 無市場別的 6 檔（1333／3452／3562／4947／8287／8913）**不補列**，不得偽造。結果：stock_meta 2,010 → 2,085 檔（補 75，其中 52 檔有產業別）、`delist_date` 0 → 72 檔有值；寫入前驗證 **0 檔在其下市日之後仍有成交**（中位數差 −9 日）。AC-4 抽樣十日：三因子每日約 1,300 檔中新增最多 39 檔（早年較多），補回的正是後來下市的左尾。**全因子重物化與模型重跑另記於 T-112**（`residual_reversal_5d` 是 composite 因子，子集模式被拒）。 | | | | |
| T-077 | 【閘門覆蓋範圍】`python -m twquant check` 的 ruff 只跑 `_TARGETS`（`src`／`tests`），**`api/` 不在範圍內**——故 `api/index.py:19` 已無作用的 `# noqa: E402`（bare `ruff check` 報 RUF100）從未被閘門攔到。這不是紅燈基線，是**覆蓋缺口**：部署到 Vercel 的進入點不受 lint 保護。AC：決定 `api/` 是否納入 `_TARGETS`（納入則一併清掉該 noqa）；若刻意排除，在 `check` 的 docstring 註明理由 | CLAUDE.md §2 | — | C | todo |
| T-073 | 【研究可重現性】`make_model_id` 的 fingerprint 只含 ML/cost config，**不含程式碼版本**：缺陷修正後重訓產生同一 model_id，而 `save_model` 為冪等覆寫 → 靜默「同一 id 對應不同模型」，破壞 SPEC-011 §3 的可重現性保證。AC：fingerprint 納入標籤/訓練程式碼版本（或內容雜湊）；既有 id 不變或提供遷移；覆寫既有 artifact 時 raise 而非靜默覆蓋 | SPEC-011 §3 + D-040 §5 | T-072 | B | done |
| | **完成註記（2026-09-04）**：閘門全綠（584 passed，+6 條測試）。**AC-1 的實作與字面不同，理由如下**：AC 說「fingerprint 納入程式碼版本」，但 `training_config_hash` 同時是 `research_log.params_hash`（`train.py:172`），也就是 **DSR 的 trial 計數依據**。把程式碼版本塞進去，等於每修一次缺陷就消耗一個 DSR 名額——而 trial 計的是「假設」，維護不是新假設（T-078 已記錄同型缺陷：hash 對向後相容的 schema 新增不穩定，使 trials 虛增）。故改為：新增 **`artifact_fingerprint` = 配置 × 程式碼 × 資料**，記在 `meta.json`，由 `save_model` 在「同 id 不同 fingerprint」時 raise。`make_model_id` 與 `params_hash` **完全不變**（AC-2「既有 id 不變」因此自然滿足）。`training_code_hash` 用**內容雜湊**而非 git SHA：worktree 有未提交改動時 SHA 會說謊，而「同 id 不同模型」正是在改到一半重訓時發生的。涵蓋 `dataset.py`／`splits.py`／`train.py`；因子程式不在其中——特徵來自 `factor_values`，那屬於**資料**版本。`training_data_hash` 逐列雜湊特徵×標籤寬表，能分辨「形狀相同但值不同」（stock_meta 補列與除權息還原正是這個形狀）。**五個凍結 artifact 沒有 fingerprint**，故對它們重訓會 raise 並要求先封存——這是刻意的：無法證明是同一個模型就不得覆寫。附帶事實：那五個的 model_id 今天已與其配置產生的 id 不符（加了 `total_return` 欄位），本來就已與配置脫鉤。 | | | | |
| T-074 | 【研究登記簿正確性】`ml eval` 沒有 `--config`，一律載入預設 `configs/ml.yaml`：(a) `cli.py:2272` 的 `trial_scope` 寫成 `ml:h{預設horizon}`，`train.py:178` 有 `net` 分支而 eval 沒有 → H20 的 params_hash 誤登進 `ml:h10`（實測：`ml:h10` 含 2 個 distinct hash，`trials_count` 虛增，`ml:net_h20` 少一筆）；(b) `nw_lag` 用預設 horizon 而非模型 horizon，重疊標籤下高估 t 值。AC：eval 接受 `--config` 或改讀 meta.json 的模型配置；scope 與 nw_lag 皆由模型決定；補測試涵蓋 net/rank 兩路徑；既有誤登以文件標註扣除，不竄改登記簿 | SPEC-010 §4 + D-038 §2 + D-040 §8 | T-072 | B | todo |
| T-075 | 【前視正確性】embargo 差一天：`splits.py:60` 的 `train_end = usable[test_lo - embargo_days - 1]`，而標籤跨度為 `open(t+1)→open(t+1+h)`，故最後一列訓練標籤的出場價索引 = `test_lo - embargo + h`；當 `embargo == horizon`（`configs/ml.yaml` 10/10 與 `configs/ml_h20_cost_aware.yaml` 20/20 皆為最小值）該索引 = `test_lo`，即**測試期首日開盤價**。SPEC-010 §3 與 `config.py:185` 驗證器只要求 `>= horizon`，正確要求為 `>= horizon + 1`。影響約佔訓練列 0.06%（每窗 1 個訊號日 × 宇宙），不足以解釋 IC 校準落差，但屬真實洩漏。AC：驗證器與 SPEC 同步改為 `>= horizon + 1`；補測試斷言最後訓練標籤的出場日嚴格早於 test_start | SPEC-010 §3 | — | B | todo |

> ML 任務（T-065~T-070）源自 [D-036](DECISIONS.md)（橫斷面 GBDT 排序 meta-factor），
> 規格見 [SPEC-010](docs/specs/SPEC-010-ml.md)、研究依據見研究報告「台股 ML 研究藍圖」
> （artifact，2026-08-29）。T-065~T-069 純研究軌不動生產路徑；T-070（接入每日
> pipeline）被 T-069 的部署裁決擋住，評估階梯任一級輸給既有線性基準即整包止步。

> 雲端搬遷任務（T-053~T-062）源自 [D-030](DECISIONS.md)（GHA cron + Supabase Free
> 滾動窗口 + Drive raw 歸檔 + Vercel）。分四期：Phase 0（T-053/054，歸檔保命線——
> 做完本機才有資格關機）、Phase 1（T-055~058，GHA pipeline + 平行對帳）、
> Phase 2（T-059~061，Vercel 前端/API）、Phase 3（T-062，切換退場）。
> 待裁決 D-031（回測範圍）/D-032（GHA 分鐘）/D-033（Drive 容量）不擋 Phase 0/1 動工。

> 供應層任務（T-031~T-037）源自 [PRD-001](docs/PRD-001-stock-page.md)、規格見
> [SPEC-009](docs/specs/SPEC-009-serving.md)。分三期：Phase 1（T-031~034，現有資料全上
> + PG 底座）、Phase 2（T-035，籌碼補全）、Phase 3（T-036/037，主力因子與持股深化）。

> 依賴修正（REVIEW H15）：T-003 改以靜態日曆檔起步（不再假依賴 prices_daily）；
> T-005 依賴補上轉接器；T-023 的 report 步驟改為掛點預留、T-024 接上（解除 T-023↔T-024
> 循環）；T-025 補上 T-015/T-016。表格順序依實際依賴重排（T-004/006/007-010 先於 T-003/005）。

## 平台重構工單（T-200～T-215）

使用者於 2026-09-06 指定此佇列依序實作，驗收後合併 main。原平台文件 T-152 改號 T-200，避免與 ML 研究混用。本段為平台工單狀態的唯一來源。

| ID | 任務 | 規格 | 依賴 | 模型 | 狀態 |
|---|---|---|---|---|---|
| T-200 | 【文件】專業平台資訊架構與前端交接：每日 ML 名單、訓練歷程、回測中心、產業／個股洞悉、版本追溯、K 線推薦日、API 草案與分期驗收；延伸 PRD-002 | [PRD-003](docs/PRD-003-platform-console.md)；D-080 | 使用者本輪指定；文件交付不依賴新訓練 | — | done（2026-09-05；純文件與示意互動預覽，不代表平台已實作。DoD：修改前 fast 709 passed／23 skipped；追加 K 線設計後完整 check：ruff、format、mypy 全綠，pytest 709 passed／23 skipped；CHANGELOG 已更新，diff 僅規劃文件。無新增公開程式介面／計算參數／golden 變更，功能 AC-1～11 留待各實作任務。預覽使用 Lightweight Charts 5.2.0，已驗證日 K／成交量、推薦日期、盤後與最早交易日、標記切換、事件點選與鍵盤定位、縮放重設、行情表、切股隔離與套件失敗狀態；原導覽／搜尋／追蹤流程通過，七頁在 1024／736／360px 深淺色均無頁面溢出或執行錯誤） |

| T-201 | 平台導覽、深連結與響應式殼層 | [SPEC-014](docs/specs/SPEC-014-platform-console.md) §2 AC-201-1～3 | T-200 | — | in_progress |
| T-202 | 模型／回測 artifact 索引與安全讀取投影 | [SPEC-014](docs/specs/SPEC-014-platform-console.md) §3 AC-202-1～2 | T-201 | — | todo |
| T-203 | 研究索引唯讀 API 與錯誤契約 | [SPEC-014](docs/specs/SPEC-014-platform-console.md) §3 AC-203-1 | T-202 | — | todo |
| T-204 | 模型歷程與回測中心接線／圖表 | [SPEC-014](docs/specs/SPEC-014-platform-console.md) §3 AC-204-1～2 | T-203 | — | todo |
| T-205 | ML 推薦快照合約、保存與匯入 | [SPEC-014](docs/specs/SPEC-014-platform-console.md) §4 AC-205-1～2 | T-204 | — | todo |
| T-206 | 每日 ML 名單與首頁、篩選與歷史 | [SPEC-014](docs/specs/SPEC-014-platform-console.md) §4 AC-206-1～2 | T-205 | — | todo |
| T-207 | 個股研究分頁、選股分離與日期身分 | [SPEC-014](docs/specs/SPEC-014-platform-console.md) §5 AC-207-1～2 | T-206 | — | todo |
| T-208 | K 線推薦日標記、定位與事件詳情 | [SPEC-014](docs/specs/SPEC-014-platform-console.md) §5 AC-208-1～2；PRD-003 §14 | T-207 | — | todo |
| T-209 | 產業研究頁與來源／覆蓋呈現 | [SPEC-014](docs/specs/SPEC-014-platform-console.md) §6 AC-209-1 | T-208 | — | todo |
| T-210 | 洞悉證據包驗證與分析閱讀頁 | [SPEC-014](docs/specs/SPEC-014-platform-console.md) §6 AC-210-1 | T-209 | — | todo |
| T-211 | 本機自選與研究筆記、舊資料相容 | [SPEC-014](docs/specs/SPEC-014-platform-console.md) §6 AC-211-1 | T-210 | — | todo |
| T-212 | 資料後台與工作中心操作重構 | [SPEC-014](docs/specs/SPEC-014-platform-console.md) §6 AC-212-1 | T-211 | — | todo |
| T-213 | 日頻 ML 推論輸出推薦快照 | [SPEC-014](docs/specs/SPEC-014-platform-console.md) §7 AC-213-1 | T-205；既有研究推論入口 | — | todo |
| T-214 | 洞悉特徵與成熟 outcome 回訓匯出契約 | [SPEC-014](docs/specs/SPEC-014-platform-console.md) §7 AC-214-1 | T-210；標籤／資料成熟度 | — | todo |
| T-215 | 整合驗收、操作文件與 main 合併 | [SPEC-014](docs/specs/SPEC-014-platform-console.md) §7 AC-215-1 | T-201～T-214 | — | todo |

## 完成紀錄

- **T-069 未結項查明：smoke 0 成交＝衛生濾網誤判農曆年休市**（2026-08-29）
  根因在 `strategy/universe.py:46`——日曆缺口條件用**曆日**判斷
  （`max_gap_days` 預設 10）。台股農曆年封關是全市場休市（2025 最後交易日 1/22
  → 開紅盤 2/3，間隔 **12 曆日 > 10**），使**每檔股票同一列同時**被標 `bad`；
  再經 `:47-54` 的 `cum_sum` − `shift(60)` 黏 60 個交易列，`:60` 的
  `filter(~_badw)` 讓整個宇宙連續 60 個交易日為空。
  量化（以 oos_scores 的 233 個交易日實算）：死區＝**前 58 個交易日（24.9%）**、
  首個乾淨日 **2025-05-02**、有效交易日 **175**、50 個訊號日只剩 **38 個**有效
  （38 × 20 ≈ 760，與回報 814 筆成交吻合）；smoke 的 25 天整段落在死區內，
  故成交 0／未成交 0。
  **對第 3 級數字的修正**：CAGR +23.71% 實際在 175 個有效日內取得；年化周轉
  1820% 以 `n_days=233` 計，**有效期重算約 2423%**；對照組同樣受影響，
  故 ML vs 對照仍同基準、**結論方向不變**。若部署，每年春節後會靜默空手約 3 個月。
  **所有 PG 回測共通，非 ML 專屬**；依 §6 不夾帶在 T-069 修，已開 **D-037** 待裁決
  （建議改用交易日曆判缺口，修完重跑第 3 級再進 DSR，重跑算一次新 trial）。
  附帶發現：`backtest/engine.py` 的 `_execute` 對「查無價格」與「未達整股」
  的訂單**靜默丟棄**（既不記成交也不記未成交），違反黃金規則 5、現有測試未覆蓋
  ——獨立的真 bug，應補 `Unfilled(reason=...)`，另立任務。

- **T-069 第 3 級的回測資料修剪：正確性分析**（2026-08-29，供後續接手）
  為繞過 `universe_ids` 逐訊號日全表重算的效能問題，回測前把 PG `prices_daily`
  修剪到「回測起日 −120 曆日」。逐一追過依賴後確認**不影響回測準確度**：
  - `ml.lgbm_rank_10d`（受測策略唯一訊號）`lookback_days=0`、只讀 ml_scores，不碰 prices。
  - `fundamental.revenue_yoy` 雖宣告 `lookback_days=430`，但 YoY 值直接取自
    `monthly_revenue.yoy`（該表未修剪），price spine 僅決定輸出日期集合 → 不受影響。
  - 硬約束是 `universe_ids` 的衛生濾網 60 個交易日（＋20 日 ADV）。實測 120 曆日窗
    （2024-10-08~2025-02-05）＝ 87 平日 − 國慶/元旦/農曆年 ≈ **78 個交易日**，
    邊際 18 天；rolling_median(20) 與 shift(60) 的取值皆落在保留範圍內，
    結果與完整表相同。
  ⚠ **脆弱之處**：以曆日換算交易日是不穩的假設，遇長年假或補班會使邊際趨近 60。
  若日後重用此修剪手法，應改為交易日精確定界：
  `SELECT min(date) FROM (SELECT DISTINCT date FROM serving.prices_daily
   WHERE date < :bt_from ORDER BY date DESC LIMIT 90) t`。
  根治仍是讓 `universe_ids` 只取 `signal_date − 回看窗` 的切片（所有 PG 回測共通，另立任務）。

- **T-069 階梯第 1-2 級（部分）**（2026-08-29）ML vs 基準對照＋決定性實測。
  `ml/evaluate.py`（daily_rank_ic / ic_summary 含 Newey-West / decile_table /
  equal_weight_signed，12 測試）＋ CLI `ml eval --model`。**實測（同 43.9 萬筆、
  同標籤、inner join 同宇宙）**：ML RankIC +0.0814（IR 0.974、勝率 87.6%、
  NW t 6.76、分位嚴格單調、頭尾 +0.086）vs 全因子等權基準 −0.0182（IR −0.167、
  勝率 41.2%、NW t −1.09、不單調、頭尾 −0.021）→ 差 +0.0997。
  **決定性（AC-4）真實資料實測通過**：獨立重跑重現同一 model_id 與 IC 至小數
  第 10 位。⚠ **但基準太弱**：全因子等權正是 D-020 已裁決「無預測力」的
  composite，打贏它不足以宣稱增值——**須補 D-020 實證有效子集**
  （reversal_5d / residual_reversal_5d / revenue_yoy 等權，即 serving.yaml
  composite_factors）**為第二基準，判定取最強基準**，才算第 1 級真正通過。
  第 3-4 級（成本後回測、DSR）未動。視覺化見 artifact「首訓成績單」。

- **T-069 階梯第 1 級通過**（2026-08-29）補上 D-020 有效子集為第二基準後重跑。
  三方對照（43.8 萬筆共同樣本、同標籤、inner join 同宇宙）：
  | | RankIC | IR | 勝率 | NW t | 分位頭尾 | 嚴格單調 |
  |---|---|---|---|---|---|---|
  | ML（LightGBM） | +0.0815 | 0.975 | 87.6% | 6.76 | +0.0860 | 是 |
  | 基準A 全因子等權 | −0.0183 | −0.168 | 41.2% | −1.09 | −0.0209 | 否 |
  | 基準B D-020 有效子集 | +0.0369 | 0.375 | 70.0% | 3.15 | +0.0380 | 否 |
  判定對最強基準（B）：**ML 勝出，RankIC 差 +0.0446（2.2 倍）、分位頭尾差 +0.0481**。
  基準B 本身確實有效（t 3.15 顯著）——D-020 篩選正確，ML 是在有訊號的對手上勝出，
  且僅 ML 分位嚴格單調。**第 1、2 級通過**；第 3 級（成本後回測，需以
  `ml.lgbm_rank_10d` 建策略 YAML 接回測引擎，重點看換手率與成本後淨值）與
  第 4 級（DSR，trials 取 research_log）未動，仍不得部署。

- **T-069 階梯第 3 級：成本後回測（通過，但保留）**（2026-08-29）受測策略
  `configs/strategies/ml_rank.yaml`——刻意「純」：唯一訊號 `ml.lgbm_rank_10d`
  權重 1.0、top_n 20、週頻 MON 換股、next_open 成交、單檔上限 5%。分數來源是
  walk-forward 的**樣本外**分數（`ml backfill-scores` 寫入 ml_scores）——用最終
  模型回頭對全期推論會是前視。對照組 `reversal_revenue.yaml`，同期間同成本。
  區間 2025-02-05~2026-01-12（233 個交易日，＝ ml_scores 樣本外範圍）：
  | | CAGR | Sharpe | Sortino | Calmar | MDD | 年化單邊周轉 | 成交筆數 |
  |---|---|---|---|---|---|---|---|
  | ML（ml_rank） | **+23.71%** | **+1.27** | +1.45 | +1.34 | −17.68% | **1820%** | 814 |
  | 對照（reversal_revenue） | −8.90% | −0.42 | — | — | — | 2659% | — |
  **成本後仍為正、且大幅勝過對照**——第 1、2 級的 IC 優勢確實穿透了交易成本
  （含 0.3% 證交稅），不是紙上訊號。
  ⚠ **保留而非無條件通過，兩點**：
  （1）**年化單邊周轉 1,820%**＝每年換手約 18 次、平均持有約 13 個交易日
  （與 10 日標籤視窗一致，屬結構性而非設定失誤）。成本已扣仍賺，但這使淨值對
  費率假設極度敏感——費率若上修，優勢會等比例被吃掉。降周轉的手段（分數 EMA
  平滑、buffer-zone 換股）**都要另行驗證並計入 DSR 的 trials 預算**，不得事後挑
  最好看的參數。
  （2）**一個月 smoke 回測產出 0 成交／0 周轉／0 報酬，全期卻有 814 筆成交**——
  尚未查明。不推翻全期結果（全期本身自洽），但在部署前必須解釋清楚，列為 T-069
  的未結項。
  第 4 級（DSR，trials 取 research_log）與第 5 級（金庫期最終評估）未動，
  **仍不得部署**。視覺化見 artifact「首訓成績單」的「成本後回測」面板。

- **T-067 首訓實測**（2026-08-29）`ml train` 首次真實資料訓練成功（GHA run #5，Drive
  base dump 還原 → walk-forward）。模型 lgbm_h10_20260112_2f5a21a9：訓練 2023-01-03~
  2026-07-29、25 特徵、4 窗、train_end 2026-01-12（金庫期未動）。**樣本外實測**：
  RankIC +0.0814（233 日、每日約 1,882 檔、43.9 萬筆）、逐日勝率 87.6%、月勝率 12/12、
  逐窗 IC 全正（+0.118/+0.055/+0.090/+0.054）、十分位嚴格單調（頭尾 +0.086）。
  誠實標註：樸素 t=14.87 但 10 日標籤重疊（lag1 自相關 0.72），**Newey-West t=6.76**
  為準。**尚未對照 zscore_weighted 基準**（階梯第 1 級未完整通過），成本後回測與
  DSR 未做——不得據此宣稱模型有效。五次重跑的教訓依序為：整表拉 2,300 萬列 OOM →
  逐因子 server 端過濾；標籤宇宙含權證 → 四碼過濾；factor_values 歷史洞 → 逐月密度
  判定；判洞閾值誤殺春節月 → 閾值改 <10。視覺化報告見 artifact「首訓成績單」。

- **T-065**（2026-08-29）資料深度審計（SPEC-010 §2 回填）。方法：一次性 GHA workflow
  ——Drive base dump（twquant-full-20260729，1.4 GB）還原拋棄式 PG（免索引、排除可
  重算表）→ scripts/audit_data_depth.py 盤點 → 報告 artifact。發現：訓練期定案
  2020~今 6.5 年（prices 1,592 交易日；train_min_years=2.0 維持）；特徵覆蓋分層
  （價量/營收 6.5 年全覆蓋、法人系 2024~2025 起、富邦分點系僅 2026 起——raw
  broker_trades 有 2022 起 4.5 年，物化未回跑，歷史 chip 回補另立任務）；
  stock_meta 無已下市股票（生存者偏誤——訓練宇宙改以 as-of 行情+流動性建構，
  不依賴主檔）；Drive 歸檔鏈完整（base 1 + daily 29 日無缺）。首跑磁碟爆滿
  （broker_trades 55M 列建 PK）→ 清預裝包＋--section 免索引修復。審計 workflow
  用畢已刪；腳本留 scripts/ 供再用。

- **T-068**（2026-08-29）ml_scores 資料合約＋薄因子（SPEC-010 §5/§6）。schemas.ML_SCORES
  （pandera，17 表普查同步）＋serving DDL（PK (model_id, stock_id, date)）＋
  serving.yaml cloud.windows ml_scores:250；factors/ml.py `ml.lgbm_rank_10d`
  （registry 新增 ml 類別；前綴過濾＋同 (date,stock) 取字典序最新模型；過
  assert_no_lookahead）；dataset.cross_rank_pct（null 傳遞）；data/serving/
  ml_store.upsert_ml_scores（寫前 pandera 驗證、ON CONFLICT DO NOTHING 冪等）；
  CLI `ml score` 補 rank_pct/model_id/available_at＋`--pg` 直寫。修正
  `_ml_features_resolved` 排除 ml.* 自身（自我參照洩漏）。測試 7 條
  （AC-6/7/9＋前綴去重＋rank_pct 手算）。閘門：374 passed / ruff / mypy 全綠。

- **T-067**（2026-08-29）ML 訓練/推論 CLI（SPEC-010 §1/§4）。configs/ml.yaml＋
  MlConfig（extra=forbid；embargo≥horizon 驗證；年/月→交易日換算入配置）；
  ml/train.py（fit_ranker 決定性旗標固定注入、walk_forward_train、oos_rank_ic、
  save_model model.txt+meta.json、log_training_run→research_log）；ml/score.py
  （load_model 斷言 train_end<as_of、latest_model_dir）；CLI `ml train`/`ml score`
  （PG serving 源：factor_values.z 特徵＋prices_daily.open 標籤）。測試 12 條
  （AC-4 決定性/AC-5 反前視/AC-8 登記/配置驗證）。閘門：367 passed / ruff / mypy
  全綠。SPEC-010 §1 同步 days_per_year/days_per_month 欄。

- **T-066**（2026-08-29）ML 資料集組裝＋purged walk-forward 切分器（SPEC-010 §2/§3）。
  `src/twquant/ml/`：splits.walk_forward_splits（擴張窗、embargo、金庫期、壞輸入
  raise）、dataset.forward_open_returns（open(t+1)→open(t+1+h)、停牌順延、尾端 null
  保留列）、excess_rank_labels（同日超額→百分位；rank 下超額不變性）、assemble
  （outer join 寬表、缺值留 null 禁填 0）。測試先行（A 級）：T-066-t 凍結 19 測試
  （含 hypothesis 50 例屬性測試）後實作，實作 commit 不含 tests/；ml/dataset.py、
  ml/splits.py 入 IO 靜態閘門。閘門：355 passed / ruff / mypy 全綠。
  依賴註記：T-065 僅供 `split.train_min_years` 配置值，切分器本身與期間無關。

- **T-064**（2026-07-30）個股因子頁重新設計：元件化＋mobile-first＋終端風精煉。
  App.tsx 2,968→1,546 行：跨頁共用層抽出 `theme.ts`（調色/格式）、`fallback.ts`、
  `hooks.ts`（MetaCtx/useResource/useMediaQuery）、`ui.tsx`（Panel/Kpi/Skeleton/chipCls）、
  `charts.tsx`（CandleChart/RangeCandle/EquityChart/IndexLine/HBars——原封搬移，其餘頁面
  外觀不變）；個股頁拆進 `src/stock/`（StockPicker/FactorScreener/StrategyPools/
  StockHeader/StockChartPanel/DataPanels/ScorecardPanel/index＋store）。
  **mobile-first**：殼層手機 sticky 頂欄＋橫向捲動頁籤（側欄 md 起顯示）、chip 列手機
  單行橫捲（ChipRow＋`.hscroll`）、搜尋框滿版、圖表高度隨斷點（useMediaQuery）、
  體檢卡條圖改 div 佈局 `ZBars`（修正 SVG viewBox 在手機縮放後文字不可讀）、
  法人/融資券與營收/主力改成對 grid（lg 兩欄、only-child 佔滿）。
  **終端風**：TPanel（方角、accent tick 標題列、header 分隔線、sub 截斷防溢出）、
  方角 chip、傾向改 [強順]/[中性]/[逆勢] 括號樣式。
  **行為不變**：localStorage 三鍵沿用（watchlist/subFactors/chartSettings）、D-020
  「非預測訊號」標示三處保留、useResource 不快取語意與 OHLC 換股殘影防護不變。
  閘門：oxlint 0 警告、`tsc -b` 綠、vite build 綠（57 模組）；Playwright 煙霧：
  離線快照＋mock live API 兩模式 × 手機(390px)/桌面(1280px) 視窗零 pageerror、
  八頁巡檢零錯誤。後端零變更（`git diff` 僅 frontend/）。

- **T-056（部分完成）**（2026-07-29）雲端每日 pipeline 首跑成功——**本機零參與**。
  卡關與排除：repo 的 GitHub Actions 原為**停用**狀態（run 排隊 4 小時 55 分、
  `jobs.total_count=0`；與分鐘數耗盡症狀不同——後者有 job 但分配不到 runner），
  `gh api -X PUT .../actions/permissions -F enabled=true` 後恢復。
  **獨立驗證**（非僅看 step exit code）：
  - Drive `daily/2026/20260729/` 11 檔齊全（broker_trades 395 KB ← 雲端確實抓到分點）
  - Supabase：prices @07-29 **1,958 檔**（雲端跑含上櫃，較本機當時 1,363 完整）、
    factor_values @07-29 **30,882 列**、非四碼 0 列（修剪生效）、prices 維持 260 交易日
  - 總量 **467 MB**（前一日 446 MB）。⚠ 距 Free 500 MB 僅餘 33 MB，且 PG 的 DELETE
    不還空間給 OS（死 tuple 計入 Supabase 用量）→ 見 D-034，prune 後可能需 VACUUM。
  **待**：cron 自動觸發（非手動 dispatch）驗收一次。

- **T-055**（2026-07-29）Supabase 滾動窗口建置。`config.CloudConfig`（dsn_env/
  stock_pattern/pattern_tables/windows，serving.yaml `cloud:` 段）；schema 新表
  `broker_flow_daily`（券商母公司日聚合，衍生表；本機已回填 2025-07 起 502k 列）；
  `data/serving/cloud.py`：`prune`（第 N 新 distinct date 為 cutoff，DELETE + pattern
  過濾，冪等）、`refresh_broker_flow`（upsert 聚合）、`seed_tables`（跨 PG COPY 串流，
  TRUNCATE 重灌冪等）；`store.connect_cloud`（pooler `prepare_threshold=None`）；CLI
  `serving prune / broker-flow / seed-cloud`。測試 4 條（窗口 ≥ 因子最大回看靜態把關
  ——混用未修剪表者由該表驅動回看、prune 冪等、聚合手算對數、seed 窗口＋四碼過濾）。
  閘門：ruff/format 綠、mypy src 89 檔綠、pytest 全套 354 綠。**實戰紀錄**：首灌
  725 MB 觸發 Supabase Free 唯讀保護＋DiskFull；教訓（1）TRUNCATE 須獨立 commit 否則
  隨失敗 COPY 回滾（2）超量後 session `SET default_transaction_read_only='off'` 可解。
  過渡縮窗（factor 20 日/composite 120 日，D-034）後 **446 MB**：prices 260 日四碼
  497k 列/75 MB、broker 明細 10 日 541k/84 MB、broker_flow 250 日 479k/61 MB、
  factor 20 日 700k/105 MB、法人/融資券/月營收/集保全史。

- **T-054**（2026-07-29）每日增量歸檔（D-030 保命線）。`data/serving/archive.py`：
  `dump_day`（ARCHIVE_SPECS＝鏡射表＋fubon_fetch_log/screener_daily＋stock_meta 全量
  快照；因子表可重算不歸檔；COPY ORDER BY 主鍵＋session UTC＋gzip mtime=0 → 同庫
  狀態重跑 bit-identical；tmp+replace 原子覆蓋）、`restore_day`（staging COPY →
  ON CONFLICT DO NOTHING 首錄保留，冪等）。CLI `archive-daily`/`archive-restore`
  （預設 `<data_dir>/archive/daily/<YYYY>/<YYYYMMDD>/`）。測試 3 條（AC-1 兩日兩目錄、
  AC-2 重跑 bit-identical、AC-3 還原進空 schema＋重複還原冪等；真 PG、無 DSN skip）。
  閘門：ruff/format 綠、mypy 僅 D-028 既有基線、pytest 全套綠。真 PG 煙霧：2026-07-23
  單日 64,766 列 → 525 KB（broker_trades 53,089 列 → 354 KB）。**維運註**：增量必須在
  serving sync 之後跑（否則歸檔到空切片）；Drive 上傳由 GHA（T-056）/runbook 負責。

- **T-053（部分完成）**（2026-07-29）全史歸檔保命線。本機 PG 15 GB →
  `pg_dump -Fc -Z 6` 1.3 GB（4 分鐘）落 `data/archive/`；還原演練（臨時
  postgres:16-alpine 容器、`pg_restore -j 4`）9 表列數與 dump 時刻基準**精確吻合**
  （broker_trades 55,340,144、factor_values 22,914,835、prices_daily 9,069,140…全表
  見 docs/OPS-archive.md §3）。runbook `docs/OPS-archive.md`（dump/還原/rclone 上傳/
  增量佈局）。**待使用者**：`rclone config create gdrive drive scope=drive.file`
  （OAuth 需本人操作）後 `rclone copy` 上傳 base dump，即全部完成。

- **T-052**（2026-07-22）期末持倉批次明細。engine.py：`_Lot`（entry_date/
  cost_shares/cost_amount/min_close）——首買建批、加碼併入均價、部分出場不變均價、
  歸零或 D-029 強制出場即移除；每日收盤更新 min_close（進場日含開盤與當日收盤）。
  `Holding` 加 entry_date/avg_cost/ret/max_loss（MAE=持有期最低收盤 vs 均價，≤0）；
  snapshot holdings 加 entry/cost/ret/max_loss；前端期末持倉表加買進日/均價/報酬率
  （紅綠著色）/最大虧損欄。測試：首買日=該股首筆買入日、均價介於買入價區間、
  ret 恆等式、MAE ≤ 0。閘門全綠（pytest 全套）。部署待 merge+rebuild。

- **T-051**（2026-07-22）回測期末持倉顯示。engine.py：`Holding` dataclass ＋
  `BacktestResult.final_holdings`（期末股數/最後價/市值/權重，市值降冪；SPEC-004 §4
  持倉快照之期末切片，逐日快照仍待補、SPEC 已註記）。snapshot.backtest_result 加
  `holdings` 欄；前端策略回測頁加「期末持倉」表（labelOf 中文名對照、市值總計）＋
  底部說明改動態策略描述。測試 +1（市值加總=期末 positions_value、整數張、權重和
  ≤1、降冪）。驗證：dev 雙服務端到端——sample 策略期末 19 檔完整渲染。
  部署待 merge+rebuild。

- **T-049**（2026-07-22）Sleeve C 上畫面。api.py：`/api/strategies`（列
  configs/strategies/*.yaml，壞配置標示不擋清單）＋ `/api/backtest` 加 `strategy`
  參數（防路徑跳脫、404）＋ PG 資料源（PgTableSource cache＋since=start−420d
  下限讀取；psycopg.OperationalError 時依 fallback_duckdb 降級注入資料湖）；
  pg_view.py 加 `since`（information_schema 判 date 欄）；snapshot.backtest_result
  放寬 TableSource。前端：策略下拉（fetchStrategies）＋重跑帶策略＋底部說明改動態
  策略描述。驗證：worktree API :8001＋vite :5174 端到端——`/api/strategies` 列出
  三策略、UI 選 reversal_revenue 重跑（159 筆、17.4s，與 curl 一致）。測試 +3
  （策略清單、未知策略 404、路徑跳脫 404）。**部署待 PR merge + docker rebuild**。
  防衛版引擎數字（含 T-047 防衛）：reversal_revenue 週頻全期 CAGR −20.5%
  （幻影 +70.9% 已清除）、月頻變體 −17.0%——排名式表達買到「相對最慘」而非
  「剛崩」且持有語意錯配，事件級 edge（64% 勝率）的正確表達開 T-050。

- **T-047**（2026-07-22）D-029 引擎防衛（裁決 B 部）。engine.py：`_fill` 開盤 vs 前收
  跳價 > jump_ratio（0.35，configs/costs.yaml `data_guard`）→ unfilled reason
  `price_jump`；持倉 close 跳價 → `_forced_exit` 以 min(跳價前, 跳價後) 全數出場
  （幻影跳價不入損益，會計恆等維持）。strategy/universe.py：衛生濾網——近
  hygiene_lookback（60）列內相鄰列跳價 > max_jump_ratio 或日曆缺口 > max_gap_days
  （10）→ 排除（UniverseConfig 新欄，可 None 停用）。測試 ×3（跳價拒單、強制出場
  價 162 手算、宇宙排除）。閘門：ruff/format 綠、pytest 347 passed、mypy 僅 D-028
  既有基線。治本（品質閘門/停牌標記/還原價）在 T-048。

- **T-046（部分完成）**（2026-07-22）反轉×營收策略正式化（Sleeve C 原型）。
  工程（done，閘門綠 344 tests）：`configs/strategies/reversal_revenue.yaml`
  （reversal_5d 0.7 × revenue_yoy 0.3、週一再平衡、top20 等權）；回測引擎 PG 路徑——
  calendar.load 加 TableSource 鴨型分支（+測試）、PgTableSource 加 cache 模式
  （回測逐訊號日重算，每表只讀一次）、engine/build/calendar 型別放寬 TableSource、
  CLI backtest `--pg/--no-pg`（預設 PG）。
  引擎全期回測（2021-07~2026-07）**數字作廢**：CAGR +70.9% 經診斷為停牌跳空幻影
  （單日 +301%、前 10 大單日貢獻 +813%、獲利前三檔交易間隔 574~1,274 天、
  已實現損益合計 −338 萬）→ 記 **D-029 待裁決**（品質閘門/還原價/停牌標記為
  回測硬前置＋引擎缺口防衛）。
  事件級乾淨驗證（日曆間隔防衛剔除停牌單，僅 5%）：5日跌≥15% 全部桶 20 日淨勝率
  60.6%/中位 +3.5%；**＋營收YoY>0：64.0%/中位 +5.0%（n=6,753）**——訊號本身成立，
  組合級績效待 D-029 資料修復後重測；walk-forward 同步遞延。

- **T-045**（2026-07-21）旗形有撐因子（使用者提出：整理→急拉→再整理→整理有撐才進場）。
  patterns.py：`_flag_hold_events` 四段因果偵測（base 20 根寬 ≤10% → pole ≤10 根
  +20%（P=近 20 根 close 峰、S=P 前最近滿足漲幅者=最短 pole）→ flag 5–20 根回撤
  ≤50% → 成立日=近 3 根觸旗形低點 ±2% 支撐帶且當日收回 +1% 上方）＋
  `price.flag_hold_50d`（value=拉升幅度×(1−回撤)）。測試 +5（含反前視五切點）。
  順帶修 T-013 遺留：`rank_ic_series` 對「當日因子值全同」的 NaN corr（非 null）
  會毒化 IC 均值 → 加 is_finite 過濾＋測試（稀疏事件因子必踩）。
  實證（2020-01~2026-07 全市場）：RankIC 5/10/20d = −0.021/−0.039/−0.060
  （t=−1.6/−2.9/−4.7，方向與宣稱相反）；事件 1,287 件、超額中位 −0.1%~−0.5%、
  逐筆淨勝率 37–42%（中位 −1%）→ 寫入 STRATEGY §4b.4，不進策略。
  閘門：ruff/format 綠、pytest 342+6 passed、mypy 僅 D-028 既有基線紅。

- **T-044**（2026-07-20）ic-report PG 讀取路徑（D-026 補完）。cli.py：ic-report 加
  `--pg/--no-pg`（預設 PG），比照 serving materialize 以 `PgTableSource` 餵 `TableSource`
  介面，因子計算路徑不變。真實資料上線抓到兩個問題並修正：(1) serving 鏡射的
  prices_daily 有 low=0 停牌列 → 型態因子除零；`_price_panel` 比照 price.py 的 > 0
  防衛剔除非正價列（補測試）。(2) `limit_up_price` 在 serving 鏡射 905 萬列**全 null**
  → 事件研究「成立日漲停占比」原實作誤報 0%；分母改「有旗標資料的事件」、全缺 → NaN
  （補測試）。漲停旗標缺料是資料層既有缺口（影響 SPEC-004 回測旗標判定，會走 proxy），
  另列追蹤。閘門：ic 相關 tests 綠、cli.py ruff/format/mypy 綠。

- **T-043**（2026-07-20）型態因子補全＋事件研究（STRATEGY §4b + D-027）。
  patterns.py：候選/掃描泛化（`_Candidate` fire/invalid 閾值 + `_scan_events`）＋
  `price.hs_top_120d`（連續三樞紐高點、中峰 ≥ 兩肩×1.02、兩肩差 ≤3%、頸線＝兩谷均值、
  跌破成立、漲破中峰作廢，dir=−1）＋ `price.box_breakout_60d`（截至 t−1 的 60 根箱體
  寬 ≤12%、close 突破箱頂×1.005，值＝突破幅度；polars 滾動向量化＋冷卻期）。
  ic.py：`event_starts`（連續交易日輸出段取首日）＋ `compute_event_study` 純函數
  （樣本數、事件後 5/10/20d 相對宇宙中位數超額均值/中位數、勝率、成立日漲停占比）；
  CLI `ic-report --event-study` 印出並以 es_* 指標落地 research_log；SPEC-002 §4 同步。
  相關矩陣檢查（vs 52週高/動能）為既有 IC 報告內容，於真實資料跑報告時執行。
  測試 +8（tests/test_pattern_factors.py 頭肩頂/箱型 6 條含各自 assert_no_lookahead、
  tests/test_ic.py 事件研究 2 條手算對數）。閘門：ruff/format 綠、pytest 337 passed、
  mypy 唯一紅燈仍為 D-028 既有基線（test_screener.py，與本任務無關）。

- **T-042**（2026-07-20）型態因子基元＋首發（STRATEGY §4b + D-027）。
  `factors/patterns.py`：`confirmed_pivots` 樞紐基元（嚴格高/低於前 k、不劣於後 k、
  同值取最早；確認日 = p+k，t 日只用確認 ≤ t 的樞紐）＋ `price.double_bottom_120d`
  （雙低容差/最小間隔/頸線＝兩低間最高樞紐高點/深度下限/跌破作廢/突破成立，
  成立日起 active_days 輸出型態深度；事件稀疏、無型態不回傳列）。掛入 builtins。
  測試 8 條（tests/test_pattern_factors.py）：樞紐確認與同值 tie-break、成立日＝突破日、
  截斷視圖部分輸出、容差/間隔拒絕、平坦序列空輸出含 schema、assert_no_lookahead
  五切點。閘門：ruff/format 綠、pytest 329 passed、mypy 對本任務模組零錯——唯一紅燈
  為 HEAD 既有 tests/test_screener.py 3 錯（stash 驗證與本任務無關，記 D-028 待裁決）。

- **一鍵跑當日全部資料（追加 T-023/T-024 進度，非新任務）**（2026-07-15）使用者需求：
  排程（`scheduler` 每日 16:40）若錯過觸發（機器離線、當日忘開），供應層/選股池快照
  就停在舊日，只能敲 CLI 補跑。資料後台加「一鍵跑當日全部資料」面板，一鍵在伺服器端
  跑 `twquant pipeline`（＝ scheduler 同一 `_run_daily_pipeline`：擷取全市場行情＋法人＋
  融資券＋… → 品質閘門 → 訊號 → 供應層 → 選股池快照）。
  - **後端**：`monitoring/jobs.py` 加 `resolve_trading_day`（None/空＝今日台北、否則驗 ISO）、
    `pipeline_args`（`pipeline --date <day>`）、`pipeline_label`；`POST /api/pipeline-jobs`
    （`PipelineJobRequest.date` 選填）。與一鍵補抓共用同一 `JobManager`（單一併發——pipeline
    內含擷取，不得與補抓並跑；工作出現在同一 `/api/ingest-jobs` 清單供輪詢）。
  - **前端**：`PipelinePanel`——watchdog 橫幅（heartbeat 日 vs 最新行情日，標示排程是否錯過/
    從未執行）＋主動作「▶ 一鍵跑今日全部資料」（date 省略、後端補台北今日）＋次動作
    「補跑此日」（指定交易日，供連假後補多天）。「補抓工作」面板改名「背景工作」（現含
    pipeline 工作）。
  - **閘門**：ruff check/format 全綠；mypy strict 86 檔全綠；pytest `test_ingest_admin.py`
    28 passed（+5：`pipeline_argv_and_label`/`resolve_trading_day`/`api_post_pipeline_job_starts`
    /`_bad_date`/`_busy_409`——後三者以假 MANAGER/monkeypatch 攔截，不真的 spawn 擷取）；
    frontend oxlint＋tsc＋vite build 綠。
  - **實機煙霧**：`twquant serve` + vite dev；資料後台頁 `PipelinePanel` 渲染正確（空湖 →
    「⚠ 尚無 heartbeat」橫幅、一鍵/補跑鈕就位）；`POST /api/pipeline-jobs {date:"nope"}` → 400、
    端點在 OpenAPI schema。happy-path spawn 未實跑（＝既有一鍵補抓同一 `MANAGER.start`，已生產驗證）。
  - **券商買賣超納入 pipeline（同需求續，使用者裁決「併入共用 pipeline」）**：反映
    「沒有跑券商買賣超跟因子」——查證因子物化本就在供應層步驟（materialize_day），
    券商買賣超原僅獨立 `backfill-fubon-chip`。抽 `_run_fubon_day`（單日 best-effort：
    非互動、skip_existing 續傳、假日探測跳過），`_run_daily_pipeline` 加「券商買賣超」
    步驟（品質閘門後、選股池快照前，五池依賴 broker_trades）。一鍵＋16:40 排程皆抓；
    PG 未啟用/不可達或來源失敗 best-effort 略過。閘門：ruff/format、mypy strict(86)、
    pytest 63（test_cli+pipeline+ingest_admin+fubon_chip；+1 `_run_fubon_day` 週末短路離線測）；
    frontend oxlint＋build 綠。

- **T-040 + T-041（策略選股池：篩選器＋每日快照回溯·done）**（2026-07-14）
  **出處**：主力買賣超研究（本日 session；樣本 2022-02~2026-06、隔日開盤進場、
  20 日均額 ≥ 20M、對全池超額、NW 日聚合 t）。五池＝逐年穩定的高勝率條件：
  主力 60 日累積買超前 10%（5/5 年正）、主力 10 日集中×YoY≥20%（勝率 48.5% 全表最高）、
  多頭排列×YoY≥20%（超額 +1.14% 全表最大）、三面向共振、主力 60 日累積賣超後 10%
  （警示，−0.71% t −3.4）。
  - **T-040**：`monitoring/screener.py` 純函數（daily_metrics/assign_pools/add_streaks/
    build_payload）＋ PG 讀取；閾值入 `configs/screener.yaml`（`ScreenerConfig`）；
    `GET /api/screener?n=`；前端「個股因子」頁新面板（池 chips＋成員連N日＋點擊選股）。
    YoY 取 ≤ 當日最近已物化值前向沿用（物化落後行情日的常態）。
  - **T-041**：`serving.screener_daily`（PK date/pool/stock_id，DO NOTHING＝首錄保留）；
    CLI `screener-snapshot [--from --to]` 整段單次回算批次寫入（回補 2026-06-01~07-14
    共 14,643 列、重跑冪等）；`?date=` 快照優先（snapshot=true）、無快照即時回算；
    前端日期選擇＋「已存快照/即時回算」標示。API 維持唯讀，寫入只走 CLI。
  - **閘門**：ruff check/format 全綠；mypy strict 85 檔全綠；pytest 全套綠（含
    `tests/test_screener.py` 10 測試：AC-7~14 ＋配置載入；AC-12 為 PG 整合、無 DSN skip）。
    frontend oxlint＋tsc＋vite build 綠。
  - **真 PG 煙霧**：as_of=2026-07-14 宇宙 1,039 檔、五池 104/41/87/24/103；
    ?date=2026-07-01 讀快照（宇宙 1,073）、?date=2026-03-02 即時回算（宇宙 934）；
    瀏覽器實測面板渲染/池切換/點擊選股/日期回溯皆過。
  - **維運註**：`check` 內建 pytest 在本機因 Windows 長 tmp 路徑失敗（既知環境問題），
    以 `uv run --all-groups pytest --basetemp=/c/t/pt` 等效跑全套。

- **排程改跑完整 pipeline（追加 T-023 進度，非新任務）**（2026-07-14）`scheduler`
  常駐迴圈原本每日觸發只裸呼叫 `_ingest_date`（僅擷取進資料湖，供應層鏡射/物化
  仍須手動 `pipeline`/`serving materialize`）。抽出共用 `_run_daily_pipeline(day)`
  （`pipeline` CLI 與 `scheduler` 迴圈皆呼叫同一份步驟：擷取→品質閘門→訊號生成→
  供應層→**選股池快照**新尾波），使每日 16:40 觸發即完成到選股池快照，不再需要
  手動補跑。供應層/選股池快照步驟皆 best-effort（PG 未啟用或不可達時該步回報略過，
  不中斷擷取/訊號主線；單日整體例外排程迴圈照樣續跑下一觸發時刻）。
  **仍未做**（T-023 原範圍，維持部分完成）：LOOP.md §3 的多波次擷取（16:30/21:45
  分波）、report 步驟掛點。
  **驗證**：`python -m twquant pipeline --date 2026-07-14` 手動跑通，選股池快照
  step 回報「359 列」；重跑同日期驗證冪等（PG 列數不變）。ruff/mypy/pytest 全套
  （含既有 `test_pipeline.py`）綠——`run_pipeline`/`PipelineStep` 框架未變、只是
  CLI 層新增一步與重構呼叫路徑，無獨立 CLI 整合測試（比照既有 `pipeline` 指令本身
  亦無 CliRunner 測試的慣例）。

- **T-038（綜合分預測力研究 + B/C 落地·done）**（2026-07-11）D-020 全程。
  **研究**：PIT-safe 重跑（worktree 21 因子、資料湖 2023-01~2026-07、IS/OOS 切分）證實等權
  綜合分樣本外無力（5d IC=−0.0047 t=−0.88、20d t=+0.09），忠實重現回報。三方對照——**B 有效
  子集**兩 horizon OOS 皆過 D-006 promote（5d t≈8–9、20d t≈3–5）；**A 滾動 IC 加權** 20d OOS
  從 IS t=8.63 崩到 t=0.04（過擬合）。報告 [docs/research/COMPOSITE-IC-D020.md](docs/research/COMPOSITE-IC-D020.md)。
  **裁決 B+C**（D-020）並落地：
  - **B**：新增純函數 `data/serving/composite.py::composite_from_signed`（子集 signed_z 均值＋
    宇宙百分位，單一真相源），`materialize.py`（PG 物化）與 `monitoring/snapshot.py`（live 現算）
    共用。子集入 `configs/serving.yaml.composite_factors`＝{reversal_5d, residual_reversal_5d,
    revenue_yoy}（黃金規則 8，不寫死）＋ `ServingConfig.composite_factors`。factor_values/
    cross_stats 仍寫全因子（體檢卡展示），僅 composite 用子集。SPEC-009 §3 同步。
  - **C**：前端三處標示「非預測訊號」——體檢卡綜合分說明、K 線 P8 綜合分位副圖 sub、
    宇宙熱門列 label tooltip（`frontend/src/App.tsx`）。
  - **閘門**：ruff＋ruff format＋mypy(strict, 83 files) 全綠；pytest 266 passed / 11 skipped
    （+5 新 `tests/test_serving_composite.py` 子集斷言；`test_serving_materialize.py` 補 composite=
    子集均值斷言）＋ 1 pre-existing 環境失敗（`test_api_stock_meta_and_factors`，本機 venv 無
    libpq/PG DSN，git stash 驗證 baseline 同樣失敗、非本次回歸）。frontend oxlint＋tsc＋vite build
    成功（42 模組；本機 node_modules 曾殘留 lightweight-charts v4，`npm install` 同步回 lock 之 v5，
    未改 package.json/lock）。**真實湖 smoke**：2330 綜合分 signed_z_mean=0.03＝子集三因子均值
    （mom_20d 0.42 仍展示於 14 因子但不入綜合分）。
  - **上線注意**：既有 `composite_daily` 為舊全因子物化 → 需
    `serving rebuild --tables composite_daily`（或重跑 materialize 區間）才反映子集；docker 前端
    需重建映像才顯示新標示。

- **T-035（主力券商買賣超·部分完成）**（2026-07-10）富邦DJ 券商(母公司)買賣超進 PG + 個股頁面板。
  `broker_trades` 直接列入 serving 鏡射表（每檔每日 top-15±、量小，不另做預聚合）；
  `stock_api.brokers`：近 window 日彙總各券商淨買賣超 → 買/賣超前 15 + 每日家數，PG 優先降級
  資料湖；端點 `/api/stocks/{sid}/brokers`。前端 P6「主力券商買賣超」面板（買/賣超前 10 兩欄）。
  **閘門**：ruff+mypy(124)+pytest 250 passed（broker 彙總測試）；frontend oxlint+build 過。
  真實驗證：2330 近 5 日買超前 3 券商 +5.84M/+3.73M/+2.36M 股。**進行中**：全市場 2026-07-08
  爬取（~1800 檔、1 req/2s、背景）。**待接**：集保 P5、主力集中度因子（T-036，需 broker ≥60 日
  史 + T-016）、爬取排程、真分點來源決策（富邦DJ 為母公司層級的先天限制）。

- **T-034（前端個股頁·部分完成）**（2026-07-10）「個股因子」頁在 K 線與因子體檢卡之間加三面板：
  法人買賣超（外資日買賣超棒＋累計線）、融資券（融資餘額線＋日增減棒＋券資比）、月營收
  （營收棒＋YoY 線），皆讀 T-033 端點（/institutional·/margin·/revenue，envelope 含 source，
  PG 空或失連自動降級資料湖）。`api.ts` 加 v2 型別與 fetcher；`App.tsx` 加 `MiniTS`（棒＋疊線
  inline SVG，紅漲綠跌）與三面板元件，無資料則不顯示（上櫃法人/融資券缺口優雅留白）。**閘門**：
  frontend oxlint 綠、`tsc -b`＋vite build 成功（42 模組）；後端 249 tests 不受影響。**待接**：
  P7 因子 sparkline/點擊展開、P8 綜合分位趨勢（/composite-history·/factors/history 端點已備）、
  Header overview 面板、抽出 frontend/src/stock/ 元件目錄、P5 集保/P6 分點（Phase 2）。
  **上線提醒**：docker 常駐服務仍為舊映像；`docker compose build api frontend && docker compose
  up -d api frontend` 後 :8899 才會出現新面板（serving 空時走資料湖，仍有真實法人/融資券/營收）。

- **T-033 / SPEC-009（個股 API v2）**（2026-07-10）個股頁 8 端點 + PG 讀取 + DuckDB 降級。
  `monitoring/stock_api.py`：overview/ohlc/institutional/margin/revenue（時序，PG 空或失連即
  降級資料湖、回應標 `source`）+ composite-history/factors-history（PG 物化，無則留白）+
  scorecard 改讀 factor_values（PG）否則現算（snapshot），payload 形狀不變（AC-4）。衍生欄
  伺服器端算：外資/投信累計淨買超、券資比/日增減、營收 MoM、52 週位置。psycopg 延後 import
  （api.py 於頂層 import stock_api，CI 無 serving 群組仍可）。configs/serving.yaml enabled→true
  （PG 不可達自動降級）。`monitoring/api.py` 加 8 路由 + `_stock_ep` 錯誤包裝（非法代號 400、
  其餘 503）。**閘門**：ruff+mypy(124)+pytest 249 passed（AC-4 計分卡形狀相容、AC-5 降級標
  duckdb、AC-6 累計/券資比/MoM 正確、source=="pg" 對真 PG）；CI 路徑（無 psycopg/PG）mypy 綠、
  PG 整合測試 skip。**待接（T-034）**：前端個股頁八面板改版讀這些端點。

- **T-032 / SPEC-009（因子物化）**（2026-07-10）PitView → factor_values/factor_cross_stats/
  composite_daily。schema 加三張因子表（factor_values PK(factor,stock_id,date)＋索引(factor,date)、
  factor_cross_stats、composite_daily）；TableSpec 索引一般化（`indexes` 取代 date_index）。
  materialize.py：對每個 as_of 用 PitView 對因子宇宙（四碼普通股）算全部註冊因子 →
  winsorize/z-score → signed_z ＋ 綜合宇宙分位 → upsert（橫斷面演算法與 snapshot scorecard
  一致，一天算一次落地取代每請求重算）；materialize_day / materialize_range（逐日推進 as_of
  維持 PIT）。sync.upsert staging 表帶表名，一交易可連續寫三表。CLI `serving materialize
  --date | --from/--to`；每日 pipeline 尾波加「供應層」步驟（sync＋物化，best-effort，未啟用/
  PG 不可達即略過，display-only 不影響訊號）。**閘門**：ruff＋mypy(122) 全綠、pytest 240 passed
  （AC-3：factor_values.value 與 PitView 現算一致 1e-9、逐日各自成列）。**真實湖 smoke**：物化
  2026-07-07/08 各 ~32k 因子列/19 因子/~1955 檔，2 日 21.4s；單股因子計分卡查詢 6.0ms（2330
  19 因子含 high_52w pct 0.96、投信連買、綜合分位 0.506）——取代舊全宇宙重算。**待接（T-033）**：
  個股 API 改讀這三張表 ＋ DuckDB 降級。

- **T-031 / SPEC-009（PG 供應層底座）**（2026-07-10）Parquet 湖 → PostgreSQL 衍生讀取層。
  `ServingConfig`（configs/serving.yaml；`pg_schema` 欄名避開 pydantic BaseModel 遮蔽）；
  docker `pg` 服務（postgres:16-alpine、named volume、對外埠 5433、pg_isready healthcheck、
  對 api/scheduler 軟依賴）；`data/serving/`：schema.py（`TableSpec` 單一真相 → 六張鏡射表
  ＋ sync_state DDL，冪等＋識別字防呆）、store.py（psycopg 連線／DSN 解析／ping，唯一
  import psycopg 處）、sync.py（watermark 增量 COPY→temp staging→ON CONFLICT upsert、
  snapshot 表全量、rebuild、status）；CLI `serving sync/rebuild/status`。IO 邊界靜態測試
  擴充：純運算層禁 import psycopg 與 twquant.data.serving/monitoring.pg_store（AC-7）。
  psycopg 進選用 `serving` 依賴群組＋mypy override，無 PG 的 CI 靠 importorskip 跳過整合
  測試仍綠（AC-8）。**閘門**：ruff＋mypy(strict, 120 files) 全綠、pytest 239 passed
  （AC-1 DDL 冪等、AC-2 sync 增量/冪等對真實 PG 執行）。**真實資料湖 smoke**：全六表鏡射
  5.24M 列（prices 4.82M/762 日、法人 184k、融資券 183k、營收 20k、集保 50k、meta 2010），
  單股查詢 5.4ms（2330 近 3 日）——取代舊 scorecard 全宇宙重算。**待接（T-032/033）**：
  factor 物化＋每日 pipeline 尾波自動掛點、個股 API 改讀 PG。**註**：`twquant check` 包裝在
  本機 Windows 因 pytest temp symlink 清理 PermissionError 誤報，各階段以獨立 basetemp 分開
  驗綠；A 級「測試先行」與實作於同一 session（非分離 session），如實記錄。

- **T-003 / T-024（官方開休市日曆）**（2026-07-10）到證交所抓官方「市場開休市日期」，補上
  現行交易日曆「只由 prices_daily 觀測推導、無法前瞻」的缺口。來源 `data/sources/twse_holiday.py`
  （TWSE OpenAPI `holidaySchedule`，端點無年度參數、只回當前公布年度）；`clean_market_holidays`
  解析民國封裝碼（"1150101"→2026-01-01），**closed 分類**＝名稱含「開始交易/最後交易」者為
  交易日註記（closed=False），其餘休市——此端點夾帶「農曆春節前最後交易日」等純交易日列，全
  當休市會誤刪真實交易日（清洗層 + 測試明確涵蓋）。schemas 新增 `market_holidays`（參考表，
  無 date 欄→`_all` 單分區、無 available_at，比照 stock_meta/trading_calendar）。`calendar`：
  `official_calendar`＝涵蓋年度平日 − 官方休市（前瞻日曆，不需先有行情）、`cross_check_official`
  官方 vs 觀測缺口核對（只在官方涵蓋年度內比對）。CLI `ingest-holidays`（抓→存→印近期休市 +
  核對）。UI：資料後台「抓官方開休市日曆」一鍵鈕（重用補抓工作進度）+「官方開休市日曆」面板，
  `GET /api/holiday-calendar` + `snapshot.holiday_calendar`。**刻意不改** `calendar.load()` 的
  持久化權威（官方全年含未來日期，若直接取代會讓 ingest_matrix 顯示未來空格）；官方日曆以顯式
  API + 核對提供，待日後 matrix 夾到當日再併為權威。閘門全綠（mypy strict、frontend
  oxlint+build）＋實抓驗證（2026：243 開市日、休市 24／交易日註記 3、官方 vs 觀測一致）。
- **T-024（手動抓取歷史資料）**（2026-07-10）資料後台加「主動指定期間抓取」入口：現有
  一鍵補抓只由漏抓清單驅動，而漏抓只在既有涵蓋範圍內偵測——空資料湖初次建置、或往前
  延伸歷史過去只能敲 CLI。後端 `POST /api/ingest-jobs` 本就吃任意 (table, source, from, to)
  白名單，故本次幾乎純前端：`monitoring/api.py` 加 `GET /api/ingest-tracks`（回
  `jobs.fetchable_tracks()`＝與補抓同一白名單，unit=date/month 決定輸入粒度）；前端
  `App.tsx` 加 `FetchForm`（資料集下拉＋起迄，日/月輸入，D-015 大區間 400→confirm 帶 force
  重送），重用既有補抓工作進度面板（running/success/failed＋輸出尾行）。台股行情為全市場
  擷取，抓完某段期間後於「個股因子/市場 K 線」頁即可查任一檔歷史 K 線。**修正**：`needs_force`
  在空資料湖（無交易日曆）會 500（`cal.load` 去查不存在的 prices_daily）→ 改以區間平日數
  估交易日，讓初次建置也套 D-015 護欄且不再 500。閘門全綠（214 tests、mypy strict、frontend
  oxlint+tsc+build）＋空資料湖端到端手驗（抓 2026-07-08 全市場、job success、個股 2330 K 線
  可查）。tests/test_ingest_admin.py 補白名單/端點/空湖護欄三測。
- **T-024（一鍵補抓）/ D-018**（2026-07-09）資料後台從「唯讀 + 複製指令」加上伺服器端
  執行：`monitoring/jobs.py`（`build_args` 由缺口識別 (table,source,from,to) 白名單推導
  twquant 子指令 argv——非白名單直接拒、`needs_force` 對 TWSE 行情大區間套 D-015 護欄；
  `JobManager` 單一併發＋輸出尾行緩衝＋最近 20 筆歷史）。`monitoring/api.py` 加
  `POST /api/ingest-jobs`（409 已忙／400 白名單或 D-015）＋ GET 清單/單筆輪詢，CORS 放行
  POST。前端漏抓清單每列加「一鍵補抓」鈕（warn_ban 先 confirm 再帶 force）、新增「補抓工作」
  面板（狀態＋輸出尾行、3 秒輪詢，工作結束自動刷新矩陣/缺口）。指令由子行程走同一
  `python -m twquant`，與手動 CLI 同路徑。閘門全綠（218 tests、mypy strict、frontend
  oxlint＋tsc）。tests/test_ingest_admin.py 補白名單/併發/護欄/端點測試。
- **T-010（擷取層可插拔化）/ D-017**（2026-07-07）SOURCE 與 STORAGE 雙插件化 + TDCC
  股權分散。`data/ingest/`：ports（唯寫 `IngestSink`＝Warehouse 零改動結構滿足；自足
  `SourcePort`；`FetchCleanSource` 包裝既有來源零改寫）、registry（`SourceSpec`/register/
  `list_specs(cadence)`，比照 factors）、orchestrator（`run_ingest` 只依賴埠、假 sink/來源
  可測）。`cli._ingest_date` 改註冊表驅動；新增 `ingest-dispersion`（週頻）。首個來源
  `TdccDispersionSource`+`clean_shareholding_dispersion`：available_at=公布週六12:00（含
  PIT 前視測試）、16/17 剔除。**發現**：opendata id=1-5 覆蓋 6 碼證券（ETF/債券，實測
  50250 列/3350 檔），四碼普通股集保分散需 TDCC 互動站（未來一檔+一筆註冊即可加，正是
  可插拔設計目的）。讀取/PitView 側不動。閘門全綠（194 tests）＋真實資料湖驗證落地。
- **T-024（擷取後台）**（2026-07-07）資料擷取監控後台（唯讀）：`monitoring/snapshot.py`
  加 `ingest_matrix`（dataset×交易日 → present/missing/failed/none）、`ingest_gaps`（缺口
  歸併連續區段＋建議補抓 CLI 指令＋D-015 ban 警示）；`monitoring/api.py` 加 GET
  `ingest-matrix`/`ingest-gaps`/`heartbeat`（即時不快取，ops 新鮮度）。前端 App.tsx 加
  「資料後台」頁（涵蓋熱力圖＋漏抓清單＋複製指令，運維色盤 green/amber/red、30 秒輪詢）。
  補抓仍走 CLI（D-016 唯讀原則不變）。閘門全綠（179 tests、ruff/mypy、frontend oxlint）＋
  對真實資料湖手驗（正確標出 TWSE 2026-01~06 缺口與 backfill ban 警示）。tests/test_ingest_admin.py。
- **T-024（監控儀表板部分）/ D-016**（2026-07-07）監控整併：退役 Streamlit
  `monitoring/dashboard.py`，改「React 前端 + 唯讀 FastAPI」。`monitoring/snapshot.py`
  （API 與 export_snapshot 共用的快照建構單一真相源）、`monitoring/api.py`（GET
  health/snapshot/stocks/stocks·{sid}/backtest/ingest-status；重運算以最新行情日快取；
  個股代號白名單防 SQL 注入）、CLI `serve`。前端即時 fetch `/api`、不可達退回內嵌
  data.json；補回 Streamlit 專屬互動（前 150 檔個股選單、擷取失敗明細、回測期間重跑）。
  docker-compose `dashboard`→`api`、frontend nginx 反代 `/api`；pyproject
  `dashboard`(streamlit)→`api`(fastapi/uvicorn)。閘門全綠（168 tests）＋對真實資料湖
  跑 `twquant serve` 手驗全端點與前端即時渲染/離線 fallback。
- **T-029 / T-024（部分完成）**（2026-07-06）容量分析 + 漂移偵測。
  backtest/capacity.py：estimate_capacity（目標權重 × 20 日 ADV × 參與率上限 →
  容量上限 + 瓶頸標的）；CLI capacity 實測 sample 策略 ~0.8 億元（瓶頸小型股）。
  monitoring/drift.py：tracking_error（影子 vs 實盤日報酬差的滾動年化標準差）、
  drift_check（> 3% 門檻→breached，供 SPEC-005 狀態機轉 REDUCED）。閘門全綠
  （152 tests）。**待接**：日報組裝+推播、告警通道、連 5 日→HALTED 的計數落地、
  衝擊函數精算、儀表板容量/漂移面板。
- **T-027 / T-028（部分完成）**（2026-07-06）洞悉層功能 + 提案生命週期（SPEC-008
  §2/§4）。insight/prompts.py（凍結 system 模板 + PROMPT_VERSION，資料進 user turn）、
  daily_brief.py / explainer.py / advisor.py（純運算：組 payload→client→結構化輸出，
  離線→None AC-6）。reporting/proposals.py（save/load/list/update + apply_changes
  套 yaml_path 變更；no_action 不落地 AC-7；狀態機驗證）。CLI insight-brief（離線
  自動降級不報錯）、proposal list/validate/approve——validate 套提案參數跑回測、
  Sharpe 不劣於基準才 validated 否則 rejected_by_gate（機器閘門 AC-3），approve 人工
  核准。閘門全綠（147 tests）。**待接**：advisor 真實輸入組裝（歸因/regime）、
  rejected 回饋進下次輸入、paper 觀察期狀態轉移。
- **T-026**（2026-07-06）洞悉層 LLM 基座（SPEC-008 §3）。insight/config.py
  （LlmConfig：模型路由 + token→TWD 成本，模型字串只在 configs/llm.yaml）、
  insight/schemas.py（Proposal/DailyBrief/Explanation pydantic 輸出模型）、
  insight/client.py（InsightClient：路由、離線降級 AC-6、日預算閘 AC-4、schema/呼叫
  連 2 次失敗→None AC-1、backend 可注入使測試不打網路；真實 anthropic backend 用
  messages.parse + adaptive thinking + prompt cache 依 claude-api skill）。
  reporting/llm_log.py（用量記錄，IO 層——insight 屬純運算層不得寫檔，IO 邊界測試
  已驗）。configs/llm.yaml、insight 選用依賴群組（anthropic）。閘門全綠（141 tests）。
- **T-023（部分完成）**（2026-07-06）每日 pipeline + heartbeat/watchdog（LOOP §3）。
  pipeline.py：run_pipeline（步驟編排、關鍵步驟失敗→degraded 不產生新訊號、
  skip_if_degraded）、write/read_heartbeat（日期+git hash 原子寫）、watchdog_check
  （缺失/過期→非 ok，dead-man switch）。CLI pipeline（擷取→品質閘門含訊號前
  latest==今日斷言→訊號生成）、watchdog。實測 2026-06-30：4 表→gate→40 檔目標→
  heartbeat。閘門全綠（135 tests）。**待接**：多波次時點細分、PSI 因子分布告警、
  catch-up 補跑、排程器接 pipeline。
- **T-021**（2026-07-06）執行層（SPEC-006）。backtest/fill.py：抽出成交原語
  decide_fill，回測引擎與 PaperBroker 共用（AC-1 兩邊不分家；回測 132 tests 全綠
  證明重構無行為改變）。execution/broker.py：Order/OrderAck/Position/Fill、Broker
  Protocol、PaperBroker（decide_fill 成交、idempotency AC-2、HALTED→raise AC-6）。
  execution/daily.py：preflight_check（部位/現金不符→中止 AC-3）、build_orders、
  reconcile（券商多出成交→凍結 AC-5）、run_session 編排。閘門全綠（132 tests）。
  **待接**：ShioajiBroker（T-022，實單雙開關）、分批下單、盤中限價保護。
- **T-020（部分完成）**（2026-07-06）投組層（SPEC-005）。portfolio/：config
  （PortfolioConfig/RiskConfig）、core（combine_sleeves+歸因 AC-1、per_stock_cap、
  turnover_cap AC-4、條件式 vol_scale AC-2、四態 kill-switch evaluate/apply
  AC-5/6、build_book）。twquant/state.py：狀態 JSON 原子寫 + fail-safe（缺失/損毀
  →HALTED）。configs/portfolio.yaml、risk.yaml。閘門全綠（123 tests）。**待接**：
  AC-3 產業上限（無 industry 資料）、AC-7 跨模組波動只縮一次（需全 pipeline golden）、
  EWMA 波動估計接實際權益序列。
- **T-019**（2026-07-06）驗證工具（SPEC-004 §5）。backtest/validation.py：
  walk_forward（連續互斥無縫測試窗，AC-9）、deflated_sharpe（DSR，用試驗 SR 變異數
  估期望最大 SR，REVIEW M11）+ deannualize_sharpe、bootstrap_sharpe_ci（決定性 seed）、
  param_plateau（因子 window 掃描找高原）。閘門全綠（114 tests）。
- **T-014（部分完成）**（2026-07-06）價量因子。price.high_52w（52週高接近度）、
  price.reversal_5d（5日反轉，dir=-1）、volume.vol_ratio_5d（量比）、
  volume.turnover_z_20d（量能 z）。反前視全綠（109 tests）。真實 IC（連續期）：
  reversal_5d 5日 t=-6.0 確認反轉（dir=-1 正確）；量能因子 5日偏負（放量後短期
  反轉）。**待接**：本地 SMB/HML/市場因子（需市值/在外股數），故中性化基準未齊。
- **T-018（部分完成）**（2026-07-06）回測引擎（SPEC-004）。backtest/：costs
  （台股檔位表、價格相依半價差 REVIEW M1、漲跌停 proxy、手續費含最低/證交稅/
  adv_impact 滑價）、engine（next_open 執行、整數張、先賣後買現金約束、漲跌停與
  參與率 unfilled、會計恆等 AC-6）、metrics（CAGR/Sharpe/Sortino/MDD/Calmar/
  周轉/勝率）。CLI backtest + configs/costs.yaml。ACs 2/3/4/5/6/8 通過（104 tests）。
  **真實回測**：sample 策略在連續期 2025-08~2025-12（104 交易日）成本後
  Sharpe 1.12、CAGR +8.57%、MDD -4.17%、勝率 56.3%、0 未成交。**待接**：AC-1
  golden 凍結（需複審）、AC-7 處置強制出場（無 disposition 資料）、AC-9
  walk-forward（T-019）。
- **T-017（部分完成）**（2026-07-06）策略框架（SPEC-003）。strategy/：config
  （pydantic，AC-1 因子存在+最相近提示、權重加總=1）、universe（20 日 ADV 中位數 +
  最低價 + 四碼普通股）、combine（zscore_weighted、rank_avg；方向併入權重）、
  overlays（breadth_regime_filter，市場寬度由 prices 計算）、build（build_targets +
  signal_dates 用 T-003 日曆排週/月頻，AC-3）。CLI `targets`；configs/strategies/
  sample_revenue_trust.yaml。**真實資料端到端**：sample 策略在 2026-06-30 產出 40 檔
  等權目標（月營收 YoY 0.6 + 投信連買 0.4，zscore 組合、top40、寬度 overlay 後總曝險
  82%）。ACs：1/3/5/6/7 通過（95 tests）。**待接**：AC-2 處置/全額交割排除、上市日、
  市場別（無 disposition/stock_meta 資料）；AC-4 ic_weighted 組合（需 PIT IC 重估）；
  score_prop/inv_vol 權重；index_trend_filter（需加權指數）。
- **T-003**（2026-07-06）交易日曆（SPEC-000 AC-3）。data/calendar.py：
  TradingCalendar（is_session/between/missing/next_session/prev_session）、
  derive_from_prices（由 prices_daily 各來源日期聯集推導——TPEX 完整即還原真實
  開市日）、persist/load（單檔 clean/trading_calendar/calendar.parquet）、
  cross_check（各來源自身區間內缺口）。CLI calendar 指令。**禁用 weekday()**：
  status CLI 與儀表板缺口偵測改以開市日為準。**實測**：243 開市日、TPEX 無缺口
  （原誤報 18 假日缺口→歸零）、TWSE 正確顯示 101 個開市日 ban 缺口。閘門全綠
  （86 tests）。
- **T-015**（2026-07-06）P0 籌碼 + 基本面因子。factors/chip.py（trust_net_10d、
  foreign_net_10d、trust_streak、margin_velocity_5d）、factors/fundamental.py
  （revenue_yoy、revenue_accel，以公布日 available_at as-of 映射到日頻，PEAD）、
  factors/builtins.py（一次匯入註冊全因子，CLI/儀表板共用）。月營收因子 PIT
  as-of 測試 + 反前視護欄全綠（81 passed）。**真實資料 IC + 對抗驗證**（as_of
  2026-06-30，四碼普通股）：
  - `fundamental.revenue_yoy`：10/20 日 t=+7.3/+8.4，缺口前連續期仍 t≈+3.9→
    **最穩健**（月營收 PEAD 屬實，§1-4）。
  - `chip.trust_net_10d`：10 日 t=-10.2，**連續期仍 t=-11.6→真實反向**（非缺口
    假象），與 +1 先驗衝突，呼應 §1-2 投信擁擠反轉；符號待 T-019 定奪。
  - `chip.foreign_net_10d`：t≈-5 穩健負（外資買超偏反向，§1-3）。
  - `chip.trust_streak`：全期 t=+3.5 但連續期崩至 +1.4（缺口灌水，勿高估）。
  - `chip.margin_velocity_5d`：|t|<2，弱。散戶密集度真身（TDCC 集保）待接。
  未做：投信主動/被動拆分（需 ETF 持股）、chip.concentration_*（分點，T-016）。
- **T-013**（2026-07-05）IC 檢驗 + research_log。factors/ic.py（純函數）：前向報酬、
  逐日 RankIC（Spearman）、均值/t-stat/IR、五分位多空價差。reporting/research_log.py
  （IO 層，append-only 分片，trials 計數供 deflated Sharpe）。CLI ic-report。
  **真實資料驗證**：price.mom_20d 在 2025-H2 上，5 日 RankIC t=-2.32（顯著反轉）、
  20 日 t=-0.14（動能無效）——精準重現 STRATEGY.md 台股實證。本地因子中性化待
  市值資料。閘門全綠（72 passed）。
- **T-012**（2026-07-05）因子框架（SPEC-002）。@factor 註冊表（名稱格式驗證、
  重複/類別檢查）、compute 引擎（long format）、標準化管線（MAD 去極值→z-score，
  AC-4）、assert_no_lookahead 反前視護欄（AC-3，含壞因子反向測試）、price.mom_20d
  基準因子。因子為純函數（IO 邊界靜態測試涵蓋）。閘門全綠（67 passed）。
- **T-011 backfill**（2026-07-05）歷史回補 CLI。`backfill --from --to`（TWSE+TPEX
  日資料，跳週末、跳假日、依分區存在續傳）、`backfill-revenue --from --to`（逐月）。
  實測回補 2026-06-16~30：9 交易日新增、端午節(06-19)自動略過、06-30 續傳跳過；
  資料湖累積 10 個交易日全市場（每日約 6000-7000 檔）。閘門全綠（58 passed）。
- **T-009（部分）**（2026-07-05）MOPS 月營收轉接器（P0 因子）。t21sc03 Big5
  HTML 逐月抓（上市 sii + 上櫃 otc），yoy 自算（當月/去年當月−1，避開來源
  「去年同月增減」欄的 HTML 錯位）；營收千元→元、金融股可為負（放寬 nonneg）。
  monthly_revenue 加 date 分區鍵（月底）。CLI ingest-revenue。實測 2026-05：
  1848 家（上市 987 + 上櫃 861），YoY 因子可查。TDCC 股權分散、TAIFEX 期貨待補。
  閘門全綠（55 passed）。
- **T-008**（2026-07-05）TPEX 上櫃行情轉接器（D-014）。dailyQuotes（瀏覽器
  headers 繞 anti-bot、歷史日期、附次日漲跌停）；HttpClient 加 headers 支援；
  倉儲 write 加 source 參數（上市/上櫃按來源分檔共存於 prices_daily，不覆蓋）。
  實測 2026-06-30：全市場 6747 檔（上市 1361 + 上櫃 5386）。法人/融資券歷史
  走 FinMind（T-010）。閘門全綠（52 passed）。
- **T-011（部分）**（2026-07-05）倉儲核心 + 清洗管線 + DuckDB 分析。
  clean.py：三張 TWSE 表 raw→typed（單位張→股、去逗號、佔位符轉 null、加
  available_at 與 date）。warehouse.py：Parquet 分區資料湖、原子寫、分區級替換、
  PitView（D-008 的 20:00 截點，AC-9）、DuckDB SQL 查詢。CLI 加 ingest / sql。
  **實際運作驗證**：ingest 2026-06-30 存入 prices 1361 / 法人 1330 / 融資券 1279 列；
  SQL join 查投信買超前 10 名，重現「投信季底作帳、外資對作金融股」現象。
  閘門全綠（44 passed）。**待補**：還原價（需 T-009 corporate_events）、
  完整品質閘門、backfill CLI、raw 層落地整合。
- **T-007**（2026-07-05）TWSE rwd 轉接器完成。實盤抓取 2026-06-30 三張表成功
  （prices 1369 檔、法人 1330 檔、融資券 1279 檔）；台積電收盤 2410、投信買賣超
  -89,595 股等真實資料已驗。fixture 錄自實盤精簡（tests/fixtures/http/twse_rwd/），
  8 個離線測試驗解析/去重/異常（stat/空/不支援）。閘門全綠（35 passed）。
- **T-006**（2026-07-05）HTTP 基礎設施完成。滑動窗限流（AC-4）、指數退避重試、
  raw 版本化原子落地；clock/transport 可注入離線測試。閘門全綠（27 passed）。
- **T-004**（2026-07-05）資料合約完成。14 表 pandera schema，available_at 型別
  強制 tz-aware；AC-3 單位、strict、tz、負值測試。閘門全綠（22 passed）。
- **T-002**（2026-07-05）conventions 完成。TZ/assert_tw_timestamp/as_of_cutoff、
  例外樹、配置載入器（AC-1/AC-2）。tzdata 依賴。閘門全綠（14 passed）。
- **T-001**（2026-07-05）專案骨架完成。`uv sync --dev` 建 venv；
  `python -m twquant check` 跑 ruff check + ruff format --check + mypy(strict)
  + pytest 四項全綠（4 tests passed）；`--fast` 跑 ruff + pytest -x。
  IO 邊界靜態測試（tests/test_io_boundary.py）以 AST 掃描純運算層禁止的網路
  import 與寫檔呼叫，含反向測試防恆真。正反雙向驗證：注入 `import requests`
  到 factors/ → 閘門退出碼 1、IO 測試抓到違規；移除後退出碼 0。CI（.github/
  workflows/ci.yml）於 push/PR 跑同一閘門。完成定義各項已核。
| T-112 | 【資料 · 重物化與重跑】T-111 修好 stock_meta 後，`factor_values` 仍是舊值。`price.residual_reversal_5d` 是 composite 因子 → 子集模式被明文拒絕，須**全因子物化** 1,619 日。完成後 `ml_h20_cost_aware` 的 12 個特徵中有 1 個（`residual_reversal_5d`）的值會變，故須以**同一組已登記的配置**重訓後再跑回測——這是同一次試驗的儀器修正，不佔新的 DSR 額度。AC：(1) 全因子物化 1,619 日完成且無缺日；(2) 比對物化前後每日檔數；(3) 重訓 `ml_h20_cost_aware`；(4) 重跑 `ml_net_h20_m` 三個成本檔位，結果記入 D-052。**排序**：D-054 裁決後，本任務排在 T-098 之後——T-098 會改變標籤，物化與重訓應在同一份修好的資料上一次做完，避免重做兩輪。 | T-111, D-052 | T-098 | A | blocked |
| T-121 | 【策略 · 宇宙市值篩】`UniverseConfig` 新增 `mktcap_rank_max` / `mktcap_rank_min`（依 PIT 市值排名，用 `fundamental.log_market_cap`）。**相對門檻，不隨年代漂移**，故不受 T-120 影響。AC-1：正確依排名過濾、市值 null 一律排除（比照 `min_roe`）；AC-2：通過 `assert_no_lookahead` 逐位元比較。兩欄位皆 `None` 時輸出須與加入前逐位元相同 | SPEC-013 §2/§7 | — | C | done（2026-09-05；`_mktcap_gate` 於 `universe.py`，7 條測試全綠。**兩個 review 未定義點已在實作中寫定**：(1) 排名母體＝當日有市值的四碼普通股全體，**不是**流動性濾網後的殘存集合——「前 100 大」是市場事實，母體若隨 `min_adv_twd` 變動，T-120 的門檻漂移會經由排名滲入；(2) 同值以 `stock_id` 為次序鍵，跨程序決定性。PIT 測試以「2023Q4 塞入巨額股本、年報 2024-03-31 才可見」驗證，讀到未來股本會使排名互換。閘門 614 passed） |
| T-122 | 【策略 · 宇宙基本面篩】`UniverseConfig` 新增 `min_revenue_yoy` / `max_revenue_yoy` / `min_revenue_streak`。AC-3：連續月數以**營收月份**計非曆月、營收缺值排除；AC-4：通過 `assert_no_lookahead`（月營收有公告時滯，**此條最容易出錯**，須明確測到公告日邊界）。門檻 10%/150%/3 為外部先驗（D-060），不得調整 | SPEC-013 §3.4/§7 | — | C | done（2026-09-05；`_revenue_gate` 於 `universe.py`，`tests/test_universe_revenue.py` 9 條全綠。**兩個關鍵實作決定**：(1)「連續」以 year_month 的**跨距**驗證而非筆數——`drop_nulls` 後缺月的兩筆會變相鄰，只數筆數會把 10 月與 12 月誤判為連續；(2) PIT 由 `view.table` 依 `available_at` 截斷，**刻意不依 `date` 過濾**——月營收的 `date` 是所屬月月底而非公布日，拿它當截點會放行尚未公告的月份。歷史不足 streak 長度者不通過（不得用較短的鏈充數）。閘門 614 passed） |
| T-123 | 【策略 · 加權法】實作 `weighting`：`score_pow`（`score^p`，p 從配置注入）與 `mktcap`（依 PIT 市值）。**併修既有缺陷**：`build.py` 的 `min_horizontal(target_weight, cap)` 只截頂不重新歸一，市值加權一旦觸頂就漏曝險。**Literal 須擴充**（現為 `["equal","score_prop","inv_vol"]`，`score_pow`／`mktcap` 不在其中，pydantic 會直接擋掉）；`score_prop` 實作為 `score_pow` 的 p=1 特例；`inv_vol` 維持未實作但改寫錯誤訊息。**負分處理事前寫定**：先橫斷面平移最低分至 0 再取冪（`rank_avg` 的 signed weights 與 `zscore_weighted` 都會產生負分）。AC-5/AC-6/AC-7/AC-13/AC-14。**D-061 追加契約**：`mktcap` 權重須用 `exp(value)`（因子回傳 ln 市值，按 log 值比例會把 1.5e6 vs 4e3 壓成 14.2 vs 8.3）且**不得套 `direction=-1`**；截頂重分配須在 **overlay 之前**（在最後歸一會把 overlay 刻意壓低的曝險補回去）；可行性檢查用**實際入選檔數**非設定 `top_n`；`score_pow` 平移**無條件**套用且 shift 取**全宇宙**最小值；零分母退化等權；四因子須**四項齊全**才入選（`rank_avg` 對存在的列求和，缺低波因子者拿不到負分、分數虛高）。`.py` 內不得出現魔術數字 2 | SPEC-013 §2.2/§2.2.1/§7, D-061 | — | B | done（2026-09-05；新模組 `strategy/weighting.py` 三個純函數 + 11 條單元測試，另在 `test_strategy.py` 加 4 條整合測試驗**順序契約**（單元測試看不到）。`weighting_power` 預設 1.0＝`score_prop`、`require_all_factors` 預設 False，故既有 15 個配置行為不變。`inv_vol` 仍未實作但錯誤訊息改指向 SPEC-013。閘門 625 → 629 passed） |
| T-124 | 【CLI · 成本檔選項】`backtest` 加 `--costs <path>`。現況 `cli.py` 寫死 `costs.yaml`，導致 `costs_finlab.yaml` **從 CLI 根本無法使用**。AC-8：未指定時行為與現況逐位元相同 | SPEC-013 §2 | — | C | done（2026-09-05；`--costs` + `_resolve_costs`：絕對路徑照用、相對名稱相對於 configs/，預設值仍是 costs.yaml 故未指定時解析結果不變。3 條測試。閘門 639 passed） |
| T-125 | 【回測 · 歷史持股重建】`BacktestResult` 只有 `final_holdings`，歷史部位沒存。由 `ledger` 逐筆買賣累加重建每日部位（純函數，落地由呼叫端負責，黃金規則 10）。AC-9：最後一日與 `final_holdings` 股數完全相符；AC-10：任一日皆無負股數（放空未實作） | SPEC-013 §6.2/§7 | — | B | done（2026-09-05；`diagnostics.positions_from_ledger`，純函數無 IO。**三種情況一律 raise**：股數變負、成交日不在權益曲線日期內（靜默略過會讓持股表少一整段）、未知 side。清空當日不留 0 股的列；未成交日由前一日結轉。7 條測試，含與 `final_holdings` 對錨。閘門 639 passed） |
| T-126 | 【報表 · 雙軌儀表板】單一 HTML、分頁切換兩軌（**不並排**，兩軌基準不同會誘導錯誤比較）。含 KPI／權益曲線／年度長條／基準對照／歷年持股三層（年度摘要、調倉時間軸新進標綠剔除標紅、個股明細）／方法論表。AC-11：KPI 比較值全部取自 `bench_stats`，原始碼中不存在寫死的比較數字（曾因寫死出現「TAIEX −34.37%」錯標） | SPEC-013 §6/§7 | T-125 | B | **done（2026-09-06）**。單頁 HTML 已發布為 Artifact，含三張伺服器端 SVG （風險報酬散點／濾網邊際效果／權益曲線），並產 markdown 進版控（`docs/reports/backtest-results.md`）。AC-11 以架構保證：`build_report.py` 不做任何計算，所有數字從 `all_backtests.json` 讀出。**§6.2 的橋**（結果包→歷年持股）已完成並在真實資料上驗過 |
| T-127 | 【實驗 · 控制組階梯】**關卡任務**：6 階（L0 現況／L1 40檔／L2 營收篩／L3 四因子／L4 分數加權／X1 只換訊號）× 正常與零成本 = 12 次回測，scope `bt:ladder_diag`。回答三問：資料成本窗口有無問題、宇宙構造值多少、40 vs 20 值多少回撤。子區間（2022-07~2023-12 / 2024-01~2026-02）由既有曲線切出、**不重跑**；符號相反即標「窗口相依，不可外推」。AC-12：結果填入 `docs/reports/ladder-results.md`。判讀依 SPEC-013 §3.7 機械套用，**執行者只填表不下結論** | SPEC-013 §3, D-060, D-061 | T-121, T-122, T-123, T-124, T-131 | B | done（2026-09-05；12 次全成功、共 45 分。**§3.7 事前判讀的兩條門檻都未達成**：L1（20→40 檔）要求「MDD 改善 >3pp 且 CAGR 下降 <1pp」，實際 MDD 僅 +2.41pp 而 CAGR −7.96pp——檔數不是免費的風險改善，且 0× 下 t=−2.03 是**全表唯一達顯著的比較**（較差）；L2（營收篩）要求 CAGR 改善 >3pp，實際 −0.04pp。**X1 隔離格是關鍵**：只換訊號時 ΔCAGR −6.74%／ΔMDD +8.72pp（t=−0.76 未能區分），而 L3 的 +6.38%／+13.95pp 是**條件於 40 檔＋營收篩**之上——四因子的優勢不可無條件外推。**L2 窗口相依**（前段 −12.26%、後段 +7.41%，符號相反）。成本 4~6.5pp/年。資料一致性：5 階的 1×／0× 指紋皆相同。閘門 659 passed） **⚠ D-064：本次結果受缺陷 A（整張捨去）與 B（2023 價格缺口）汙染，不得引用其數字下結論；待 T-135／T-136 後重跑** |
| T-128 | 【策略 · A 軌小型飆股】`track_a_smallcap.yaml`（市值前 100 大以外、營收篩、動能×營收加速×量能×ROE 軟排名、15 檔等權、月頻）。scope `bt:smallcap_momentum`，2 次（正常成本 + 高滑價）。**強制檢查**：容量（低於 1,000 萬須紅字標示）、成本敏感度、`unfilled` 中 `limit_up` 比例（>5% 須標註報酬高估）。主基準為**等權小型宇宙**，不得以「打敗 0050」判定成敗 | SPEC-013 §4, D-060 | T-127 | B | **done（2026-09-06）**。A 軌 80M：CAGR **+15.90%**／MDD −38.5%／Sharpe 0.68／曝險 94%。**輸 0050 含息 24pp**。此窗口由權值股帶動，而 A 軌的 `mktcap_rank_min: 101` 系統性站在行情對面。資金 10M→80M 幾乎不影響它（−0.33pp），因為小型股本來就買得起 |
| T-129 | 【策略 · B 軌穩健打敗大盤】B1 `track_b1_large_enhanced.yaml`（市值前 100 大選 30、市值加權）與 B2 `track_b2_broad_capw.yaml`（全市場選 50、市值加權）。各 2 次，scope `bt:largecap_enhanced` / `bt:broad_capweighted`。**強制列出 Beta 與追蹤誤差**：Beta < 0.8 代表架構改造失敗，此時輸給 0050 不得歸因於因子。報表須同時畫 0050 買進持有，未勝出即誠實寫「直接買 0050」 | SPEC-013 §5, D-060 | T-127 | B | **done（2026-09-06）**。B1 **+23.68%**／MDD **−24.0%**／Sharpe **1.28**（回撤比 0050 還淺）；B2 **+27.79%**／−30.9%／**1.35**（目前 Sharpe 最高）。**資金修正的影響極大**：B1 在 10M 下只有 +15.29%／曝險 70%——大型股一張最貴，10M 下 30 檔每檔僅 33 萬，被整張捨去嚴重扭曲（D-070） |
| T-130 | 【實驗 · 長窗口重跑】T-116 回補到 2005 完成後，以長歷史重跑階梯與兩軌。**須先解 T-120**（絕對門檻在長歷史上漂移：3,000 萬 TWD 在 2005 與 2026 是完全不同的門檻）。目的是解 DSR 的統計檢定力問題——現行 885 個 OOS 日下，DSR ≥ 0.95 需要年化 Sharpe ≈ 2.03，是不可能達成的門檻 | SPEC-013 §3.6 | T-116, T-120, T-127 | A | blocked |
| T-131 | 【回測 · 統一結果包】**T-127 前置**（D-061／R8）。現行 `backtest` 只印指標並把純量寫進 `research_log`，`BacktestResult` 的 `equity`／`ledger`／`unfilled` 全部丟棄——照 SPEC-013 §3.3 跑完 12 次之後，§3.6「只切既有曲線不重跑」所需的 artifact 根本不存在。一次 run 落地曲線、ledger、未成交、日期、完整配置與**資料指紋**，之後的分段與報表都從同一份 artifact 產生。落地由 CLI 或 `reporting/` 負責（黃金規則 10：回測層不得寫檔）。AC：(1) 同一配置重跑產出 bit-identical 的結果包；(2) 結果包含資料指紋，資料變動即可偵測；(3) 由結果包重算的 CAGR／Sharpe／MDD 與 `backtest` 印出的一致 | SPEC-013 §3.3, D-061 | T-125 | B | done（2026-09-05；`reporting/result_bundle.py`＋`backtest --out`。指紋涵蓋的表由**策略自己決定**（訊號因子宣告的 tables ＋ prices_daily ＋ 依宇宙濾網補 financial_statements／monthly_revenue）——寫死清單會在新增濾網時悄悄失效。指紋在**跑回測之前**取：跑完再取的話，長回測期間若有 ingest 寫入會記到跑完後的狀態。**已知限制並已寫進 docstring**：只用 (列數, max(available_at))，偵測得到回補／重抓／修正，偵測不到不改列數也不動 available_at 的就地竄改。12 條測試。閘門 651 passed） |
| T-132 | 【登記簿 · 分片檔名碰撞會靜默吃掉紀錄】`ingest_log` 與 `research_log` 都以 `part-<時間戳>.parquet` 命名分片，而 Windows 系統時鐘解析度約 1~16ms——實測連續兩次 `now_tw()` 有 **~100%** 機率完全相同，第二筆直接覆蓋第一筆且**毫無訊號**。`research_log` 這一邊是 **DSR 的試驗登記簿**：掉一筆 → `trials_count` 少算一個 distinct `params_hash` → DSR 被高估 → 晉升閘門被騙過。實測凍結時鐘後寫 4 筆只算到 1 筆。AC：(1) 凍結時鐘寫 5 筆，兩個模組都須全數存活；(2) `trials_count` 不得因碰撞低估 | CLAUDE.md 黃金規則 5 | — | B | done（2026-09-05；檔名加 `uuid4().hex[:8]` 尾碼，時間戳留前綴故排序仍大致依時序；跨程序亦適用（回補腳本會開子程序寫同一目錄）。3 條測試以**凍結時鐘**釘住不變量，不靠時序碰運氣——靠時序的測試只會偶爾紅，正是這缺陷長期沒被抓到的原因。**既有損失無法回推**：被覆蓋的檔案已不存在，現有 81 個分片的時間戳雖無重複，但過去覆蓋掉的筆數查不回來。閘門 659 passed） |
| T-133 | 【診斷 · 逐筆勝率與賺賠比】D-061 的審查明列「分開呈現交易勝率、賺賠比、成本後期望值與相對基準表現，**避免把 IC 勝率、日勝率和逐筆勝率混用**」，但引擎只有 `Metrics.win_rate`（＝**日勝率**），逐筆統計根本沒算——先前只在儀表板的臨時腳本算過。AC：(1) 一趟 ＝ 部位由 0 到回到 0，分批出場屬同一趟、清空後再進場是新的一趟；(2) 兩邊費用都計；(3) 期末未平倉不計入已實現統計但須回報筆數；(4) 無虧損時賺賠比回傳 None 而非 0（0 會被讀成「全賠」）| D-061 | — | B | done（2026-09-05；`diagnostics.round_trip_stats`，9 條測試。**實測兩種勝率差 11~12pp**：L0 日勝率 53.2% vs 逐筆 42.1%、L3 56.5% vs 44.9%——每一階都是賠的次數比賺的多，全靠右尾。賺賠比是全表最乾淨的區辨量：L1 僅 **1.07**（幾乎打平，解釋了 40 檔為何讓 CAGR 掉 8pp）、L3/L4 為 1.41/1.48。閘門 668 passed） |
| T-134 | 【診斷 · 持有期分桶與損益集中度】`diagnostics.holding_buckets`：把已平倉 round trip 依持有曆日分桶，回報每桶期望值與**損益佔比**。回答的是「策略靠什麼賺錢」，**不是「該抱多久」**——docstring 明寫 D-042 的教訓，防止讀成條件化陷阱。AC：(1) 桶界左閉右開，邊界值只算一次（全閉區間會重複計入）；(2) 空桶保留回報 0；(3) `pnl_share` 分母帶號故各桶加總為 1，並列 `pnl`（TWD）以防分母不穩 | D-063 | — | B | done（2026-09-05；6 條測試含邊界重複計入的反例。實測 L0 佔 84% 的「持有一個月」格每筆期望 −0.46%、L3 獲利過半來自 6% 的 121+ 天趟數——**集中度脆弱性**，不是勝率問題。閘門 674 passed） |
| T-135 | 【引擎 · 整張捨去】`_execute` 對不足一張的目標靜默歸零，實測 40 檔等權曝險只有 61%、檔數越多越嚴重，汙染所有跨檔數比較（D-064 缺陷 A）。**已做**：記錄 `unfilled(reason=lot)` 使其現形，成交行為不變。**待裁決**：啟用盤中零股或提高初始資金——兩者都改變成交結果，屬策略層變更，須事前登記後重跑。AC：(1) 不足一張時 unfilled 含 reason=lot 與原欲買股數；(2) 結果包與 CLI 的未成交統計含此類別；(3) 零股/資金的選擇寫進 DECISIONS | D-064 | — | B | done（2026-09-05；**D-068 裁決 10M → 80M**。判準不是曝險高而是**橫向差異小**——10M 下 20 檔與 50 檔差 20pp，那會汙染「檔數」的比較；80M 壓到 5pp。200M 更好但每檔 400~1,300 萬會撞到 ADV 5% 參與率上限，用「買不到」換「衝擊失真」不划算。引擎不改，`unfilled(reason=lot)` 記錄已完成。不增 DSR trial（`params_hash` 不含 `initial_capital`）。實際資金可行性另立 T-148） |
| T-136 | 【資料 · 2023 年 3～5 月價格缺口】`prices_daily` 缺約 55% 普通股（4 月只有 807 檔），台積電整個 4 月空的。衛生濾網正確地踢出 60 日，於是 2023-03～08 所有回測的宇宙從 ~640 剩 ~241 檔且不含權值股（D-064 缺陷 B）。從 T-116 staging 的完整價格回補重新載入，須驗證單位、`available_at`、去重，不得直接覆蓋。AC：(1) 2023-03～05 每月四碼普通股列數不足一半者 < 20 檔；(2) 載入前後以 `data/quality.py` 的跨日重複偵測確認無汙染；(3) 重跑 T-127 12 次與 T-128/129 9 次，結果包指紋須與汙染版不同 | D-064, T-116 | — | **A** | done（2026-09-05；`scripts/repair_prices_2023.py` + `prune_repair_ghosts.py`。**全期掃描找到三段缺腿**（2023-02-20 1 天、03-09~05-23 50 天、06-15~06-30 10 天），共補 73,662 列；重疊 60,926 列逐欄比對全符後才寫。**中途製造又清掉一個自己的缺陷**：第一版補了 staging 有而 PG 沒有的所有列，帶進 294 檔只存在於窗口的幽靈股（宇宙在接縫由 1,767 跳到 2,058），且衛生濾網攔不住（窗口前無列 → `shift(1)` null → 缺口判定 fill_null(0) 為 false）；已備份後刪除 13,724 列並改為只補「窗口前後正常日 PG 已知」的股票。驗證：全期 0 天缺腿；2023-08-07 宇宙 242 → **717**、台積電回來、B1 池 7 → 98。另加 `quality.group_share_anomalies` 偵測器——`sparse_dates` 以鄰近日為基準，對連續 50 天的停擺完全失效（鄰居也全壞），故改以**全期佔比**為基準。閘門 679 passed） |
| T-137 | 【資料 · 價格宇宙的存活者偏誤】T-136 比對時發現：staging 的歷史宇宙 3,007 檔（含已下市）比 PG 的價格宇宙大，正常日每天約有 280 檔 staging 有而 PG 沒有。補齊是對的方向（T-111 已在 stock_meta 做過），但**必須整段期間一起做**——只補局部會製造宇宙斷層（T-136 就踩到，見該任務註記）。AC：(1) 全期補齊後逐日四碼普通股數無跳階；(2) 補齊前後以 `group_share_anomalies` 與 `duplicate_snapshot_dates` 驗證；(3) 補齊會改變所有回測的宇宙，須事前登記後重跑 | T-136 | — | **A** | **closed（2026-09-05，D-065：前提被證偽）**——那 951 檔不是下市股。2020 年後有行情的 387 檔經 FinMind `TaiwanStockInfo` 查證**全部是 `emerging`（興櫃）、0 檔下市**；三個量測都指向這個結論：逐年新增列數**遞增**（下市股該遞減）、列數最多的 8 檔**全是 1,622 列**（全期都在交易）、type 欄位直接寫著 emerging。補進去不是修偏誤而是**製造**偏誤——策略宇宙是 TWSE+TPEX，興櫃是議價交易、流動性極低。真正的舊下市股（564 檔）在 2020 年前就沒行情，不在回測期間。轉 T-143 |
| T-138 | 【資料 · 全資料健檢】`scripts/audit_data.py`：一次跑完所有**真的發生過**的失效模式（整腿缺席／稀疏日／跨日重複快照／個股列數斷層／因子覆蓋率塌陷／非有限值／橫斷面常數）。每項檢查都對應一個歷史缺陷而非想像的風險；只讀不寫，發現問題只回報。首跑（2022 起）找到 **6 critical**：`factor_values` 1,724 列 inf/NaN、`prices_daily` 413 處個股列數斷層（最大 260 日）、2023 那段缺腿的 **ETF 部分未修**（61 天）、`institutional_daily`／`margin_daily` 的 ETF 腿長期稀疏。AC：(1) 可重跑並輸出 markdown；(2) critical 時以非零碼結束；(3) 每項檢查在文件中說明它對應哪個真實缺陷 | D-064, T-136 | — | B | done（2026-09-05；報表 `docs/reports/data-audit.md`） |
| T-139 | 【ML · 標籤橫跨資料缺口】`forward_open_returns` 用 `px.shift(-(1+h)).over("stock_id")`——**列位移**。某檔中間缺 N 個開市日時，「20 日報酬」實際橫跨數月而**無任何標記**。實測 `prices_daily` 有 413 處 >5 開市日的缺口（最大 **260** 日、2022~2026 每年都有），其中 382 處落在 ml_scores 期間，h=20 下約 **8,673 個訓練樣本**的標籤橫跨缺口。凍結模型 `lgbm_net_h20_20260225_9a575e97` 即以此訓練。AC：(1) 標籤改以**交易日曆的列距**驗證，跨距超過 h × 容忍倍數即設 null（不得靜默算出數字）；(2) 量化受影響樣本數並與現行標籤比對分布；(3) 重訓並與凍結模型比較——這會改變模型，須事前登記 | T-138 | — | **A** | **程式已完成**（2026-09-05；`twquant/panel.py` 的 `session_index`／`shift_within_span`／`safe_ratio`，9 條測試）。標籤端 `forward_open_returns` 加 `max_span_sessions`，**預設 None 不啟用**故既有配置逐位元不變；測試含反向確認（證明樸素版確實會算出跨缺口的數字）。因子端 `price.py` 的 9 處除法全部改用 `safe_ratio`，並新增 `_lagged_return` 附跨距防護（容忍倍數 3，只攔「往回 20 列卻跨 1,200 個開市日」的荒謬情形）。**待辦**：重物化 factor_values、在配置啟用 max_span_sessions、重訓 | 
| T-140 | 【因子 · 零分母與溢位防護】`factor_values` 有 22,988 列 `inf`（6 個價格因子、1,785 檔、459 個日期）。根因是 2021-01~02 的 1,185 列 `close=0`（那些列有成交量與金額，只有價格欄是 0——清洗層漏掉的無效值），被跨缺口的 `shift` 撈到。`inf` **不是** null，`drop_nulls` 攔不住。AC：(1) 分母 ≤0 或缺值回 null；(2) **連結果本身也要驗有限**——`1e300/1e-300` 分母是正的仍溢位成 inf，memory 的建議「guard the denominator **then assert the output is finite**」兩句都要做，先前只做前半句；(3) 正常值逐位元不變 | T-138 | — | B | done（2026-09-05；`panel.safe_ratio`，測試含溢位案例。閘門 692 passed） |
| T-141 | 【資料 · 基準 ETF 缺口】T-136 的遺留：2023 的 TWSE 缺腿同時帶走 **1,260 個非四碼代號**，而 staging 的宇宙只有 3,007 檔四碼股，涵蓋不到 ETF。`0050` 在該期間只剩 **26/89 列**——它是 B 軌的判定基準，缺口後第一天的「日報酬」實際是 60 幾天，配對比較的 Beta 與 TE 因此被汙染。AC：(1) 只抓實際被引用的基準（免費層配額正被 T-116 佔用）；(2) 重疊列逐欄比對相符才寫；(3) 修補後列數須與市場開市日數相符 | T-136 | — | B | done（2026-09-05；`scripts/repair_benchmark_etfs.py`，0050／0056／006208 各補 61 列。**0050 逐年列數 2022:246、2023:239、2024:242、2026:163 與市場開市日數完全相符**。2025 差的 5 天（06-11~06-17）經 FinMind 確認**本來就沒有交易**——1:4 分割前的停牌，非資料缺失。**已知未修**：其餘 1,257 檔權證與冷門 ETF 仍缺，策略宇宙是 `^[1-9][0-9]{3}$` 故不影響回測；若日後新增以它們為基準的比較，須先跑本腳本並把代號加進 `BENCHMARKS`） |
| T-142 | 【資料 · 全期子集重物化】T-139／T-140 改了 `price.py` 的 9 處除法與跨距防護，但 `factor_values` 是改動前算的，22,988 列 `inf` 還留著。**實測 inf 不是可忽略的 0.1%**：winsorize 雖把它夾成有限值，卻扭曲整個橫斷面——含一列 inf 時其餘 9 檔的 z 全變（−1.4863 → −1.0243、+1.156 → +0.4703），一列壞資料汙染當日全部樣本。子集含 `fundamental.revenue_yoy` 是 D-057 要求（子集須完整包含 composite 集合）。AC：(1) 重物化後 `factor_values` 無 inf/NaN；(2) 2023-02-20~06-30 另行全因子物化 | T-139, T-140, T-083 | — | B | **done（2026-09-05）**。1535/1535 個交易日、78.4 分。**但 D-070 證明它對回測無影響**——回測不讀 `factor_values`（14 個結果包裡 0 個提到），重跑逐位元相同。它真正影響的是 ML 訓練的特徵與儀表板 |
| T-143 | 【策略 · `markets` 濾網是 no-op】`UniverseConfig.markets` 預設 `[TWSE, TPEX]`，但 `universe.py` 明寫「市場別旗標暫為 no-op」。目前擋住興櫃的**只是「`prices_daily` 剛好沒有它們」**——一個靠巧合成立的防線。D-065 發現 T-137 差點就把 387 檔興櫃、316,389 列補進去，那會讓它們無聲地進入策略宇宙。`stock_meta` 有 market 欄（TWSE 1,124／TPEX 925，興櫃不在其中），接上即可。AC：(1) `markets` 生效，市場別為 null 者一律排除（比照 `min_roe`／市值的規則）；(2) 通過 `assert_no_lookahead`；(3) 現行配置的宇宙**逐位元不變**（因為興櫃本來就不在 `prices_daily`，這條證明濾網沒有誤殺） | D-065 | — | **A** | **done（2026-09-05）**。`_market_gate` 上線，null **排除**（D-069）。AC-3 已量：2022-07-01／2023-07-03／2024-06-03／2025-06-02／2026-02-25 五個日期，開濾網前後宇宙**完全相同**（506／704／766／591／714 檔），濾網未誤殺。順序照裁決「先補 meta 再嚴格排除」：`ingest-meta` + `serving sync`（upsert，不覆蓋，已下市的 71 檔保留）把 3718 中光電投控補進 `stock_meta`（TPEX），**回測窗 2022-07-01~2026-02-28 內無市場別者由 1 檔降為 0 檔**。殘餘 7 檔（0054／4947／8287／1333／3562／8913／3452）末筆行情皆 ≤ 2022-05-26，窗外；ISIN 不列已下市公司，故不得偽造市場別（T-111 同一規則）。`scripts/audit_data.py` 加檢查 #9，窗前移到 2020 時這 7 檔即報 critical——「排除 null 安全」這個前提從此**機器可證偽**，不再是註解裡的口頭承諾。閘門：707 passed，ruff／mypy 全綠 |
| T-144 | 【ML · 標籤配置】`MlLabelConfig` 加 `max_span_sessions: int | None`（預設 None）並接到 `forward_open_returns`；`ml_h20_cost_aware.yaml` 設 42（h=20 × 2 容忍）與 `total_return: true`（D-054）。AC：(1) None 時逐位元不變；(2) 42 時橫跨缺口的標籤為 null；(3) 配置載入時 `max_span_sessions < horizon+1` 即拒絕 | T-139, D-066 | T-142 | B | **done（2026-09-05）**。**做的過程中發現 T-139 的防護對這份配置是 no-op**：`ml_h20_cost_aware.yaml` 用的是 `transform: cost_adjusted_return`，走 `cost_adjusted_return_labels`，而那個函式有**自己的** `shift(-(1+horizon))`，與 `forward_open_returns` 完全獨立。只接後者等於再造一個「看起來接上、實際什麼都沒擋」的旗標。已補上同一道防護，並同時遮 `_exit`（成本用）與 `_exit_r`（報酬用）——只遮後者的話，label 會變成「乾淨的毛報酬減掉髒的成本」。**實測攔到什麼**（2020-01-02~2026-09-04、h=20、max_span=42、含股利）：樸素 2,826,306 → 防護後 2,822,394，**攔下 3,912 個標籤（0.138%）**；被攔那批 |label| 中位數 0.1444、p99 **5.8098**、最大 10.1837，留存那批中位數 0.0647、p99 **0.4852** —— **尾部大 12 倍**，最大值是一個被標成「20 日報酬」的 1018%。數量微不足道、影響不微不足道：那正是梯度提升樹會重壓的樣本。（D-066 當初估 8,673 是 `forward_open_returns` 路徑；數字變小是因為 D-064 的資料修補已填掉 2023 缺口。）配置同步設 `total_return: true`（D-054）。閘門：713 passed |
| T-145 | 【ML · 乾淨基準重訓】依 D-066：先把 `lgbm_net_h20_20260225_9a575e97` 歸檔到 `_archive/…__pre-D064-data-defects/`（T-073 慣例），再以**完全相同**的 `ml_h20_cost_aware.yaml`（僅 T-144 的兩處資料修正）重訓，重產 `ml_scores`。**不調特徵、不調參**。AC：(1) 新 artifact 的 `artifact_fingerprint` 與舊不同且 `data_hash` 對應修補後資料；(2) 12 個特徵、split、lgbm 參數與舊 meta 逐項相同；(3) OOS 分數覆蓋 2022-01-14 ~ 2026-02-25 | D-066, T-144, T-143, T-135 | T-142 | **A** | **done（2026-09-05）**：`lgbm_net_h20_20260225_6a798962`，OOS RankIC **+0.0452**（汙染版 +0.0363）。AC-1 指紋已變（`config_hash` 9a575e97… → 6a798962…、`ml_config_hash` 4ed36850… → 81658ed2…；`cost_config_hash` 未變是對的，成本模型沒動）。AC-2 逐項比對通過：12 特徵集合相同、horizon 20、16 窗、**`train_end` 同為 2026-02-25**；label 只差 D-066 明列的兩處（`total_return` → true、`max_span_sessions` → 42）。AC-3 OOS 分數 1,781,220 列、2022-02-08 ~ 2026-02-25、2,032 檔已入 `ml_scores`。**過程中自己抓掉一次作廢**：第一次用 `--to 2026-09-04`，保險庫是從資料結束日往回算 6 個月，於是 `train_end` 變成 **2026-03-05**，越過 SPEC-013 §1.3 的邊界 2026-02-28 3 個交易日；已移入 `_rejected/…__vault-eroded-3-sessions` 並改用 `--to 2026-08-28` 重跑。**另記**：worktree 的 `data/` 是獨立實體目錄非連結，`ml train` 預設會寫進 worktree 而不是真正的模型庫，故須明確給 `--out` |
| T-146 | 【ML · 證偽測試】依 D-066 事前登記的準則：L0-new（新模型）vs X1，乾淨資料、1× 成本、**80M 資金（D-068，非 D-066 當初建議的 30M）**。**判定分三層**（D-066，事前寫定，不得在看到結果後挑方便的）：**甲＝繼續投入開發**（月度區塊 t ≥ +2.0，或 ΔCAGR ≥ +3pp 且 ΔMDD 不劣於 −3pp）；**乙＝保留但停止開發**（t ≥ 0，或 Sharpe 差 ≥ +0.10）——ML 留在因子庫與組合裡（B1-ML 就是這個用法），但**明文禁止**以「它還有價值」為由重啟特徵工程或調參；**丙＝退場**（兩者皆未達）。「未能區分」對甲層算未達，對乙層則看點估計符號與 Sharpe 差。L0-new 的 params_hash 因 model_id 改變 → +1 trial（`bt:ml_net_h20_turnover`）。AC：(1) 結果包指紋與 T-127 重跑版相同（證明資料一致）；(2) 判定依準則機械套用，執行者不下結論 | D-066 | T-145 | **A** | **done（2026-09-05）→ 判定「乙 層：保留但停止開發」**。L0-new CAGR **+13.65%** vs X1 **+12.36%**（Δ +1.29%）；MDD **−46.12%** vs **−33.77%**（Δ **−12.35pp**）；Sharpe **0.58** vs **0.72**（Δ **−0.14**）。配對日報酬差均值 +0.0152pp／日、月度區塊 SE 0.0458pp、**t = +0.33**（44 個月區塊、885 日）。四條判準只過乙-1（t ≥ 0）且勉強過；甲-1（t ≥ 2.0）、甲-2（ΔCAGR ≥ 3pp 且 ΔMDD ≥ −3pp）、乙-2（Sharpe 差 ≥ +0.10）**全部未達**。**讀法**：ML 訊號在乾淨資料上有正的點估計，但**買不到更好的風險調整報酬**——回撤深 12.35pp、Sharpe 低 0.14。依 D-066 乙層明文禁止以「它還有價值」為由重啟特徵工程或調參。判定由 `scripts/t146_verdict.py` 機械輸出，門檻是常數。**途中修掉自己一個守衛錯誤**：原本比整體 `data_fingerprint`，但它雜湊的是「該策略實際讀到的表集合」，訊號不同必然讀不同的表（L0-new 讀 `ml_scores`、X1 讀 `financial_statements`＋`monthly_revenue`），該守衛對本比較**永遠不會通過**——那是設計如此不是資料有問題。改成比**共用表**：`prices_daily` 兩邊都是 9,331,376 列、同一水位 ✓ |
| T-147 | 【策略 · B1-ML】補完 2×2 的第四格：`track_b1ml_large_enhanced.yaml` 與 B1 **逐字相同，只換訊號為 ML**（同宇宙、同 30 檔、同市值加權、同 25% 上限）。沒有它就分不清 B1 的優勢來自**架構**還是**換訊號**——現有三格只是對角線。**已量測非假設**：模型在市值前 100 大的分數標準差 0.2148／0.1968／0.2073 vs 全宇宙 0.2117／0.1959／0.2109，離散度幾乎相同，故非「把模型用在它不會的地方」。scope `largecap_enhanced_ml`（與 B1 分開，不得稀釋 DSR 計數），3 個成本情境。AC：(1) 四格在**同一份資料**上跑、結果包指紋相同；(2) 回報 B1-ML−L0（架構）與 B1−B1-ML（訊號）兩條邊，皆以月度區塊 SE 判定 | D-067 | T-142 | B | **done（2026-09-06）**。B1-ML 三個成本情境完成。**證偽了 D-067 的事前假設**：架構邊 B1ML−L0new ΔCAGR +3.40pp（t +0.04），訊號邊 B1−B1ML ΔCAGR **+6.63pp（t +1.80）**——訊號那條邊反而較大，與「優勢主要來自架構」相反。t 未達 ±2.0 故不宣稱定論，但方向須記錄 |
| T-148 | 【回測 · 實際資金可行性】D-068 明列這是**與策略比較不同的問題**：定額配置（單檔 10~20 萬）+ 盤中零股，回答「用實際資金跑不跑得動」。**整張交易下定額 15 萬只買得起 150 元以下**，實測 2026-02-25 有 281 檔一張超過 15 萬（最貴者一張 971 萬），砍掉 15% 宇宙且**逐年惡化**（2022 年 93% 買得起 → 2026 年 85%），砍的正是 2024–25 拉動大盤的高價股。零股可解，但改變成交語意（成交價、手續費最低 20 元對小額不利、流動性不同），引擎要改且既有回測不可比。另一個陷阱：定額配置**不複利**，權益成長後曝險會下降，報酬率被系統性低估。AC：(1) 零股成交模型；(2) 與 80M 比例配置對照，差異須可歸因；(3) 明確標示這是可執行性驗證而非策略比較 | D-068 | — | B | todo |
| T-149 | 【資料 · 2023 缺口重物化】T-136 修補了 2023-02-20~06-30 的價格，宇宙由約 790 檔變成約 3,700 檔，橫斷面 z／pct 是**相對**量 → 該區間**每一個因子值都還是舊的**。實測列數：2023-04-10 是 29,077 列、06-30 是 29,205 列，而區間外的 07-03 是 52,640 列。**不修，T-145 的重訓就會吃到一個「宇宙只有一半」的半年**——那正是 D-064 三個汙染之一。做法：`scripts/rematerialize_2023_gap.py`，**用歷史上實際存在的 35 個因子**。**為什麼不直接跑 `serving materialize`（實測後放棄）**：(a) 它產出 **42** 個因子而歷史每天是 **35**，多的 7 個是 `chip.broker_*`×3／`ml.*`×2／`price.industry_*`×2，只補在這 89 天等於在時間軸上挖一個反向的洞；(b) 90 分鐘只完成 1 天（見 T-150）。AC：(1) 區間內每日因子數＝35、與區間外一致；(2) 區間內列數回到約 52,000 量級；(3) composite 子集規則（D-057）在腳本啟動時驗證 | T-136, D-064 | — | **A** | **done（2026-09-05）**。87/87 個交易日、**24.5 分鐘**（對比全因子版的 90 分鐘／天，約 230 倍）。AC-2 已達：區間內每日列數由 **29,077 回到 53,000+**（53,198／53,468／53,281／53,395…），與區間外的 52,640 同量級。AC-1／AC-3 由腳本啟動時的驗證把關 |
| T-150 | 【效能 · broker 因子每天全撈整表】`chip.broker_*` 三個因子每天都**無條件全撈** `serving.broker_trades`（5,700 萬列，單次實測約 **286 秒**）——`PgTableSource` 沒有把日期條件推進 SQL。這讓 `serving materialize` 慢到 **90 分鐘/天**，89 天的區間要跑 5 天，且峰值記憶體達 **20.6 GB**（機器共 31.8 GB）。T-083 已對 `prices_daily` 做過同一件事（`since` 推進 SQL + `cache=True`，100 → 3.8 秒/日），套用到 broker 讀取即可。AC：(1) 單日物化含 broker 因子的耗時降到分鐘級；(2) 峰值記憶體降到個位數 GB；(3) 物化結果與現況逐位元相同（這是效能修正，不得改變數值） | T-149 | — | B | todo |
| T-151 | 【資料 · 未還原的公司行動＋零收盤價】建 T-126 的基準統計時，對齊防護當場抓到：**0050 缺 5 個策略交易日（2025-06-11~06-17）**，查出那是 **1:4 分割**的停牌（188.65 → 復牌 47.57），而 `corporate_events` **只有 `cash_dividend`、沒有分割／減資**。跨那一天會算出 **−74.8% 的單日報酬**——而 0050 正是「有沒有打敗大盤」的主基準。**為什麼既有的三道防護都攔不住**：`_entry > 0 & _exit > 0` 只擋零價，分割兩端都是正價；T-144 的跨距防護不攔，停牌 5 個開市日遠在容忍值 42 內；`safe_ratio` 也只防零分母。這是**第三種**形狀。全宇宙實測：排除 `close = 0` 後仍有 **25 筆**單日 < −50% 無對應事件（最極端 `5314` 2025-03-31 由 1390 → 75.8）。另一個獨立缺陷：**`close = 0` 共 727 列、全部集中在 2021 年**，其中 **218 列 `volume > 0`**——有成交卻沒有收盤價，資料自相矛盾（標籤的 `>0` 防護擋得住，但因子與報表未必）。**已先做**：`audit_data.py` 檢查 #10，兩者都變成機器可偵測。AC：(1) 分割／減資事件進 `corporate_events` 並使 `adj` 涵蓋；(2) 修正後 0050 跨 2025-06 的單日報酬回到合理範圍；(3) 2021 的 `close = 0` 依來源修正或明確標為缺值，不得留 0 | T-126, D-054 | — | **A** | in_progress（**AC-1／AC-2 已完成**：`split_rows` + `scripts/load_splits.py` 由 FinMind `TaiwanStockSplitPrice` 載入全市場 **34 筆**（28 split／6 reverse_split），與既有 12,143 筆除權息**無主鍵衝突**。0050 在 2025-06-18 的單日報酬由 **−74.8% → +0.87%**。**過程中又量出兩個獨立缺陷並一併修掉**：(a) **分割與股利被混成單一因子**，而不含息路徑整個跳過還原——分割根本不是報酬而是計價單位改變，任何情況都必須還原。實測 0050 不含息因此是 CAGR **−8.66%**／MDD **−77.18%**（2025 −66%），拆開後回到 **+35.55%／−28.47%**。(b) **`corporate_events` 對 380 檔 ETF 只涵蓋 16 檔**，而缺事件時因子被 `fill_null(1.0)`，於是「含息」**靜默退化成不含息**——006208 因此少約 4.4pp／年卻照樣標著「含息」。現在無事件卻要求含息即 raise。**交叉驗證**：0050 與 006208 追同一指數，修正後兩者的價格報酬對得上（+35.55% vs +35.6%）。另 `align_to` 加 `event_dates`：只放行**有公司行動可解釋**的停牌，無法解釋的仍 raise。**餘下 AC-3**：2021 年的 727 列 `close = 0`（含 218 列 `volume > 0`）。**另注意**：ML 的 `attach_adjustment` 也是混合因子，`total_return: false` 的凍結模型其標籤含**未還原的分割**——又一個 D-066 判定「每個數字都不可信」的佐證，T-145 重訓已設 `total_return: true` 故不受影響） |
| T-153 | 【ML · F 模型訓練與第一段證偽】walk-forward 切分與現有模型**完全相同**、LightGBM 參數不調。**第一段判準（D-074 事前寫定）**：OOS 營收驚奇 RankIC ≥ **+0.02** 且 ≥ 60% 測試窗為正；未過即**整條線關閉**，不進入第二段。先導量測的 IR 6+ 是樣本內，**不算數** | D-074, T-152 | T-152 | B | todo |
| T-154 | 【ML · 第二段經濟價值】用 F 分數當池子閘門（前 30%），其餘與 **M3 逐字相同**，判定 **F − M3**，沿用 D-066 三層。**對照組是 M3 而非 C1**：M3 是「動能當池子」，與 F 只差池子定義；對照 C1 會同時換兩件事。丙層 → 整條線關閉；乙層 → 保留為零件、停止投入。**只過第一段沒有經濟意義**——預測得到營收卻換不到錢，是學術結果不是策略 | D-074, T-153 | T-153, M 系列 | B | todo |
| T-155 | 【ML · R 模型資料集】依 D-075 建「未來 60 日大回撤」資料集。**標籤定義寫死為「相對 t 日收盤的最低點跌幅」**，不得用 run-max 版——實測後者的 IC 有 13~41% 來自與 `high_52w` 共用的「當下價位在區間中的位置」。AC：(1) 標籤以 t 日收盤為基準，附測試比對兩種定義的 IC 差；(2) 特徵不含任何前向價格衍生量；(3) 通過 `assert_no_lookahead` | D-075 | — | B | todo |
| T-156 | 【ML · R 模型訓練與第一段】切分與 LightGBM 參數與現有模型完全相同。**第一段判準**：OOS 回撤 RankIC ≥ **+0.05** 且 ≥70% 測試窗同號（門檻高於 F 系列的 +0.02，因先導 IC 高一個量級；跑不出來即代表先導是樣本內假象）。未過即整條線關閉 | D-075, T-155 | T-155 | B | todo |
| T-157 | 【策略 · R 風控閘門】把 R 疊在 **C1** 上（剔除預測回撤最深 10%），其餘逐字相同。**判準與其他線不同（用途是風控不是選股）**：ΔMDD ≥ +5pp 且 ΔCAGR ≥ −3pp，或 ΔSharpe ≥ +0.15；皆未達即關閉。**R 分數不得作為 `signals` 進入排序**——先導只證明它能排序「誰會跌得深」，未證明能排序「誰會漲得多」 | D-075, T-156 | T-156 | B | todo |
