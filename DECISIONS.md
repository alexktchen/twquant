# DECISIONS — 決策紀錄

規格模糊、文件矛盾、測試基準疑義，都在這裡記錄與裁決。
格式：D-編號、日期、狀態（待裁決/已裁決）、問題、選項、裁決與理由。

## 待裁決

### D-031（2026-07-29，待裁決）雲端回測端點的資料範圍
- 問題：`/api/backtest` 現為即時計算，需要完整歷史；搬遷後 Supabase 只有滾動窗口
  （prices 260 交易日、factor_values 60 日），全史回測無資料可用。
- 選項：
  - A. 雲端版限制回測區間 ≤ 窗口長度，超出回 400 並附提示文案（「全史回測需本機
    還原 Drive 歸檔」）——零額外基礎設施
  - B. 回測改離線：`workflow_dispatch` 觸發 GHA 跑回測（GHA 內臨時還原歸檔到
    service PG），結果落 Supabase 供前端輪詢——全史可用但複雜度高
  - C. A 先行、B 視使用頻率再開任務
- 建議：C。影響範圍：T-061、`monitoring/api.py` backtest 端點、前端策略回測頁文案。

### D-032（2026-07-29，待裁決）GHA 分鐘數預算——repo 維持 private 或轉 public
- 問題：GitHub Free private repo 每月 2000 分鐘；每日 pipeline 估 20–30 分鐘/日
  → 月耗 ~600–900 分鐘，夠用但不寬裕（加上補跑、回測、CI 會更緊）。public repo
  分鐘無上限，但程式碼公開（token 本就在 Secrets，不受影響）。
- 選項：A. 維持 private，監控分鐘用量、逼近上限再議；B. 轉 public。
- 建議：A（先觀察實際月耗，資料與策略邏輯屬個人資產不宜公開）。影響範圍：T-056。

### D-033（2026-07-29，待裁決）Google Drive 15 GB 容量的長期歸檔策略
- 問題：免費 15 GB 與 Gmail/相簿共用。初始全史 pg_dump -Fc（估 2–4 GB）+ 每日
  增量（broker_trades 為大宗）長期累積，一年內可能逼近上限。
- 選項：A. 升級 Google One 100 GB（NT$65/月，唯一固定成本）；B. 定期把最舊增量
  合併進基底 dump 重壓（免費但需定期本機開機操作）；C. 增量改存第二個免費帳號。
- 建議：先按 B 的格式設計歸檔（增量可合併），容量逼近 80% 再裁決 A/B/C。
  影響範圍：T-053、T-054 的歸檔檔案佈局。

### D-029（2026-07-22，待裁決）prices_daily 停牌跳空/資料缺口使回測失真——品質閘門與還原價為回測硬前置
- 問題：T-046 引擎全期回測（reversal_revenue，2021-07~2026-07）出現 CAGR +70.9%
  幻影績效。診斷（scratchpad/bt_diagnose.py）：單日組合報酬 +301%/+221%/+170%
  （漲停 10% 下物理不可能）、前 10 大單日貢獻 +813%、獲利前三檔的交易日間隔
  574/819/1,274 天（持倉跨停牌、以復牌跳空價「獲利」）；**全部已實現損益合計
  −338 萬**（權益全掛在幻影標價持倉）。母體掃描：回測期四碼股相鄰資料列漲幅
  >2× 有 2,776 次（1,085 檔）——停牌復牌跳空＋減資＋疑似鏡射缺口混合。
  事件級回測同樣受污染（出場取「後 N 列」會跨停牌），需日曆間隔防衛重驗。
- 選項：
  - A. 資料層修復為回測硬前置：T-011 待補項（品質閘門攔非漲停跳價、corporate_events
    還原價、停牌區間標記）＋鏡射缺口盤點回補
  - B. 引擎防衛（SPEC-004 增修）：持倉價格序列出現 > N 日曆天缺口 → 以最後有效價
    強制出場並記 forced_exit；非漲停單日 |Δ| > 35% 列進 unfilled/警示
  - C. A + B（資料修復治本、引擎防衛治標並防未來資料事故）
- 建議：C。影響範圍：T-046（引擎數字作廢待重測）、T-011、SPEC-004 §3、
  所有用「後 N 列」當出場的研究腳本（已以日曆防衛重驗）。
- **裁決（2026-07-22，使用者「繼續」核可建議 C）**：採 C。B 部（引擎防衛）即刻落地
  （T-047）：`_fill` 跳價拒單（unfilled reason `price_jump`）、持倉跳價以
  min(跳價前, 跳價後) 強制出場（`_forced_exit`，幻影不入損益）、宇宙衛生濾網
  （近 60 列內跳價/缺口 >10 日曆天 → 排除；UniverseConfig 可調）；閾值 0.35 進
  configs/costs.yaml `data_guard`。A 部（品質閘門/還原價/停牌標記，T-011 待補項）
  另開任務為回測硬前置——引擎防衛是過渡措施非治本。

### D-028（2026-07-20，待裁決）基線 mypy 紅燈：tests/test_screener.py 3 錯（T-040/T-041 遺留）
- 問題：T-042 開工前基線檢查發現 HEAD 的全套 mypy 已紅（與 T-042 無關，stash 驗證）：
  `tests/test_screener.py:15` Module "twquant.monitoring" has no attribute "screener"、
  `:82` Returning Any from function declared to return "DataFrame"、
  `:205` Module "twquant.data.serving" has no attribute "schema"（皆 attr-defined/no-any-return）。
  疑似 T-040/T-041 收尾時 mypy 未跑全套或模組屬性匯出變動。
- 選項：A. 開小任務修 test_screener.py 的型別（import 路徑改直接模組匯入 + 回傳型別註記，
  預估 <20 行）；B. 併入 T-040/T-041 的複審回合處理。
- 建議：A（純測試檔型別修正，不影響行為）。依 CLAUDE.md §7 基線紅不動手修，先記錄。

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

### D-030（2026-07-29，已裁決）全雲端搬遷目標架構——GHA cron + Supabase Free + Drive 歸檔 + Vercel
- 背景：使用者 2026-07 決定本機將關機，twquant 不能再依賴本機 PG/Docker 常駐
  （取代 D-013/D-016 的本機 docker-compose 常駐前提；免費優先）。本條為 2026-07-20
  口頭裁示的正式化，並經 2026-07-29 現況盤點確認可行（本機 PG 15 GB：broker_trades
  8.9 GB、factor_values 4.3 GB、prices_daily 1.4 GB）。
- 裁決（目標架構）：
  - **每日 pipeline → GitHub Actions cron**：平日 19:05 台北（UTC `5 11 * * 1-5`）
    跑完整 `_run_daily_pipeline`；`workflow_dispatch` 取代後台「一鍵跑今日全部資料」。
    GHA cron 有 3–15 分鐘延遲，pipeline 須冪等可重跑。富邦 19:00 未公布時沿用
    「稍後重試不污染」邏輯（D-025/memory 2026-07-16）。
  - **展示層 DB → Supabase Free（500 MB）滾動窗口**：prices 260 交易日（四碼普通股）、
    broker 明細 10 日、broker_flow_daily 250 日、factor_values 60 日，估 ~370 MB。
    連線走 transaction pooler :6543、關 prepared statements。窗口大小以「因子最大
    價格回看天數」為硬下限（如 240 日動能必須被涵蓋），逐一核對 configs 後定案。
  - **raw 歸檔 → Google Drive（唯一保命線）**：初始全史 `pg_dump -Fc` 一次 + 每日
    增量 dump（尤其 broker_trades——券商分點資料過期不可再取得，歸檔鏈絕不能斷）。
    全史回測/研究 = 暫時開機還原 Drive dump。格式用 pg_dump，不用 parquet（D-026）。
  - **前端 + API → Vercel 同一專案**：React 靜態建置 + FastAPI 包成 Python serverless
    function（rewrite `/api` 取代 nginx 反代）；DSN 指向 Supabase pooler。長任務端點
    （`/api/pipeline-jobs`、`/api/ingest-jobs`）改為透過 GitHub API 觸發
    `workflow_dispatch`。Vercel 不行再退 Cloud Run。
- 已知風險（任務內處置）：Supabase Free 閒置 7 天暫停（長假加保活 job）；
  回測端點資料範圍（D-031）；GHA 分鐘數（D-032）；Drive 容量（D-033）。
- 範圍：任務 T-053~T-062。切換完成前本機 compose 照常運行（平行期比對）。

### D-027（2026-07-20，已裁決）技術型態回測走規則式因子（B1）；AI 視覺辨識僅作離線標註
- 背景：使用者提出兩個回測系統方向——「吃 XQ」與「AI 依技術線圖辨識」。評估：
  XQ 無公開程式 API、為本機 Windows 軟體，與 all-cloud 方向衝突（只能手動匯出檔案，
  進不了每日 pipeline）；LLM 看圖若進因子/回測迴圈則違反黃金規則 10（factors/ 禁連網）
  與 SPEC-004 AC-8（同輸入 bit-identical）。
- 裁決（使用者 2026-07-20）：採 B1——規則式型態偵測作為 `price.*` 事件因子（樞紐點
  確認延遲反前視），完整重用既有因子框架與回測引擎。規格 STRATEGY §4b，任務
  T-042/T-043。
  - 命名沿用 `price.*` 類別：註冊表正則本就允許、不動介面（比照 T-039 行為錨定前例）；
    不新增 `pattern` 類別。
  - AI 視覺辨識定位為洞悉層（SPEC-008）離線標註/校準工具；標籤若入庫，為含 as_of 的
    資料表（資料而非邏輯），因子只讀表——決定性與 IO 邊界皆守住。待 B1 有 IC 結果後
    再評估開任務。
  - XQ 不作為資料源；日後若需驗證 XS 策略，走「訊號重放」模式（回測引擎加外部訊號
    入口，共用 fill/costs），屆時另開任務並增修 SPEC-004。

### D-026（2026-07-14，已裁決）全面停用 parquet——因子物化改讀 PG，PitView 抽為 TableSource 協定
- 背景：本機 bind-mount 的 parquet 資料湖（`D:\project\twquant\data`）在一次排查中發現完全
  是空的，而 PG `serving.*` 卻已有數月歷史——資料湖早就沒被持續寫入。回補這幾天缺口時，
  使用者明確裁示（2026-07-14）：**全部資料（raw 表與因子）長期只落地 PG，不再有 parquet**。
- 衝突：此裁示與既有設計相牴觸——D-017（2026-07-07）明訂「分析讀取側 scan/sql/connect/
  view/PitView 維持具體 Warehouse、不抽象化……未來採 Postgres 時另開 ReadPort 條目」；
  SPEC-009 §0 明文「因子計算的唯一讀路徑仍是 PitView……PG 只存已算好的值，永不作為
  因子/回測/策略/投組的輸入」。本條即 D-017 預留的「ReadPort 條目」，正式裁定該原則
  的因子計算部分反轉；**回測/策略/投組讀取側維持原樣不動**（範圍見下）。
- 裁決：
  - `data/warehouse.py` 新增 `TableSource`（`Protocol`，鴨型別）：`scan/latest_date/
    dates_in_range/view` 四個方法。`PitView.__init__` 參數型別由具體 `Warehouse` 放寬成
    `TableSource`——`Warehouse` 零改動即結構滿足（新增 `dates_in_range` 方法對齊協定）。
  - 新模組 `data/serving/pg_view.py`：`PgTableSource`（結構滿足 `TableSource`）整表讀出
    PG serving 鏡射表 → `pl.LazyFrame`；`available_at`（PG `timestamptz` 走線為 UTC）在
    `.scan()` 內 `convert_time_zone` 正規化到 Asia/Taipei，與 parquet 路徑的 `TW_DT` dtype
    對齊，`PitView.table()` 的截點比較邏輯不必知道、也不用改。本模組不 import
    psycopg（IO 邊界：只有 `data/serving/` 得碰 PG 連線，`store.connect()` 提供）。
  - `data/serving/materialize.py` 的 `_compute_universe`/`_materialize_on`/`materialize_day`/
    `materialize_range` 参數型別由 `Warehouse` 放寬為 `TableSource`；`materialize_range`
    不再依賴 `sync.partition_dates`（parquet 分區目錄），改用 `source.dates_in_range()`
    （PG 走 `SELECT DISTINCT date ... WHERE date BETWEEN`，parquet 走既有目錄枚舉）。
  - CLI `serving materialize` 改建 `PgTableSource`（PG 直連）取代
    `Warehouse(_load_system().data_dir)`；`pipeline()` 每日流程的 `_serving_step()`
    **不動**（仍傳本地 `Warehouse`——`materialize_day` 兩種來源皆吃，向後相容，該函式的
    ingest/gate/signal 全流程改 PG-only 是另一個更大的範圍，見下）。
  - raw 表回補（prices/institutional/margin/monthly_revenue/shareholding_dispersion）已在
    本次回補中改走「來源 SourcePort 抓 → schemas.validate → `sync.upsert()` 直進 PG」，
    略過 `Warehouse.write()`；`broker_trades`（D-025）本就 PG 直寫，其 parquet 寫回
    （`serving rebuild` 安全網）暫保留、屬另一範圍。
- 刻意界線（本條**只**涵蓋因子物化的讀取路徑，其餘維持 D-017 原判）：backtest/strategy/
  portfolio/monitoring 的 PIT 讀取、`twquant daily`/`backfill`/`pipeline` 的 ingest/gate
  仍走 parquet `Warehouse`。這些模組要不要也搬離 parquet，是**另一個決策**（規模與 D-017
  估計的「~8 模組」相近），未來要做就再開一條 D-02x，不在本條隱含。
- IO 邊界（SPEC-009 AC-7）不受影響：`factors/*.py` 仍完全不 import psycopg/`data.serving`——
  它們只收一個 `view` 物件、不知道背後是 parquet 還是 PG，靜態閘門 `test_io_boundary.py`
  無需改動。SPEC-009 §0/§3 同步更新（見文件）。
- 驗證：`data/serving/pg_view.py` 對真實 PG 手動驗證過 `available_at` 時區正規化
  （PG `timestamptz` UTC → polars `Datetime[us, Asia/Taipei]`，D-008 截點比較結果與
  parquet 路徑一致）；`tests/test_pg_view.py`（新增，`TWQUANT_PG_DSN` 未設則 skip）覆蓋
  PIT 截點過濾與 `dates_in_range`。
- 範圍：`data/warehouse.py`、`data/serving/pg_view.py`（新）、`data/serving/materialize.py`、
  `cli.py`（`serving materialize`）、`docs/specs/SPEC-009-serving.md`、`tests/test_pg_view.py`。

### D-025（2026-07-12，已裁決）backfill-fubon-chip 改 PG 直寫 + fubon_fetch_log 抓取帳本
- 需求（使用者）：(1) 抓到就直接進 PG，不留實體檔案；(2) 逐 (日, 股) 續傳——每次抓新日期
  先檢查已抓過的股號跳過；(3) **多程序併跑**且檢查間隔要小，不能漏抓；(4) 新增查詢：
  以天為單位顯示已抓檔數＋佔宇宙總數的百分比。
- 裁決：帳本放 PG（資料庫本就擅長並行協調，取代檔案帳本）：
  - 新簿記表 `serving.fubon_fetch_log`（date, stock_id, n_rows, fetched_at；PK (date,stock_id)；
    比照 sync_state，不屬鏡射/因子表，sync/rebuild 不觸碰）。
  - worker 抓完一檔 → 解析 → **同一交易** upsert `broker_trades` ＋寫 log → commit。
    **log 列存在 ⇔ 資料已入庫**，故「查 log 跳過」永不漏抓；競態最壞情況＝兩程序重抓同檔
    （upsert 冪等，無害）。skip_if 在**抓取當下**逐檔查 log（非日初快照，間隔最小）。
  - 假日探測（D-024）改以 SENTINEL 列（stock_id='_day_empty'）持久化；該日事後發現有
    資料時自動 `clear_sentinel` 解除。
  - 新指令 `fubon-chip-status`：讀 log 依日彙總（已抓/宇宙總數＋百分比、有資料檔數、
    非交易日標記）。
  - **資料湖不棄守**：日終自 PG `read_day` 讀回該日完整資料（含他程序貢獻）補寫
    `clean/broker_trades` 分區。理由：湖是因子層（PitView，黃金規則 1）與
    `serving rebuild`（truncate 後自湖全量重灌）的真相源——只寫 PG 而不寫湖，一次
    rebuild 就會清光直寫資料。此為對「不用進 parquet」的刻意保留，成本僅日終一次寫入。
  - `Warehouse._write_partition` tmp 檔名帶 pid：多程序同刻寫同一分區不互踩同一 tmp。
- 煙霧驗證（本機 PG 5433）：ensure_schema 建表、mark 冪等、day_stats FILTER/sentinel、
  read_day 讀回 2026-05-07 全日 56,362 列且 available_at 轉回 Asia/Taipei 18:00、過 schema 驗證。
- 取代 D-023 的「純串流」與 D-024 原檔案式探測快取；raw HTML 完全不落地。
- 範圍：`data/serving/schema.py`（FUBON_FETCH_LOG_SPEC）、新模組 `data/serving/chip_ingest.py`、
  `data/warehouse.py`（pid tmp）、`cli.py`（backfill 重構 + fubon-chip-status）、`tests/test_cli.py`。

### D-024（2026-07-12，已裁決）backfill-fubon-chip 假日探測跳過 + 進度列
- 需求（使用者）：非交易日不要整天 ~1800 檔白抓；即時顯示目前抓取的股號/日期、
  進度條、整體百分比、起始/預估結束時間。（週六日既有 `weekday<5` 過濾本就跳過；
  問題是**平日國定假日**，如 2026-01-01、2026-05-01，全市場白抓一小時。）
- 裁決：每日先抓前 `--probe` 檔（預設 5，CLI 參數非寫死）：**全數 HTTP 成功但 parse
  皆無 t01 表** → 判定非交易日、跳過整天（D-025 起並以 SENTINEL 持久化）。任何 err
  （被擋/重試耗盡）**不算證據**、照常全抓——避免把「被擋」誤判成假日而無聲丟掉交易日。
  進度列單行 \r 重繪：進度條、整體 %、done/total、目前日期+股號、開始/已用/預估結束
  （平均速率外推）；thread-safe（worker 完成即 tick，不論成敗/跳過）。
- 已知限制：富邦DJ 軟擋（HTTP 200 回空頁）與假日不可分——若探測檔全被軟擋會誤標非交易日；
  訊息會大聲印出，且該日一旦有資料會自動解除標記（D-025 clear_sentinel）；`--probe 0` 停用。

### D-023（2026-07-11，已裁決）backfill-fubon-chip 預設不留 raw HTML（省磁碟）
- 問題：raw HTML 快取（`data/raw/fubon_chip/date=<d>/<sid>.html`）每檔 ~11KB、全市場每日
  ~1432 檔 ≈ 15MB/日（~3.8GB/年、數萬個小檔，stat 都嫌慢）。使用者要求下載時「不用再留
  實體檔案，尤其 HTML 太浪費空間」。
- 釐清資料流（避免誤解）：canonical 分點資料落 **parquet 資料湖** `clean/broker_trades`
  （欄式、極小，是黃金規則 1 因子透過 PitView 讀的**單一真相源**）；**PG 是可重建鏡射**，由
  另一步 `twquant serving sync` 從資料湖增量 upsert（broker_trades 為 6 張鏡射表之一，
  SPEC-009 §3）。故：真正浪費的是 HTML，不是 parquet；且回補**不該**直寫 PG（會繞過資料湖／
  PitView，且 PG rebuild 會失資料）。
- 裁決：`backfill-fubon-chip` 新增 `--keep-raw-html/--no-keep-raw-html`，**預設不留**
  （純串流：抓→解析→丟，不讀不寫 HTML；不建立 date= 目錄）。保留 parquet clean 層不變。
  想要分日內續傳／免重抓重解析者用 `--keep-raw-html` 顯式開啟。
- 取捨：不留 HTML → 失去分日內續傳（run 中斷需重抓整日）與「改 parser 後免重抓重解析」的
  能力；但續傳仍有**分區級**粒度（skip_existing 跳已完成日），單日 ~10 分可接受。此 HTML
  快取本就非 SPEC-001 §7 的版本化 raw archive（是 `<sid>.html` 覆寫式暫存），故弱化其
  留存不牴觸 raw 落地原則。既有已下載的 `data/raw/fubon_chip` 可安全刪除回收空間（canonical
  在資料湖、PG sync 後亦在 PG）。
- 範圍：`cli.py`（`_fetch_fubon_day` 加 `keep_raw` + `--keep-raw-html` 選項）、`tests/test_cli.py`。

### D-022（2026-07-11，已裁決）backfill-fubon-chip 平行下載 + 富邦限流上調
- 問題：`backfill-fubon-chip` 全市場單日 ~1800 檔、序列 1 req/2s → 約 1 小時（D-019）。
  使用者要求改為多執行緒平行下載提速。
- 關鍵事實：真正的吞吐節流是共用的 `SlidingWindowLimiter`，不是執行緒數。只加執行緒
  而不提高限流，各執行緒會卡在 `acquire()` 排隊，總速率不變。且原 limiter 非 thread-safe
  （check-then-append 無鎖），多執行緒會 race。
- 裁決（使用者選「積極 6 workers」，2026-07-11）：
  - `SlidingWindowLimiter.acquire` 與 `HttpClient._get_transport` 加鎖 → 共用 client 下
    thread-safe（全域限流仍準確、單一 requests.Session）。此為全來源共用類別，加鎖對單執行緒
    行為無影響。
  - `backfill-fubon-chip` 新增 `--workers`，逐股迴圈改 `ThreadPoolExecutor`；`.map` 保序
    → 輸出與序列版 bit-identical（黃金規則／§4 冪等）。並行度預設＝限流 `max_requests`
    （超過亦被節流，僅徒增排隊），不寫死。
  - `configs/system.yaml` 富邦限流 **1 req/2s → 6 req/2s**（3 req/s），全市場單日 ~10 分。
- 風險與取捨：富邦DJ 為免費公開站；D-015 已載 TWSE 對「持續大量」即使在 rate 內也會封 IP。
  6 req/2s 是**使用者明示授權的積極設定**、被封風險自負；若被擋，調降 `--workers`／`max_requests`
  或改分批。此條為對 D-019「限流 1 req/2s」敘述的刻意調整（來源特性、canonical schema 不變）。
- 範圍：`data/http.py`、`cli.py`（helper `_fetch_fubon_day` + `--workers`）、`configs/system.yaml`、
  `tests/test_http.py`、`tests/test_cli.py`。

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
