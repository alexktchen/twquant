# DECISIONS — 決策紀錄

規格模糊、文件矛盾、測試基準疑義，都在這裡記錄與裁決。
格式：D-編號、日期、狀態（待裁決/已裁決）、問題、選項、裁決與理由。

## 待裁決

### D-021（2026-07-11，待裁決）CGO/overhang 因子（T-039）的周轉率權重資料
- 問題：STRATEGY §4a 的參考價權重需個股**日周轉率 = 成交股數 ÷ 在外流通股數**；
  資料湖尚無在外股數（T-014 的本地 SMB/HML/市值中性化同卡此欄）。另參考價需
  **還原價**序列（T-011 待補項），為共同硬前置。
- 選項：
  - A. 先接在外股數/股本來源（TWSE OpenAPI 每日市值表或 MOPS 股本；一併解鎖
    T-014 中性化基準與 SMB/HML——綜效最大，但多一條 ingest 管線）
  - B. 第一版以「視窗內成交量佔比」近似權重（不需新資料即可動工，但偏離 GH/An
    定義，權重失去「最後成交機率」語義，論文對照性打折）
  - C. B 先行驗證 IC 有無訊號、A 為正式定義後補（B 作穩健性對照保留）
- 建議：C。影響範圍：T-039、T-014（中性化基準）、SPEC-001（若採 A 需新表
  shares_outstanding 或 stock_meta 日頻化）。

## 已裁決

### D-020（2026-07-11，已裁決 B+C）composite_daily 綜合分無預測力——採實證有效子集 + 前端標示
- **衍生韌性修補（2026-07-11，已落地）**：調查 D-020 時發現兩個耦合的靜默失效——
  (1) `standardize` 的 winsorize/zscore 以 polars `.over` 算 median/mean/std，群內任一 NaN
  會傳染成整個 (date,factor) 橫斷面 NaN，再被 `_compute_universe` 的 `filter(is_finite)`
  丟棄，使某因子在約半數交易日無聲消失；(2) `_compute_universe` 以 bare `except: continue`
  吞因子例外，讓上述缺口不可見。已改：standardize 群內忽略非有限值估統計量、退化股自身
  維持非有限僅它被過濾；物化吞例外改記日誌（DataError→DEBUG、非預期→WARNING 附堆疊）。
  補屬性測試（單一 NaN 不汙染同群）＋物化日誌測試（見 CHANGELOG 2026-07-11）。
- **裁決（2026-07-11，使用者）**：採 **B（縮減綜合分為實證有效子集）+ C（前端明確標示
  「非預測訊號」）**，**不採 A**（滾動 IC 加權，20d 樣本外崩潰、過擬合風險高）。
  - 子集 = `{price.reversal_5d, price.residual_reversal_5d, fundamental.revenue_yoy}`
    （短反轉＋產業殘差反轉＋月營收 YoY；T-038 樣本外 5d t=+8.58、20d t=+4.88，兩 horizon
    過 D-006 promote；且有 STRATEGY.md §1 先驗）。子集入 `configs/serving.yaml.composite_factors`
    （黃金規則 8，不寫死）；`materialize.py` 與 `snapshot.py` 共用純函數
    `data/serving/composite.py` 算「子集 signed_z 均值＋宇宙百分位」，維持單一真相源。
  - 體檢卡仍展示全部因子的值/z/百分位，僅**綜合分**改用子集；SPEC-009 §3 同步、
    tests 補子集斷言；前端 P8 綜合分位趨勢/宇宙熱門列/體檢卡加標示。
  - **上線注意**：既有 `composite_daily` 為舊全因子物化，改版後需
    `serving rebuild --tables composite_daily`（或重跑 materialize 區間）才反映子集。
  - 若日後仍要走加權（A 家族），須改收縮/貝式框架並以 T-019 `walk_forward` 逐窗驗證
    promote，非固定 252 窗直接上線。

### D-020 分析與證據（原待裁決內容，保留供追溯）
composite_daily 綜合分無預測力——動能/量能因子實證 IC 與宣稱方向相反
- 發現（2023-01~2026-07 RankIC 檢驗，四碼普通股宇宙）：`composite_daily.signed_z_mean`
  為 19 個註冊因子 signed_z（z × direction）的**等權平均**（`data/serving/materialize.py`，
  SPEC-009 §3）。其中動能類（`price.mom_1d`、`price.mom_20d`、`price.mom_60d`、
  `price.high_52w`、`price.rs_market_20d`）與量能類（`volume.turnover_z_20d`、
  `volume.vol_ratio_5d`）七個宣稱 direction=+1 的因子，實證 IC 在宣稱方向上**顯著為負
  （t < −2）**，與反轉類（`price.reversal_5d`、`price.residual_reversal_5d`，dir=−1）/
  營收類（`fundamental.revenue_yoy`）的正 IC 互相抵銷，綜合分 5 日 IC ≈ −0.009、
  20 日 IC ≈ −0.004——統計上無預測力。
- 與既有文件的一致性：此發現**不意外**。STRATEGY.md §1-1 已載明美式橫斷面動能在台股
  無效（Chui/Titman/Wei 2010，反轉主導）；T-013 完成註記早已實測 `price.mom_20d`
  5 日 t=−2.32、T-014 註記量能因子 5 日偏負。問題在 composite 把「已知在台股反向」的
  因子仍以 +1 等權灌入。`snapshot.py` 體檢卡 docstring 與前端體檢卡表格已聲明
  「描述性定位，非預測力」，但**綜合分本身的出口**（composite-top 宇宙熱門列、個股頁
  P8 綜合分位趨勢）未帶此標示，易被誤讀為選股訊號。
- 選項：
  - **A. 滾動實證 IC 加權**：以滾動視窗（如 252 日）PIT 估各因子 RankIC 符號/大小做
    權重取代等權。權重估計**只准用 as_of 之前的資料**（黃金規則 1；權重時序落 PG 供
    稽核），否則是把未來 IC 灌進當日綜合分。預測力上限最高，但引入估計噪音與過擬合面；
    SPEC-009 §3、materialize、snapshot/stock_api、前端文案與測試全要同步改。
  - **B. 縮減為實證有效子集**：綜合分只納入通過 D-006 門檻（|t| ≥ `ic_tstat_screen`=2，
    正式 ≥ `ic_tstat_promote`=3，門檻引用 configs 不另寫死）的因子——候選：短期反轉
    `price.reversal_5d`、殘差反轉 `price.residual_reversal_5d`、月營收
    `fundamental.revenue_yoy`；其餘因子仍展示於體檢卡但不進綜合分。改動面小於 A、
    語義清楚；子集選擇本身即樣本內決策，仍需樣本外驗證。
  - **C. 維持描述性定位**：演算法不動，前端在綜合分、composite-top 熱門列、綜合分位
    趨勢明確標示「綜合分為橫斷面相對定位，非預測訊號」（與 snapshot.py docstring 對齊）。
    零過擬合風險、零回歸面，但熱門列繼續以無預測力的分數排序。
- 過擬合防線（走 A/B 的前提）：以樣本內（如 2023-01~2025-06）估權重/選子集、
  留出樣本外（如 2025-07~2026-07）驗證期，樣本外 IC t-stat 過 D-006 門檻才上線；
  驗證由 T-038 產出報告後回此條裁決。A/B 任一採行都需同步 SPEC-009 §3、PRD-001、
  snapshot.py docstring 與對應測試（體檢卡「描述性」聲明若失真即改寫）。
- 影響範圍：`data/serving/materialize.py`（composite 計算）、`monitoring/snapshot.py`
  （scorecard 綜合宇宙排名）、`monitoring/stock_api.py`（composite-history/
  composite-top）、`frontend/src/App.tsx`（P8/熱門列文案）、SPEC-009 §3、
  tests/test_serving_materialize.py。C 為純前端文案，與 A/B 不互斥、任何裁決下皆可先行。
- **實證證據（T-038，2026-07-11）**：全報告見 [docs/research/COMPOSITE-IC-D020.md](docs/research/COMPOSITE-IC-D020.md)。
  PIT-safe 重跑（worktree 21 因子、資料湖 2023-01~2026-07、IS=2023-01~2025-06 / OOS=2025-07~2026-07）：
  - **發現成立**：等權綜合分樣本外無力——5d IC=−0.0047（t=−0.88）、20d IC=+0.0005（t=+0.09）；
    全期 5d IC=−0.0072、20d=−0.0016，忠實重現回報值。機制確認：5d 有 8 個動能/量能因子
    signed-t 顯著為負、7 個反轉/營收因子顯著為正，等權相抵。
  - **B（有效子集）最穩健**：預設子集（reversal_5d＋residual_reversal_5d＋revenue_yoy）
    OOS 5d IC=+0.0549（t=+8.58）、20d IC=+0.0256（t=+4.88）；資料驅動子集 OOS 5d t=+9.03、
    20d t=+3.24。**兩 horizon 樣本外皆過 D-006 promote（t≥3）**，且有 STRATEGY.md §1 先驗。
  - **A（滾動 IC 加權，win=252）有過擬合風險**：5d OOS t=+6.78 但 IS→OOS 衰減 41%；
    **20d OOS 從 IS t=8.63 崩到 t=+0.04（完全蒸發）**——長 horizon 的滾動 IC 估計隨 regime
    反覆，不穩健。
  - **建議**：採 **B 重定義綜合分 + C 前端標示無論如何先行；不建議 A**（若日後走加權須改
    收縮/貝式框架並以 T-019 walk_forward 逐窗驗證，非固定窗直接上線）。
  - Caveats：法人/融資券 2023-2024 覆蓋缺口（chip 因子分析窗大多缺席，近端 2025-2026 在場，
    上線前需複核近端子期）；未還原價（T-011 待補，與生產因子同基礎故比較公平）；3 個 chip
    因子 + mom_1d 在分析窗 IC 全 NaN（無覆蓋/近退化）。
  - **已裁決 B+C（見本條頂部）**：研究完成、證據已備，實作依 CLAUDE.md §6 於裁決後展開。

### D-019（2026-07-09，已裁決）broker_trades 分點來源改走免費富邦DJ（取代 D-001）
- 問題：D-001 裁定分點走 FinMind **付費** API；但 FinMind 的分點 dataset 屬贊助會員，
  成本與額度是門檻。既有 legacy `cloudDailyReport` 已有可用的富邦DJ 分點抓法，能否改走
  免費來源接上 canonical `broker_trades`（schema 早已定義，T-010/T-016 待接）。
- 選項：A. 照 D-001 走 FinMind 付費；B. 改寫富邦DJ 免費爬蟲成 twquant 來源；C. 只先評估。
- 裁決：**B，取代 D-001 的來源選擇**（D-001 clause B「禁止驗證碼自動破解」仍有效；
  富邦DJ 是公開頁面爬取，與 TPEX/TDCC 既有爬法同性質，非破解）。新增
  `data/sources/fubon_chip.py`（`fetch → parse_broker_rows → rows_to_canonical`，注入式
  HttpClient、內建 html.parser 解析）+ CLI `backfill-fubon-chip`。
- 來源特性與限制（寫進因子設計前提，重要）：富邦DJ zco 頁為**母公司層級 top-15 買超 +
  top-15 賣超**彙總（實測會混入少數本土分點如「新光-台中」），故 canonical：
  `broker_id` = **券商名稱**（無官方代號）、`avg_price` = **null**（頁面無均價）、
  單位張 ×1000 轉股、`available_at` = 交易日 **18:00** 台北（盤後，比照 prices/institutional，
  D-008 訊號截點 20:00 內同日可見）。**無分公司(分點)全貌、無鉅額/隔日沖明細** → T-016
  的「總公司排除／分點集中度全譜」不可由此來源完全計算；需分點全貌時另接官方 bsr/付費源。
- 架構歸屬：**不註冊為 daily SourceSpec**。富邦DJ 每檔每日、全市場一天 ~1800 請求屬批量
  回補，由專屬 CLI 指令驅動（比照 finmind `backfill-finmind`、非每日編排迴圈），限流
  `source_rates.fubon`（1 req / 2s）。承接 D-011/D-014 的 (table,date,source) 分片替換：
  逐日累積全股後**該日一次**寫 `part-fubon.parquet`（Warehouse 分區級替換 → 逐股寫會互蓋）。
- 範圍：來源模組 + 回補指令 + 離線 fixture 測試。`backfill-fubon-chip` 支援 `--stocks` 指定
  個股，或**留空＝全市場**（股票池讀資料湖 stock_meta→prices_daily，`^[1-9][0-9]{3}$` 過濾、
  >100 檔先確認、`--data-dir` 可指向已populated 的資料湖）。`chip.concentration_*` 因子待 T-016。

### D-018（2026-07-09，已裁決）資料後台「一鍵補抓」——白名單指令 + 伺服器端執行
- 問題：D-016 定監控 API 唯讀、補抓走 CLI（複製建議指令、自己開終端貼上）。實務上
  漏抓多為零星單日/單表，來回切終端摩擦大；希望在後台直接按鈕補抓。
- 選項：A. 維持唯讀、只複製指令（現況）；B. 開放任意指令執行端點；C. 白名單化——
  只由缺口識別 (table, source, from, to) 推導 CLI 子指令，伺服器端以子行程執行。
- 裁決：C。新增 `monitoring/jobs.py`（`build_args` 白名單推導 argv、`JobManager`
  單一併發 + 尾行緩衝）與 `POST /api/ingest-jobs`（+ `GET` 清單/單筆輪詢）。CORS 放行
  POST。前端漏抓清單每列加「一鍵補抓」鈕，3 秒輪詢工作輸出，工作結束自動刷新矩陣/缺口。
- 邊界（收斂放寬 D-016，而非推翻）：**不接受命令字串**（指令全由白名單枚舉推導，無注入
  面，非白名單 table/source 直接 400）；**單一併發**（資料湖寫入不併發、避免疊加免費來源
  請求量，第二個工作 409）；**D-015 護欄**（TWSE 行情回補 >20 交易日需前端 confirm + `force`
  才放行）。工作狀態存行程記憶體（重啟即清），成敗真相仍落 `ingest_log`。查詢端點維持唯讀。
- 理由：把「建議指令」的既有安全模型（枚舉 + D-015 警示）直接複用為執行白名單，摩擦大降
  而攻擊面不增；子行程走同一 `python -m twquant`，與手動 CLI 完全同路徑、無第二套語義。

### D-017（2026-07-07，已裁決）擷取層 SOURCE 與 STORAGE 雙插件化 + TDCC 股權分散
- 問題：來源與儲存後端寫死在 `cli._ingest_date`（`plans=[('twse',...),('tpex',...)]`
  字面量 + 直接呼叫具體 `Warehouse`）。未來換來源（官方/FinMind/TDCC）或換儲存
  （Parquet+DuckDB→Postgres）需散改多處。使用者要求「盡量規劃成 adapter，未來可任意
  換掉來源與儲存」。
- 裁決：於 CLI 編排邊界導入兩個 minimal seam（`data/ingest/ports.py`），**分析讀取側
  完全不動**：
  1. **IngestSink**（儲存，唯寫）：`write / partition_exists / source_dates /
     latest_date`，方法名與簽名**逐字對齊 `Warehouse`**，故 `Warehouse` 以結構型別
     **零改動**即滿足（`runtime_checkable`，有測試 `isinstance` 驗證）。`source` 為
     不透明 disjoint-writer 標籤；part-<source>.parquet vs DB 交易 DELETE+INSERT 屬
     adapter 細節。
  2. **SourcePort**（來源，自足）：`name/tables/load()`（=抓取+清洗+驗證）。既有
     twse/tpex 以 `FetchCleanSource` 包裝現行 `Source`+cleaner **零改寫**。
  3. **註冊表**（`data/ingest/registry.py`，比照 `factors/registry`）：`register/get/
     list_specs(cadence)/_clear`；`SourceSpec(source_tag, build, cadence)`；副作用匯入
     `builtins.py`。新增來源 = 一檔 + 一筆 register，不改編排核心。
  4. **編排器**（`orchestrator.run_ingest`）：只依賴埠，用假 sink/假來源可單元測試
     （同時驗證來源與儲存皆可換）。`cli._ingest_date` 每日迴圈改由 `list_specs('daily')`
     驅動。
- 刻意界線：分析讀取側 `scan/sql/connect/view/PitView`（backtest/calendar/monitoring/
  strategy/factors 廣泛使用，且為 D-008 PIT 憲法邊界）維持具體 `Warehouse`、不抽象化
  ——抽象化將觸及約 8 模組與所有 PIT 測試、逾 CLAUDE.md §7 400 LOC 上限且無正當性；
  未來採 Postgres 時另開 `ReadPort` 條目。
- 首個落地：`TdccDispersionSource` + `clean_shareholding_dispersion` 寫
  `shareholding_dispersion`（`ingest-dispersion` 指令，cadence=weekly；基準日取自 CSV
  資料日期欄；available_at=公布週六12:00 依 SPEC-001 §1；16=差異數/17=合計 剔除）。
  **實作發現（重要）**：opendata `getOD.ashx?id=1-5` 目前僅含 6 碼證券（ETF/受益證券/
  債券，實測 3350 檔），**不含四碼普通股**（2330 等）。四碼普通股的集保戶股權分散表
  僅 TDCC 互動查詢站（表單 POST）提供，屬未來另一個 SourcePort——正是本可插拔設計要
  讓其「一檔+一筆註冊」即可加入的情境。散戶密集度因子（T-015/T-016）若需普通股集保
  分散，待該來源接上。承接並形式化 D-014/D-011 的多來源分片替換語義為 (table,date,source)。

### D-016（2026-07-07，已裁決）監控前端改即時 API、退役 Streamlit
- 問題：監控有兩套重疊儀表板——Streamlit（`monitoring/dashboard.py`，即時讀資料湖，
  埠 8501）與 React 前端（`frontend/`，靜態 data.json 快照，埠 8899）。維護雙份、
  且「即時」與「快照」語義分裂。
- 選項：A. 整併到 React、退役 Streamlit，另建唯讀 JSON API 供前端即時 fetch；
  B. 只補 React 到功能對等、兩者並存；C. React 走靜態快照、退役 Streamlit（喪失即時）。
- 裁決：A。新增 `monitoring/api.py`（FastAPI，唯讀 GET）＋ `monitoring/snapshot.py`
  （API 與 `export_snapshot.py` 共用的建構函數＝單一真相源）；React 改即時 fetch `/api`，
  API 不可達時退回內嵌 data.json（離線單檔仍可用）。刪 `monitoring/dashboard.py`；
  pyproject 的 `dashboard`(streamlit) 群組換成 `api`(fastapi/uvicorn)。docker-compose
  的 `dashboard` 服務換成 `api`（:8000），`frontend` 的 nginx 反代 `/api`。個股選單
  （前 150 檔）、擷取失敗明細、回測期間選擇等 Streamlit 專屬互動一併移入前端。以
  `twquant serve` 啟動 API。
- 理由：單一 UI、單一資料路徑；FastAPI 全型別（mypy strict 免 override，不像
  streamlit 需忽略）；API 每次查詢開/關 DuckDB 連線、不長期持有生產分區檔 handle
  （順帶緩解 REVIEW H22 的 Windows 檔鎖疑慮）。取代 D-013 中 `dashboard`(Streamlit) 服務。

### D-001（2026-07-04，已裁決）分點資料來源
- 問題：官方 bsr/TPEX 分點查詢有驗證碼與 anti-bot，如何取得 broker_trades？
- 選項：A. FinMind 付費 API；B. 驗證碼自動破解；C. 半自動人工輸入
- 裁決：A。B 明確禁止（法律與穩定性風險）；C 只作為個別股票補資料的手段。

### D-002（2026-07-04，已裁決）回測與模擬倉的成交假設
- 問題：PaperBroker 是否獨立實作成交邏輯？
- 裁決：不獨立。PaperBroker 直接 import 回測引擎的成交模組（SPEC-006 AC-1
  鎖定），否則「實盤 vs 回測」漂移偵測會混入兩套假設的差異，失去意義。

### D-003（2026-07-04，已裁決）第一版範圍
- 裁決：現股多頭、日頻、盤後制。不做：做空、信用、當沖、日內、零股。
  介面（權重正負、Order.side）預留擴充。

---

以下（D-004~D-011）為 2026-07-05 規格對抗審查後的裁決，逐項回應
[docs/research/REVIEW.md](docs/research/REVIEW.md)。

### D-004（已裁決）波動目標的唯一歸屬（REVIEW H5/M3）
- 問題：STRATEGY §2 Overlay-2（條件式）與 SPEC-005 vol_target（無條件）語義矛盾，
  且 T-017 與 T-020 會各實作一次造成雙重縮放，兩邊測試各自通過、check 攔不住。
- 裁決：波動縮放**只存在於 SPEC-005 投組層（T-020）**，採**條件式**版本
  （僅波動落在 252 日 >90 百分位才降曝險）。T-017 overlay 不做波動縮放。
  新增跨模組 AC：全 pipeline 波動縮放係數只被套用一次（golden 端到端驗證）。

### D-005（已裁決）參與率分母的唯一定義（REVIEW H12）
- 裁決：回測與實盤都用「過去 20 交易日成交金額中位數（ADV）」作參與率分母；
  回測另以當日實際成交金額做事後上限截斷並記入 unfilled。ADV 統一為 SPEC-003
  的「20 日成交金額中位數」，LOOP/SPEC-004/STRATEGY 全引用此定義。

### D-006（已裁決）IC 門檻兩階段（REVIEW M2/M15）
- 裁決：單因子初篩 RankIC t-stat ≥ 2 進候選；正式上線 ≥ 3。兩數字放
  configs（`ic_tstat_screen`、`ic_tstat_promote`），LOOP §1 與 STRATEGY §1-8
  引用同一鍵，不再各寫一個值。

### D-007（已裁決）文件優先權順序（REVIEW H11）
- 裁決：衝突時權威順序 `data/schemas.py > 模組 SPEC > ARCHITECTURE（示意）
  > 其他`。ARCHITECTURE §3 簽名視為示意，以各 SPEC 為準。寫入 SPEC-000。

### D-008（已裁決）訊號生成 PIT 截點（REVIEW H3/H6/H17/H18/M25/M30）
- 問題：回測 view(as_of=date) 取 23:59、生產 18:00 產訊號，兩軌可見資料集不同
  （融資 21:00、月營收 22:00、TDCC 週六），是內建漂移來源，反前視測試不會攔。
- 裁決：定義訊號生成截點。每交易日訊號的 as_of = 當日 20:00（含 18:00 盤後，
  **不含** 21:00 融資 → 融資以 T-1 進當日訊號）。回測 date 型 as_of 一律 coerce
  到 20:00 而非 23:59。資料擷取改多波次（LOOP §3）。新增 AC：回測與生產對
  同一 as_of 的 PitView 可見列集合 bit-identical。

### D-009（已裁決）kill-switch 四態狀態機（REVIEW H10/H34）
- 裁決：擴為 `NORMAL → NO_NEW_ENTRY → REDUCED(50%) → HALTED`。單日虧損 >3%
  → NO_NEW_ENTRY（持倉不動、禁止任一權重上升）；回撤 >15% → REDUCED；
  回撤 >20% 或品質閘門連 3 日不過 → HALTED。回撤高點基準 = 上線以來權益高
  水位。HALTED 僅人工恢復。SPEC-005 §2 逐條列觸發器→狀態→恢復→持久化＋AC。

### D-010（已裁決）portfolio_state 移出可重建層（REVIEW H20）
- 裁決：狀態與凍結旗標移出 `data/signal/`，改放新增的 `data/state/` 專層
  （SPEC-000 標記不可重建、須備份）。單一 state manager 序列化＋原子寫；
  狀態檔缺失/損毀 → fail-safe HALTED＋CRIT，禁止靜默回 NORMAL。

### D-011（已裁決）資料湖寫入協定（REVIEW H19/H21/H22/M31/M32/M33）
- 裁決：新增 SPEC-001 §7「寫入協定」，統一規範原子寫入（temp+rename）、raw
  版本化與選版、log 類 append-only 分片、跨分區 union_by_name、Windows 檔鎖、
  schema 演進、磁碟治理。

### D-015（2026-07-05，已裁決）大量歷史回補走 FinMind，官方端點供每日擷取
- 發現：一年 TWSE 回補跑到約第 130 天（累積約 390 請求）後，TWSE rwd 端點開始
  回空白/HTML（IP 暫時封鎖），即使限流在 3 req/5s 內——TWSE 對「持續大量」請求
  有超越單純 rate 的封鎖機制。TPEX（不同基礎設施）與 MOPS 未被擋。
- 裁決：（1）**每日擷取**（scheduler，每日約 4 請求）用免費官方端點，不會觸發封鎖；
  （2）**大量歷史回補**改走 FinMind（付費，設計給批量歷史，無封鎖風險，且統一
  TWSE+TPEX+分點）——與 D-014 一致；（3）免費端點的 backfill 保留作為補洞用，但
  需分小塊、加長間隔，且預期 TWSE 會被暫時擋。
- 現況：TPEX 上櫃 243 交易日（完整年）、TWSE 上市 142 日（2025-07~2026-01-02）、
  月營收 12 月完整。TWSE 缺口待 ban 解除後補跑，或改由 FinMind 一次補齊。

### D-014（2026-07-05，已裁決）TPEX 上櫃資料來源分工
- 問題：TPEX 有 anti-bot；法人/融資券的歷史端點難尋（OpenAPI 僅回最新日，
  帶 date 的 afterTrading 端點名不易找）。
- 裁決：T-008 聚焦 **TPEX 行情**（afterTrading/dailyQuotes，帶瀏覽器 headers，
  支援歷史日期、附次日漲跌停價），與 TWSE 上市共同寫入 prices_daily（按來源
  分檔 part-<source>.parquet 共存）。TPEX **法人/融資券的歷史資料改由 FinMind**
  （T-010，統一 TWSE+TPEX）提供，避免與 TPEX latest-only OpenAPI 的日期不符前視。
- 倉儲改動：Warehouse.write 加 source 參數，同表同分區各來源寫各自 part 檔、
  掃描時全讀，解決多來源覆蓋問題。

### D-013（2026-07-05，已裁決）Docker infra 與資料庫選型
- 問題：infra 服務容器化；資料庫要 DuckDB/Parquet 還是 DB 伺服器？
- 裁決：**維持 DuckDB + Parquet**（嵌入式、單機、零維運，符合原架構），資料湖
  掛在 Docker volume。docker-compose 三服務共用同一映像：app（執行 CLI）、
  scheduler（每日排程跑 ingest→pipeline）、dashboard（Streamlit，SPEC-007）。
  不引入 Postgres/Timescale（多人/服務化才考慮，屆時走 DECISIONS）。
- scheduler 與 dashboard 為 v1 骨架：scheduler 目前跑 `twquant daily`（單日 ingest，
  容忍非交易日），dashboard 讀倉儲顯示資料狀態與法人流向；完整版分別在 T-023、T-024。

### D-012（已裁決）會計恆等式修正（REVIEW H7/M5）
- 問題：SPEC-004 AC-6「現金＋持倉市值−累計成本 == 權益」重複扣成本（成本成交
  時已從現金扣除），照字面寫會把錯誤會計凍進 golden。
- 裁決：AC-6 改為「權益(t)=現金(t)+持倉市值(t)；且 權益(t)−權益(t-1)==當日持倉
  損益−當日成本（誤差<1e-6）」。equity curve 定義為含成本淨值、成本於成交時
  自現金扣除。
