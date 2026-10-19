# SPEC-017 — 新聞洞悉層（資訊關係圖）

狀態：2026-10-19 規劃核可（D-200；任務 T-399～T-407）。本文件是給實作模型的規格書：資料、定義、時點規則、任務與驗收寫死。
使用者原話：「新聞頭條整理成一個新分頁，國外的財經網站也要爬…翻譯成台灣繁體中文，再標示翻譯過，半夜也要固定爬，再結合成台灣相關類股的關聯」
「這算是更完善洞悉層」「洞悉層資料接入要有辦法來做各種相關資訊的關聯」「LinkedIn 有一種關聯幾度的演算法」「國外的新聞也要可以直接關聯美股」
「很像電影關鍵報告…上帝視角來關聯出資訊」「我想要的是一個完整的新聞洞悉層維度」。

依賴與邊界：
- **D-191 §一**：資訊功能以「看得更快更清楚」評，不用回測擋；本線第一層（T-400～T-406）全部是資訊功能。
- **D-109 第二保留期**（2026-09-09 起封存，最早 2027-03-01 開、只能開一次）：新聞從 2026-10-19 才開始累積，全部在保留期內。
  **開封前不准拿新聞×報酬算任何數字**（含試算、含「看起來有效」）。「關聯新聞能否預測」只能在 T-407 寫成事前登記假說、凍結版本，於開封時一起驗。
- D-166／D-168：公告與籌碼的事件研究扣成本後都沒有優勢——「有關」≠「會漲跌」，網站不得做暗示訊號的標記。
- `configs/stock_groups.yaml` 寫「不進因子、不進模型」：族群關聯要當因子，另立裁決（併入 T-407）。
- 全部免費額度（使用者 2026-10-19）。

## 0. 給實作者的一頁

1. **這是一個獨立的微服務**（使用者 2026-10-19：「這應該可以算一個獨立的微服務了」），程式全部在 repo 頂層 `news-service/`
   （同 `cron-trigger/`、`market-live/` 自成一塊），有自己的資料庫、運算、部署與對外介面；其他程式**只能透過它的 API 讀**，不碰它的表（§10）。
2. 新聞領域的邏輯**只有一份，寫在 TypeScript**：`news-service/src/*.ts`（純函式：解析、檢查、提及偵測、傳播、重算）。
   不 import 第三方套件、不讀 `process.env`／`Deno.env`、不做 IO（IO 由呼叫端注入，同 `market-live.ts` 的 `runOnce`）。`node --test` 測。
   執行端：新 Supabase 專案的 Edge Function（Deno）——`ingest`（pg_cron＋pg_net 每 30 分鐘）、`api`（唯讀查詢）、`health`。
   網站（Pages Functions）與 Python（晨報、名單分析、證據包）都呼叫 `api`，**不重做任何關聯邏輯**、也不直連新聞資料庫。
3. 資料表**只增不改**（§2）。任何「更新」都是新增一列＋舊列補 `valid_to`。
4. 參數（來源、別名、權重、主題、上限）一律放 `configs/news*.yaml`；Edge 讀不到 repo → 部署時產生 JSON 鏡像，測試核對兩者一致。
   規則版本＝設定檔內容的 sha256（不手填）。
5. 時點：研究與 AI 引用一律用「可得時間」＝ max(`first_seen_at`, 計算時間, 用到的線的 `valid_from`)；不得用 `published_at`。
6. 錯誤一律 raise 並記進 `ingest_runs`，不吞（黃金規則 5）；單一來源壞掉不擋其他來源。
7. 每個任務一個 PR，正式碼 ≤ 400 行（不含測試）；前一個上線看過再做下一個。

## 1. 名詞

- **資訊（item）**：一則新聞或公告。
- **實體（entity）**：台股、美股、族群、主題、國外公司（非上市或非美股）。
- **線（edge）**：實體之間的關係，有種類、權重、有效期間、狀態（草稿／核可）。
- **度數**（同 LinkedIn）：資訊直接提到的實體＝**一度**；從一度再走一條線＝二度；再一條＝三度（上限）。
- **關聯（link）**：資訊×實體，含度數、分數、最佳路徑。

## 2. 資料（新 Supabase 專案，schema `news`）

| 表 | 主要欄位 | 規則 |
|---|---|---|
| `raw_items` | id（source＋guid／網址的 sha256 前 16 碼）、source、guid、url、title_orig、summary_orig、lang、published_at、**first_seen_at**、content_sha | 只增；第一次抓到才寫 |
| `raw_item_versions` | item_id、title_orig、summary_orig、content_sha、seen_at | 來源改了標題或摘要 → 新增一版，不覆寫 |
| `translations` | item_id、content_sha、title_zh、summary_zh、status（ok／failed）、checks（jsonb）、model_id、prompt_version、translated_at | 只增；同一版內容只翻一次 |
| `entities` | id、type（tw_stock／us_stock／group／theme／company）、key、name_zh、name_en、valid_from、valid_to | 只增 |
| `entity_aliases` | entity_id、alias、lang、origin（rule／ai_draft）、status（draft／approved）、reviewer、reviewed_at、valid_from、valid_to | AI 草擬的要核可才生效；`valid_from`＝核可時間 |
| `graph_edges` | src、dst、type、weight、evidence、origin、status、reviewer、reviewed_at、valid_from、valid_to | 同上；主庫同步的線 `valid_from`＝同步時間 |
| `mentions` | item_id、entity_id、method（alias／llm）、evidence_span、rule_version、created_at | **一度、永久**；`evidence_span` 必須逐字出現在原文 |
| `links_current` | item_id、entity_id、degree、score、path（jsonb）、rule_version、computed_at | 二、三度；只存最新規則版每則前 `max_links` 名；保留 `links_keep_days` 天 |
| `clusters` | item_id、cluster_id、joined_at | 同事件；**只能新的併進舊的**，不回頭重分 |
| `us_quotes_daily` | ticker、date、close、chg | 清單內美股每日收盤 |
| `ingest_runs` | run_id、source、started_at、ok、new_items、error | 每來源每次一列 |
| `llm_usage` | ts、task、model_id、input_tokens、output_tokens、neurons | 新聞這邊的 Workers AI 用量帳 |

容量（一天約 200 則）：文字＋一度關聯一年約 150MB；向量（T-406，`halfvec(1024)`＋索引）一年約 200MB，超過一年的向量刪除；
二、三度保留 90 天。MOPS 公告不複製，用主庫股號連。

## 3. 進料（T-400）

- 來源清單 `configs/news.yaml`：每來源 name、url、kind、lang、條款網址、檢查日期、抓取間隔、每次上限。
  第一批：Bloomberg Markets／Technology、CNBC Top／Technology、FT Markets、Nikkei Asia（無發布時間）、DigiTimes Asia、Fed 新聞稿、鉅亨網頭條。
- 條件式請求（ETag／If-Modified-Since）；429 或 5xx → 這個來源本次停、下次再試；連續失敗記 `ingest_runs`。
- 去重：同 id 不重寫；內容變了 → `raw_item_versions`。
- 排程：pg_cron 每 30 分鐘呼叫 Edge Function；函式先回 202、工作放 `EdgeRuntime.waitUntil`；每次每來源最多 `per_run_limit` 則。
- 防暫停：既有 cron-trigger Worker 每天呼叫一次新專案的健康檢查端點（不佔新 cron）。
- 監控：網站顯示最後抓取時間；每日 pipeline 與晨報檢查「超過 `stale_hours` 小時沒抓」→ 用既有推播通知擁有者。

## 4. 翻譯（T-401）

- Workers AI qwen3（`configs/llm.yaml` 加 `news_translate` 任務），一次 `translate_batch` 則，術語表在 `configs/news.yaml`。
- **擋**（不過→重試一次→仍不過：`status=failed`、網站顯示原文標「未譯」）：
  1. 數字：原文的每個數字（含 million／billion／trillion、%、$）換算後都要在譯文找到（億、兆、萬換算後相等）；
  2. 出現簡體專用字（字表在設定檔）。
- **只記錄不擋**（`checks` 裡留結果，兩週後看誤擋率再決定）：漲跌方向詞相反、中文數字（排除「兩倍」等正常用法）、多出原文沒有的公司名。
- 網站：譯文標「譯」，點開看原文與來源連結。
- 用量：新聞每天上限 `daily_neurons`（初值 2,500，估計用約 900）；Python 各任務合計 6,500（`configs/llm.yaml` 調整）。

## 5. 實體與一度（T-402）

- 台股：主庫 `stock_meta`；英文名來自證交所／櫃買公司基本資料的英文簡稱＋人工清單。美股：約 300 檔清單（`configs/news_entities.yaml`）。
  族群：主庫 `stock_group`。主題：約 40 個（`configs/news_entities.yaml`）。
- 主庫同步（Edge Function 唯讀連主庫，每天一次）：**比對差異**——不見的線補 `valid_to`、新的線 `valid_from`＝同步時間。
  （主庫 `replace_groups` 每 7 天整份重寫、沒有歷史，所以不能照抄。）
- 提及偵測：別名比對（英文字界、ticker、括號代號、中文全詞）；**停用詞與反例**（ON、ARM、META、Delta＝達美航空 vs 台達電、短中文名嵌在長名裡）
  在設定檔、每條都要有測試；沒對到時才讓 AI 在候選清單內挑，並給原文片段（逐字比對，不在原文就丟）。
- 總經新聞（只對到主題、沒對到公司）不往成員擴散。

## 6. 二、三度（T-404）

- 每種線一個係數（`configs/news_graph.yaml`）；分數＝路徑上係數相乘 ÷ 每個中途點出度的 `fanout_power` 次方；每則固定總分預算 `budget`。
- 同一實體取最高分、留最佳路徑；同分依代號排序（結果穩定）。v1 不傳正負；`peer_of` 最多一跳；每則最多 `max_links` 個實體。
- **顯示範圍**：個股頁、名單小標、晨報、名單分析只用**二度以內**；三度只出現在單則關係圖。
- 重算：`news-rebuild --as-of <時刻>`（node 腳本）只用當時已核可、有效的實體、別名、線與當時的規則版本；
  AI 的判定（`mentions.method=llm`）當不可變事實沿用，不重叫模型。

## 7. 網站

- 新分頁「新聞」（第 5 個分頁）；首頁「新聞頭條」卡＝今天重點＋「看全部 →」。地鐵告示風格（固定顏色分類、圖示、少字），390px 先驗。
- 新聞流（T-400 起）：色塊（國際／台灣／美股）、「譯」、關聯標籤（美股附昨晚漲跌、台股標度數）、篩選（族群、個股、國際／台灣、關鍵字）。
- 單則關係圖（T-404）：同心圓（中心新聞、一圈一度），線粗＝分數；點任一點→個股頁。
- 今日影響地圖（T-405）：族群方塊，顏色＝消息熱度（對自己 20 日平均）；股價另欄顯示；**不做「消息熱、股價沒動」這類暗示訊號的標記**。
- 美股→台股傳導圖（T-405）：左美股（昨晚漲跌）、右族群，帶子粗細＝連起兩邊的分數加總。
- 個股頁「相關消息」（T-402，附路徑）；每日名單小標（T-402，`badges.ts`＋`BADGE_LEGEND`，標資料時間）；核可頁（T-403，只有擁有者帳號）。
- 網站的 `/api/news/*`（Pages Functions，在 Cloudflare Access 後面）只是把請求轉給新聞服務的 `api`（服務金鑰放 Pages 祕密，不給瀏覽器），
  回應 60 秒暫存；主庫的資料（名單、族群漲跌）照舊由主庫端點給，需要兩邊的由瀏覽器分別抓再組。

## 8. 洞悉層消費者（Python，T-402／T-404）

- 晨報：事實句多「昨晚國際（已譯）＋關聯到的台灣族群」，照現有 `unverified_numbers`／`reversed_directions` 核對。
- 名單分析（pick_review）、一句話（pick_blurbs）：消息面多「近 3 天二度以內的國際新聞」事實句。
- 證據包：新增 `linked_news` 區，`available_at`＝§0 第 5 點的可得時間，交給既有 `_usable` 檢查；新聞庫連不上記缺口、不擋。
- Python 用 HTTP 呼叫新聞服務的 `api`（`TWQUANT_NEWS_API`＋`TWQUANT_NEWS_KEY`），讀取放在 `data/sources/news_service.py`、由 reporting／CLI 層注入；
  `insight/` 不得連網或 import 資料庫套件（`tests/test_io_boundary.py`）。服務沒回應 → 記缺口、不擋。
- 順手修：Python 的 Workers AI 用量帳記在執行機器的檔案（`reporting/llm_log.py`），雲端每次從零算 → 改記主庫小表，否則 6,500 的上限守不住。

## 9. 任務與驗收

| 任務 | 內容 | 驗收（每條要有 `test_acN_*` 或報告） |
|---|---|---|
| T-399 | 規格、裁決、0 號試驗 | AC1 SPEC-017＋D-200 合併；AC2 試驗報告：Supabase 能否打包 `supabase/functions/` 以外的共用 TS、各來源從雲端 IP 抓不抓得到 |
| T-400 | 原文進料、去重、版本表、監控、排程、防暫停、新聞流（原文） | AC1 RSS／鉅亨解析（錄好的回應）；AC2 同 id 不重寫、內容變了加一版；AC3 一個來源壞不擋其他、錯誤進 `ingest_runs`；AC4 時點：`first_seen_at` 由我們的時鐘決定；AC5 網站列表 390px 截圖 |
| T-401 | 翻譯、檢查、「譯」、用量帳 | AC1 數字換算檢查（含 billion→億、trillion→兆、%）；AC2 簡體字擋；AC3 記錄型檢查只記不擋；AC4 用量上限到了就停；AC5 人工抽 30 則譯文報告 |
| T-402 | 實體、別名、主庫同步、一度、四處接上 | AC1 別名反例全部不誤判；AC2 同步比對差異（補 `valid_to`／新 `valid_from`）；AC3 `evidence_span` 不在原文就丟；AC4 個股頁、名單小標、晨報事實句；AC5 抽 30 條關聯報告 |
| T-403 | AI 草擬供應商與別名、核可頁 | AC1 草擬一律 draft；AC2 只有擁有者能核可；AC3 `valid_from`＝核可時間 |
| T-404 | 二、三度、關係圖、名單分析、證據包 | AC1 出度打折與總分預算；AC2 總經新聞不擴散；AC3 結果穩定（同輸入同輸出）；AC4 時點：截點往前移，關聯只能變少不能變多；AC5 證據包 `linked_news` 過 `_usable` |
| T-405 | 影響地圖、傳導圖、美股收盤 | AC1 熱度對自己 20 日平均；AC2 沒有暗示訊號的標記；AC3 截圖 |
| T-406 | 向量：同事件合併、AI 候選、語意搜尋 | AC1 合併只新併舊；AC2 向量只當候選、要再確認 |
| T-407 | 研究準備：`news-rebuild --as-of`、本機封存、事前登記假說 | AC1 重算與線上結果一致（同版本）；AC2 假說、門檻、版本在 2027-03-01 前 commit；AC3 開封前沒有任何新聞×報酬的數字 |

## 10. 服務邊界與對外介面（API v1）

| 端點 | 用途 | 主要參數 |
|---|---|---|
| `GET /v1/items` | 新聞流 | since、until、kind、group、stock、q、limit |
| `GET /v1/items/:id` | 單則（原文、譯文、檢查結果、一度提及） | — |
| `GET /v1/items/:id/graph` | 單則關係圖（到三度，最多 `max_links` 點） | — |
| `GET /v1/links` | 依實體查相關資訊（個股頁、名單小標、晨報、名單分析） | entity（tw:2330／us:NVDA／grp:半導體）、max_degree（預設 2）、since、as_of |
| `GET /v1/heat` | 今日影響地圖（族群熱度） | date |
| `GET /v1/flows` | 美股→台股傳導 | date |
| `GET /v1/review` ／ `POST /v1/review/:id` | 核可頁（草擬的別名、線） | 只收擁有者（由網站 Access 身分轉送） |
| `GET /v1/health` | 最後抓取時間、各來源狀態、今日用量 | — |

- `as_of` 參數：只回「可得時間 ≤ as_of」的資料（證據包與研究用；§0 第 5 點）。
- 回應帶 `api_version`、`rule_version`；欄位只增不刪，要刪欄或改意義就開 `/v2`。
- 服務掛掉時：網站新聞分頁顯示「新聞服務暫停」、其他分頁照常；Python 消費者記缺口。
- 研究用全量另提供唯讀資料庫角色（本機 `scripts/pull_cloud_to_local.py` 拉回），不給線上程式用。

## 11. 使用者要動手的事

1. 在現有 Supabase 帳號開第二個免費專案（不夠再談另開帳號——另開帳號規避額度可能違反條款，先確認）；連線字串放 `.env` 與 GitHub secrets。
2. 部署：Supabase Edge Function、Cloudflare（cron-trigger 防暫停、Pages 第二個 Hyperdrive）——AI 的自動模式擋正式部署，需要使用者執行或授權。
3. 核可 AI 草擬的供應商、別名、主題→族群。
