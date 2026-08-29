# CHANGELOG

- 2026-08-29 ML 方向定案（D-036）＋SPEC-010-ml 草稿＋T-065~T-070 任務切分：
  橫斷面 LightGBM 排序 meta-factor——訓練走研究軌離線 CLI、推論落地 ml_scores 表、
  策略層以薄因子讀取；黃金規則與 IO 靜態閘門不鬆動，部署被 T-069 評估階梯
  （OOS RankIC→分位單調→成本後回測→DSR→金庫期）裁決擋住。研究藍圖見 artifact 報告。

- 2026-08-29 daily-pipeline 交易日解析修復＋8/27-28 補跑：GHA cron 於 8/27、8/28
  延遲 7-10 小時跨過台北午夜，DAY 解析成隔日/週六 → 連 4 紅（runs #44-47），兩交易日
  行情/訊號/供應層漏跑（margin_daily 因 T-1 回補步驟倖存）。修法：schedule 觸發改
  「台北 17:00 前視為前一日、週末回退週五」（帶明確 date 的手動觸發不受影響）；
  另以一次性 push workflow（backfill-0827-0828.yml，跑綠即刪）補齊兩日。

- 2026-07-30 T-064 個股因子頁重新設計：元件抽出 src/stock/（T-034 遺留）＋共用層
  theme/fallback/hooks/ui/charts（App.tsx 2,968→1,546 行）；mobile-first（手機
  sticky 頂欄＋橫捲 chip 列＋響應式圖高＋ZBars 條圖）；終端風 TPanel。行為與
  localStorage 鍵不變、D-020 標示保留；oxlint/tsc/vite build 綠、Playwright
  雙視窗煙霧零錯誤；後端零變更。

- 2026-07-29 T-056 雲端每日 pipeline 首跑成功（本機零參與）：擷取→閘門→券商→
  訊號(PG)→供應層→選股池→broker_flow 聚合→歸檔上 Drive→窗口修剪全綠。
  卡關為 repo Actions 停用（非分鐘數）。獨立驗證：Drive 11 檔齊全、Supabase
  prices@07-29 1,958 檔 / factors 30,882 列 / 非四碼 0。⚠ 總量 467 MB
  （+21 MB/日，餘裕 33 MB）——PG DELETE 不還空間給 OS，D-034 追加觀測。

- 2026-07-29 T-059 前端+API 上雲：api/index.py（Vercel Python function 掛既有
  FastAPI app）+ vercel.json（rewrites /api、includeFiles src/configs）；空湖 +
  Supabase 實測——個股頁/選股池/策略回測/meta 全走 PG，大盤K線/因子IC/指數/容量
  仍需資料湖（開 T-063）。GHA：repo Actions 原為停用狀態（run 排隊 5 小時無 runner），
  已啟用。

- 2026-07-29 T-055 Supabase 滾動窗口：CloudConfig + broker_flow_daily 表 +
  data/serving/cloud.py（prune/refresh_broker_flow/seed_tables）+ CLI serving
  prune/broker-flow/seed-cloud；Supabase 446 MB（factor 實測超估 → 過渡縮窗，
  D-034 待裁決）。T-053 收尾：base dump + 8 天增量上 Drive，MD5 驗證吻合。

- 2026-07-29 T-054 每日增量歸檔：data/serving/archive.py（dump_day/restore_day，
  bit-identical 冪等、ON CONFLICT 首錄還原）＋ CLI archive-daily/archive-restore；
  測試 +3、全套綠。T-053 全史 dump 1.3 GB＋還原演練 9 表精確吻合（Drive 上傳待
  使用者 rclone OAuth）。

- 2026-07-29 D-030 全雲端搬遷規劃正式化：目標架構裁決（GHA cron + Supabase Free
  滾動窗口 + Drive raw 歸檔 + Vercel）＋任務 T-053~T-062 四期拆解＋待裁決
  D-031/D-032/D-033。純文件，無程式變更。

- 2026-07-22 T-052 期末持倉批次明細：engine _Lot 追蹤（首買日/加權均價/MAE，
  加碼併入、歸零重置）→ 持倉表加買進日/均價/報酬率/最大虧損欄；測試 +4 斷言。

- 2026-07-22 T-051 回測期末持倉：engine final_holdings（SPEC-004 §4 期末切片）＋
  API holdings＋前端期末持倉表（中文名/市值/權重）＋動態策略描述；測試 +1、
  dev 端到端驗證（19 檔渲染）。

- 2026-07-22 T-049 Sleeve C 上畫面：/api/strategies＋/api/backtest?strategy=（PG 源、
  since 下限讀取 17.4s 回應、fallback 降級）＋前端策略下拉；worktree 雙服務端到端
  驗證通過（部署待 merge+rebuild）。防衛版引擎全期：週頻 CAGR −20.5%、月頻 −17.0%
  ——幻影已清、排名式表達與事件 edge 錯配（買「相對最慘」非「剛崩」），正確表達
  （事件窗因子＋日頻等權）開 T-050。測試 +3、pytest 全綠。

- 2026-07-22 T-047 D-029 引擎防衛（裁決採 C，B 部落地）：_fill 跳價拒單
  （price_jump）、持倉跳價 min 價強制出場（幻影不入損益）、宇宙衛生濾網
  （近 60 列跳價/缺口排除）；costs.yaml data_guard、UniverseConfig 新欄；
  測試 +3、pytest 347 passed。治本（品質閘門/停牌標記/還原價）開 T-048；
  Sleeve C 上前端（/api/backtest 接 PG＋策略選單）開 T-049。

- 2026-07-22 T-046 反轉×營收策略正式化（部分完成）＋重大資料品質發現（D-029）：
  策略 YAML（Sleeve C 原型）＋回測引擎 PG 路徑（calendar TableSource 鴨型＋測試、
  PgTableSource cache、型別放寬、CLI backtest --pg）。引擎全期 CAGR +70.9% 診斷為
  **停牌跳空幻影**（已實現合計 −338 萬；prices_daily 相鄰列 2× 跳價 2,776 次）→
  D-029 待裁決：品質閘門/還原價/停牌標記為回測硬前置。事件級日曆防衛重驗：
  反轉＋營收 20 日淨勝率 64.0%/中位 +5.0% 成立。閘門：pytest 344 passed。

- 2026-07-22 型態文獻總表（純文件）：docs/research/PAPERS-PATTERNS.md——三代理並行
  網路調查、逐篇核實出處（LMW 2000/Savin 2007/Osler 2000·2003/Marshall 系列/
  BLL→STW→Bajgrowicz 窺探鏈/HZZ trend factor/Bessembinder-Chan 台灣/旗形模板線/
  JKX 2023 CNN/Murray 2024）＋與 T-042~T-045 台股實證對照表。核心結論：文獻與在地
  實證一致——型態有資訊、裸訊號不獲利，價值在橫斷面特徵化；台股方向多與美股相反。

- 2026-07-21 T-045 旗形有撐因子 price.flag_hold_50d（STRATEGY §4b.2）：base→pole→
  flag→測撐成功日進場（非追突破）；+5 測試含反前視。順修 rank_ic_series 全同值日
  NaN corr 毒化 IC 均值（is_finite 過濾＋測試）。實證：RankIC 三視窗顯著為負
  （20d t=−4.7）、逐筆淨勝率 37–42% → §4b.4 記錄，不進策略。

- 2026-07-20 T-044 ic-report 改讀 PG（D-026 補完）＋真實資料修正：`--pg/--no-pg`
  （預設 PG，PgTableSource）；型態因子 `_price_panel` 剔除非正價列（serving 鏡射有
  low=0 停牌列，除零）；事件研究漲停占比分母改「有旗標資料的事件」（serving 的
  limit_up_price 905 萬列全 null，原實作誤報 0%）。首次全市場 6.5 年型態因子
  IC＋事件研究已跑（見 research_log ic:price.double_bottom_120d 等三組）。

- 2026-07-20 T-043 型態因子補全＋事件研究（STRATEGY §4b）：price.hs_top_120d（頭肩頂
  跌破，dir=−1）、price.box_breakout_60d（箱型突破，滾動向量化）＋ compute_event_study
  純函數（事件＝連續輸出段首日；事件後 5/10/20d 相對宇宙中位數超額、勝率、成立日漲停
  占比）＋ CLI ic-report --event-study 落地 research_log（es_*）＋ SPEC-002 §4 同步。
  測試 +8，pytest 337 passed（mypy 唯一紅燈仍為 D-028 既有基線）。

- 2026-07-20 T-042 型態因子基元＋price.double_bottom_120d（STRATEGY §4b）：樞紐點確認
  延遲反前視基元 + W 底突破事件因子（深度值、active_days 持續窗、事件稀疏輸出），
  8 條測試含 assert_no_lookahead。閘門綠（基線既有 test_screener mypy 3 錯記 D-028）。

- 2026-07-20 技術型態因子方向定案（D-027，純文件）：評估「吃 XQ」與「AI 看線圖」兩個
  回測方向後，裁定走 B1 規則式型態事件因子——STRATEGY §4b 新增規格（樞紐點確認延遲
  反前視、W底/頭肩頂/箱型突破三因子、事件研究雙軌檢驗、不進 composite/不標準化），
  TASKS 新增 T-042/T-043；AI 看圖定位為洞悉層離線標註（黃金規則 10 + AC-8 約束）、
  XQ 僅保留日後「訊號重放」選項。無程式碼變更。

- 2026-07-16 券商買賣超 SENTINEL 污染修正＋排程改 21:00（使用者回報「今天沒券商資料」）：
  16:40 排程跑券商步驟時富邦分點尚未公布 → 探測前 5 檔皆空 → **誤標整天為非交易日
  （SENTINEL `_day_empty`）**，之後晚點一鍵/排程再跑都撞 SENTINEL 而跳過，當日永久無資料。
  根因兩修：(1)`_run_fubon_day` pipeline 版**不再讀/寫 SENTINEL**——探測改「抓前 N 檔但
  不落地」，全空只回「稍後重試」、不污染當日（假日 SENTINEL 標記留給明示 backfill 指令）；
  成功路徑仍 `clear_sentinel` 自癒既有污染。(2) 排程時刻 16:40 → **19:00**（docker-compose
  command ＋ CLI 預設；分點傍晚才出，行情/法人/融資券那時早已齊；19:00 仍未公布則券商步驟
  回「稍後重試」不污染、晚點一鍵補跑）。當日 07-16 以 `backfill-fubon-chip --no-skip-existing`
  補齊（54,064 列/1,963 檔）。閘門：ruff/format、mypy strict(86)、
  pytest 63 passed（test_cli+pipeline+fubon_chip+ingest_admin）。

- 2026-07-15 一鍵跑當日全部資料·券商買賣超納入 pipeline（使用者需求續）：反映「沒有跑
  券商買賣超跟因子」——因子物化本就在 pipeline 供應層步驟，券商買賣超（富邦DJ 全市場
  分點）則原本只有獨立 `backfill-fubon-chip` 指令、不在每日 pipeline。抽 `_run_fubon_day`
  （`backfill-fubon-chip` 單日 best-effort 精簡版：非互動、skip_existing 續傳、假日探測
  跳過），`_run_daily_pipeline` 新增「券商買賣超」步驟（排在品質閘門後、選股池快照前，
  讓五池吃到當日主力買賣超）。一鍵與 16:40 排程皆會抓（全市場 ~10 分；PG 未啟用/不可達
  或來源失敗 best-effort 略過不擋主線）。前端面板副標與說明補上券商分點與耗時提示。

- 2026-07-15 一鍵跑當日全部資料（使用者需求）：排程若錯過每日觸發（機器離線等），
  資料後台新增「一鍵跑當日全部資料」面板——一鍵在伺服器端跑 `twquant pipeline`
  （＝ scheduler 16:40 同一完整流程：擷取全市場行情＋法人＋融資券＋… → 品質閘門 →
  訊號 → 供應層 → 選股池快照）。`monitoring/jobs.py` 加 `pipeline_args/pipeline_label/
  resolve_trading_day`（date 省略＝今日台北），`POST /api/pipeline-jobs`（與一鍵補抓
  共用同一單一併發 JobManager、工作出現在同一清單）；前端 `PipelinePanel`（watchdog
  橫幅標示排程是否錯過＋主動作一鍵今日＋次動作補跑指定交易日）；「補抓工作」面板改名
  「背景工作」（含 pipeline 工作）。閘門：ruff/format、mypy strict(86)、pytest 全綠
  （+5 `test_ingest_admin.py`：argv/label/resolve/400/409）；前端 oxlint＋tsc＋build 綠。

- 2026-07-14 排程改跑完整 pipeline（使用者需求）：`scheduler` 常駐迴圈原本每日只裸
  擷取，供應層/選股池快照要手動補跑；抽出共用 `_run_daily_pipeline`，每日 16:40
  觸發即完成擷取→品質閘門→訊號→供應層→選股池快照全流程（best-effort，不中斷主線）。

- 2026-07-14 T-040/T-041 策略選股池（使用者需求）：主力買賣超研究的高勝率條件常態化為
  五個每日選股池（`monitoring/screener.py`＋`configs/screener.yaml`＋`/api/screener`＋
  前端「個股因子」頁面板）；`serving.screener_daily` 每日快照（CLI `screener-snapshot`，
  冪等、可回補）＋ `?date=` 回溯（快照優先、無則即時回算）。SPEC-007 §6 / AC-7~14。

- 2026-07-12 D-024/D-025 `backfill-fubon-chip` PG 直寫＋假日探測＋進度列（使用者需求）：
  逐檔抓完當場 upsert PG `broker_trades`＋同交易寫新帳本表 `fubon_fetch_log`（log 存在 ⇔
  資料已入庫 → 多程序併跑逐檔跳過永不漏抓，競態最壞重抓一檔）；raw HTML 完全不落地。
  假日探測 `--probe`（預設 5）：全空即標記 SENTINEL 跳過整天（err 不算證據防誤判）。
  單行進度列（進度條/整體 %/目前日期股號/開始/已用/預估結束）。新指令 `fubon-chip-status`：
  每日已抓檔數/宇宙總數＋百分比。日終自 PG 讀回完整日補寫資料湖分區（防 serving rebuild
  清空、因子層 PitView 仍有真相源）；`Warehouse` 分區 tmp 帶 pid 防多程序互踩。
  閘門：ruff/mypy 綠、pytest 302 passed（僅既有無 DSN 的 test_stock_meta 紅）；
  本機 PG 5433 煙霧：建表/mark 冪等/day_stats/read_day 56,362 列 tz 正確全過。

- 2026-07-11 D-023 `backfill-fubon-chip` 預設不留 raw HTML（使用者需求、省磁碟）：新增
  `--keep-raw-html/--no-keep-raw-html`，預設純串流（抓→解析→丟，不寫 `data/raw/fubon_chip`，
  ~15MB/日 → 0）。canonical 仍落 parquet `clean/broker_trades`（小、因子/PitView 真相源）；
  PG 由既有 `twquant serving sync` 從資料湖鏡射，非本指令直寫。既有 raw HTML 可安全刪除。
  閘門：ruff/mypy 綠、tests 28 passed（+串流不落檔測試）。

- 2026-07-11 D-022 `backfill-fubon-chip` 平行下載（使用者需求）：逐股抓取改用執行緒池
  （新增 `--workers`，預設取 `source_rates.fubon.max_requests`），`SlidingWindowLimiter`
  與 `HttpClient` 共用 client 下加鎖（thread-safe 全域限流＋單一 Session）。富邦限流調至
  6 req/2s（使用者授權的積極設定，全市場單日 ~10 分；被封風險見 D-015）。`ThreadPoolExecutor.map`
  保序 → 輸出與序列版 bit-identical（冪等）。閘門：ruff/mypy 綠、test_http+test_cli+test_fubon_chip 27 passed。

- 2026-07-11 供應層物化韌性修補（D-020 衍生）：(1) `standardize` 的 winsorize/zscore 改為
  群內忽略非有限值估 median/mean/std，單一退化股的 NaN 不再經 `.over` 傳染成整個
  (date,factor) 橫斷面 NaN，也不再讓整因子被 `_compute_universe` 的 `filter(is_finite)` 丟棄；
  退化股自身維持非有限，僅它被下游過濾。(2) `_compute_universe` 吞因子例外不再靜默——
  預期資料缺口（DataError）記 DEBUG、非預期錯誤記 WARNING 附堆疊（黃金規則 5）。
  補屬性測試（單一 NaN 不汙染同群其他股 z）＋物化日誌測試。閘門：ruff/format 綠、
  mypy strict 全綠、pytest 284 passed。

- 2026-07-11 修 `serving rebuild` 靜默無效 + 修正 composite 重物化指引（使用者回報）：
  `serving rebuild --tables composite_daily`（及其他因子表 factor_values/factor_cross_stats）
  過去因 `rebuild()` 只過濾 `MIRROR_SPECS` 而 targets 為空、什麼都不做也不報錯（違反黃金
  規則 5），正是 composite_daily 全歷史殘留 D-020 前舊演算法值的排查根因。改為
  `schema.rebuild_targets()` 單一真相：收到非鏡射表名一律 raise，因子表訊息指引改用
  `serving materialize --from/--to`；CLI 清楚報錯並以非零碼結束。同步修 `configs/serving.yaml`
  的 composite_factors 註解與 SPEC-009 §3（rebuild 只重灌鏡射表、因子表走 materialize）。
  閘門：ruff/format/mypy 綠、pytest 274 passed（+6 rebuild_targets 斷言，不需 PG）；
  既有 `test_stock_meta::test_api_stock_meta_and_factors` 因本環境無 TWQUANT_PG_DSN 而紅
  （與本變更無關，git stash 驗證基線同樣紅）。

- 2026-07-11 K 線主圖疊加均線與關鍵價位（使用者需求）：`CandleChart` 主圖（pane 0）加
  MA5/10/20/60 收盤均線（以完整史料滾動平均後裁切到視窗，視窗首日均值才正確）＋量價分布
  關鍵價位 POC（最大量價位，實線）/VA頂·VA底（70% 價值區虛線，price-line 標籤帶價位數字）。
  `RangeCandle` 加均線 chips（預設 MA5/MA20）與「關鍵價位」開關；crosshair legend 顯示各
  均線值。關鍵價位為視覺化確認層（隨區間重算，非預測因子，見 research/PAPERS-KEYLEVELS.md §C）。
  純前端：oxlint 綠、vite build 過。

- 2026-07-11 研究：價量關鍵價位文獻調查（research/PAPERS-KEYLEVELS.md 第三批——CGO/
  V 型未實現損益/支撐壓力實證）→ STRATEGY §4a 行為錨定因子規格 + T-039 排程（blocked）
  + D-021 待裁決（周轉率權重資料來源）。

- 2026-07-11 D-020 裁決 B+C 落地：綜合分（composite_daily）由「全因子等權」改為「實證有效子集」
  ＝短反轉＋產業殘差反轉＋月營收 YoY（`configs/serving.yaml.composite_factors`）。新增純函數
  `data/serving/composite.py`，materialize（PG）與 snapshot（live）共用；factor_values 仍存全因子、
  僅綜合分用子集。前端體檢卡/P8 副圖/宇宙熱門列標示「非預測訊號」。閘門：mypy strict 83 files、
  pytest 266 passed（+5 子集斷言）、frontend build 綠。**上線需** `serving rebuild --tables
  composite_daily` 重物化 + docker 前端重建。SPEC-009 §3 同步。

- 2026-07-11 T-038 綜合分預測力研究完成（D-020 證據）：PIT-safe 實測確認等權綜合分樣本外無力
  （5d IC=−0.0047 t=−0.88、20d t=+0.09）。三方樣本外對照：B 有效子集兩 horizon 皆過 D-006
  promote（t≥3）、A 滾動 IC 加權 20d OOS 崩潰（t=0.04 過擬合）→ 建議 B+C、不建議 A。報告
  docs/research/COMPOSITE-IC-D020.md，證據寫入 D-020。

- 2026-07-11 D-020 開研究案（T-038）：composite_daily 綜合分實證無預測力（2023-01~2026-07，
  動能/量能七因子 IC 與宣稱方向相反、與反轉/營收正 IC 抵銷，5 日 IC≈−0.009）。提案
  A 滾動 PIT IC 加權 / B 實證有效子集 / C 前端標示「非預測訊號」，待裁決；純文件變更。

- 2026-07-11 因子選股加「訊號最強/最弱」切換 + K 線可加選多個因子副圖（使用者需求）：
  `factor_top` 加 `order=top|bottom`（bottom＝方向化 z 遞增，找最逆因子方向個股，如 5 日
  反轉的漲多股）；`/api/factor-top` 加 `order` query（非法值 400）。前端 FactorScreener 加
  最強/最弱切換鈕；K 線副圖新增可多選因子（chips，上限 4），每個副圖（量/綜合分位/主力/因子）
  左上以 watermark 標示指標名稱。回測 5 日反轉 top-N 選股（2023-2026、每 5 日再平衡、次日開盤
  進場、來回成本 0.585%、剔除分割/減資跳空）：top10 淨 CAGR +21.4%/Sharpe 0.73（毛 CAGR 高但
  成本吞掉大半），加流動性門檻後轉負——訊號集中在小型低量股，實務難成交。

- 2026-07-11 個股因子頁新增「因子選股」篩選器（使用者需求：直接找出 5 日短期反轉訊號最強個股）：
  新端點 `GET /api/factor-top?factor=&n=`（`stock_api.factor_top`）——該因子最新物化日、
  方向化 z（z×direction）遞減前 n，winsorize 並列以方向化 pct 決勝、附 stock_meta 中文名與
  `available` 因子清單（前端下拉免硬編碼）；未註冊因子回 400、未物化回空。前端 FactorScreener
  面板預設 `price.reversal_5d`（2023-2026 實證 5 日 RankIC +0.065 最高），點擊即選股。
  測試：未知因子 raise + PG 整合（方向排序/截斷/available）。

- 2026-07-10 `/api/stock-meta` 改讀 PG（使用者回報：追蹤股/選單無中文名）：原讀資料湖全湖
  掃描實測 ~108s，前端 stock-meta 名稱圖長期空→追蹤 chips/搜尋（0 檔）/更多個股皆無名。
  改讀 `serving.stock_meta`（`stock_api.stock_meta_all`），實測 0.25s；追蹤股顯示中文名、
  搜尋回 1974 檔。與個股頁「全 PG、開頁零等待」一致。前端無改動（名稱圖本就用 mm）。

- 2026-07-10 開頁零等待（使用者裁決：「開頁都不需要先讀東西，直接到個股因子，然後都不要快取」）：
  App 不再阻塞等 `/api/meta`（讀資料湖、實測單次 138s）——立即以內嵌 meta 渲染、`/api/health`
  （毫秒級）判定 live、meta 背景載入補欄位；預設頁改「個股因子」（全 PG 直查、毫秒級）；
  Splash 移除。維持全面不快取。另修復審查確認的 5 項缺陷：K 線圖打字重建（RangeCandle
  useMemo 穩定 identity）、composite-top as_of 與回列跨語句競態（as_of 改取自回列）、換股
  瞬間舊 K 線殘影（OHLC state 以 sid 標記，個股/市場頁皆修）、離線追蹤股 K 線無聲消失（補
  占位訊息）、主力副圖單位標示（張）。

- 2026-07-10 個股頁三則增強（使用者指示）：(1) K 線副圖化——lightweight-charts 升 v5 原生
  pane，K 線下疊「量 / 綜合因子分位（signed-z 棒+分位線）/ 主力買賣超（日淨買超條+累計線，張）」
  三副圖共用時間軸與十字線；新端點 `GET /api/stocks/{sid}/broker-flow`（PG broker_trades 每日
  SUM(buy−sell)）；獨立「綜合因子分位趨勢」面板併入副圖後移除。(2) 追蹤股——個股因子頁
  ☆/★ 追蹤按鈕 + 追蹤 chips 列（localStorage 本機持久化、預設選股優先追蹤第一檔）。
  (3) 熱門列改宇宙排名——新端點 `GET /api/composite-top?n=10`（composite_daily 最新物化日
  pct 遞減 + stock_meta 名稱），chips 顯示名次，PG 未物化退回快照熱門。SPEC-009 §4 追加
  兩端點。閘門後端 ruff+mypy+pytest 綠（PG 整合測試含 broker-flow/composite-top，無 DSN skip）、
  前端 oxlint+tsc+build 綠。

- 2026-07-10 全鏈路移除快取（使用者裁決「都不要快取，每個都到 PG query」）：
  **後端**拔掉 `monitoring/api.py` 的 `_memo`/`_cache`（原以「段名+湖根+最新行情日+config
  指紋」為鍵，同一交易日內重覆請求命中舊值——PG/湖盤中更新看不到的根因），所有儀表板端點改
  每請求即時現算/直查；`stock_api.serving_cfg()` 亦不再按 mtime 快取，改每請求重讀
  serving.yaml。個股端點本就每請求直查 PG，行為不變。**前端** `App.tsx` 拔掉 `useResource`
  的 module 級 `resCache`/`resInflight`（改每次掛載/換鍵即時重打），個股頁/市場頁的
  `cache[sid]` OHLC map 改單一 `series` state（換股即重抓、離線退回內嵌樣本）。端點 payload
  形狀不變。代價：重運算端點（全因子 IC/回測/容量）每請求重算、換頁/換股每次重打。閘門後端
  ruff+ruff-format+mypy(本地 bs4 未裝的既有紅除外)+pytest 254 綠、前端 oxlint+tsc+build 綠；
  瀏覽器實測：換頁 StrictMode 雙打證無 module 快取、回到已看過的個股仍重抓 ohlc、0 console error。

- 2026-07-10 T-034 完成：P7 因子計分卡加 250 日 z-sparkline 欄（一次抓全因子歷史）、P8 綜合
  因子分位趨勢面板（棒=signed-z 均值、線=宇宙分位）。`factors/history` 端點改「每因子最近 N 個
  物化點」（row_number 視窗，與 composite-history 同語義——稀疏歷史仍回滿序列）。個股頁八面板
  （Header/K線/法人/融資券/營收/主力/計分卡+sparkline/綜合趨勢）全數上線且全 PG。
  閘門 ruff+mypy(124)+pytest 250；前端 oxlint+build；實測 mom_20d 250 點/composite 250 日。

- 2026-07-10 sync 補「部分分區」偵測：日期已在 PG、事後同一分區又補了新來源檔（如 TWSE 晚於
  TPEX 進同一天）→ 以分區檔 mtime 晚於 sync_state.last_synced_at 偵測並重同步該日（新增
  對應測試）。一次性 rebuild prices（941 日/5.70M 列）修復 2026-07 上市股缺日（2308 等 K 線
  斷點根因——PG 該日只有上櫃來源）。閘門 ruff+mypy+pytest 全綠。

- 2026-07-10 個股 API 改「只讀 PG、移除 DuckDB 降級」（使用者裁決）：`stock_api` 全部端點一律讀
  serving PG——無該股資料回空、PG 連不上讓端點 503，不再降級資料湖（消除 PG↔湖不一致的混淆）；
  衍生欄抽成純函數（derive_institutional/margin/revenue/brokers）便於單元測試；前端 K 線改讀
  PG `/ohlc`、`Src` 型別收斂為 `'pg'`（UI 不再出現 duckdb）。SPEC-009 §4/AC-5/AC-6 同步更新。
  閘門 ruff+mypy(124)+pytest 全綠；docker api/frontend 重建上線，6 端點實測 source=pg、前端 0 個 duckdb。

- 2026-07-10 供應層三則修正：(1) `serving sync` 改為「比對 PG 已有日期、補齊資料湖所有尚未進
  PG 的分區」——含事後 backfill 的舊日期，不再只認 watermark（補完舊史直接 sync 即可，免 rebuild）；
  (2) 前端 K 線改讀 PG `/ohlc`（去重、與其餘面板同源），並 rebuild prices(880 日)/法人/融資券/
  營收 使 PG 與資料湖一致；(3) 修 broker_trades 舊 PoC 分區 Big5 亂碼（重爬＋rebuild broker）。
  閘門 ruff+mypy(124)+pytest 251（新增 backfill 舊日期同步測試）。

- 2026-07-10 T-034（續）個股頁首總覽列（名稱/產業/最新價/漲跌/52週位置/綜合分位，讀
  `/overview`）+ 全端上線：docker api/frontend 映像重建、compose 資料湖掛載改用
  `TWQUANT_DATA_HOST` 環境變數（確定性掛主 repo 真實湖，解決 worktree 空 data 問題）。
  :8899 個股頁八面板中的 Header/K線/法人/融資券/營收/主力/計分卡全部即時 PG 資料。
  frontend oxlint+build 過；api /overview 實測 2330 source=pg。

- 2026-07-10 T-035（部分）主力券商買賣超：`broker_trades` 進 serving 鏡射 +
  `/api/stocks/{sid}/brokers` 端點（近 window 日彙總買/賣超前 15，PG 優先降級 lake）+ 前端 P6
  面板（富邦DJ 母公司層級 top-15±）。爬蟲驗證可用、全市場當日爬取進行中。閘門 ruff+mypy(124)+
  pytest 250；frontend build 過。

- 2026-07-10 T-034（部分）前端個股頁：個股因子頁加法人買賣超/融資券/月營收三面板（inline SVG
  棒＋疊線，讀 /api/stocks/{sid}/institutional·margin·revenue，含 source、PG 空則降級資料湖）。
  api.ts 加 v2 型別/fetcher。frontend oxlint＋tsc -b＋vite build 全過。待接 P7/P8、Header、元件抽出。

- 2026-07-10 T-033 個股 API v2（SPEC-009 §4）：`monitoring/stock_api.py` + 8 個
  `/api/stocks/{sid}/*` 端點（overview/ohlc/institutional/margin/revenue/factors/history/
  composite-history），PG 讀取優先、空或失連自動降級 DuckDB 並標 `source`；scorecard 改讀
  factor_values（payload 不變）。serving.yaml enabled→true。閘門 ruff+mypy(124)+pytest
  249 passed；CI 無 PG 仍綠（降級/skip）。

- 2026-07-10 T-032 因子物化（SPEC-009）：`data/serving/materialize.py` 把 21 因子經 PitView
  對全宇宙算好（value/z/pct/signed_z/composite）落地 factor_values/factor_cross_stats/
  composite_daily；CLI `serving materialize`；每日 pipeline 尾波加供應層步驟（best-effort）。
  閘門 ruff＋mypy(122)＋pytest 240 passed（AC-3 與 PitView 現算一致 1e-9）；真實湖 smoke
  2 日各 ~32k 因子列、單股計分卡查詢 6ms。

- 2026-07-10 T-031 PG 供應層底座（SPEC-009）：`ServingConfig` + docker `pg` 服務 +
  `data/serving/`（schema 六鏡射表＋sync_state DDL、store psycopg 連線、sync watermark
  增量 upsert＋rebuild）+ CLI `serving sync/rebuild/status` + IO 邊界擴充（純運算層禁
  import psycopg／serving）。psycopg 進選用 `serving` 群組；無 PG 的 CI 跳過整合測試仍綠。
  閘門 ruff＋mypy(120)＋pytest 239 passed；真實湖 smoke 鏡射 5.24M 列、單股查詢 5.4ms。

- 2026-07-10 個股頁改版立案（PRD-001 / SPEC-009）：新增產品需求 `docs/PRD-001-stock-page.md`
  （看盤式個股頁八面板：法人/融資券/分點/集保/月營收/因子計分卡+歷史）與工程規格
  `docs/specs/SPEC-009-serving.md`（PostgreSQL 衍生讀取層——Parquet 湖仍為唯一真相源與
  PIT 基準，PG 僅供展示、可 rebuild、禁回饋因子計算）。TASKS.md 登記 T-031~T-037（分三期）；
  SPEC-000 配置白名單加 `configs/serving.yaml`、.env 加 `TWQUANT_PG_DSN`。純文件，不動程式。

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
