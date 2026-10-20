# SPEC-018 — 新聞向量層與新聞因子

狀態：2026-10-20 規劃（D-201；任務 T-410～T-416）。本文件是給實作模型的規格書：架構、資料、因子定義、時點規則、事前登記、任務與驗收。
使用者原話：「再來規劃新聞用向量資料庫來關聯股票的功能，這些用向量資料庫做應該很適合，要完整規劃，之後可以用來訓練新的模型 基於新聞因子」。
規劃時使用者決定：歷史語料用**免費來源（鉅亨網回補＋MOPS 公告）**；本機資料庫**換成有 pgvector 的 PG 映像**；**情緒第一階段就做**。

**分兩階段（使用者 2026-10-20：「我先不訓練，先規劃向量關聯，之後再訓練」）**：
- **第一階段＝向量關聯（現在做）**：T-410 雲端向量（相似新聞、語意搜尋、同事件合併）、T-411 語意對股票（草稿「可能相關」，核可才算數）。只動雲端新聞服務與網站，不動本機 PG、不裝 Ollama、不抓歷史語料。
- **第二階段＝訓練（使用者說開始才做）**：T-412 歷史語料與本機 pgvector、T-413 情緒、T-414 新聞因子、T-415 模型試驗、T-416 保留期驗證。§3.2、§3.3、§4～§7 先凍結在文件裡；第一階段的設計不得預先遷就第二階段（例如不為了因子去改線上 schema），但也不得做出讓第二階段無法用同一個 bge-m3 的選擇。
- 第一階段的線上向量自 2026-10-19 起全部在 D-109 保留期內：只做資訊功能，不算任何新聞×報酬的數字（§7 照舊）。

依賴與邊界：
- 接在 [SPEC-017](SPEC-017-news-insight.md)（新聞洞悉層）之後：進料、翻譯、別名比對、關係圖、從新聞學關係都已上線；本文件只加「向量」與「因子」兩層。
- **D-109 第二保留期**（2026-09-09 起封存，最早 2027-03-01 開、只能開一次）：線上新聞從 2026-10-19 才有，全部在保留期內。
  所以**能拿來做研究的新聞只有歷史回補的那段**（2021-01～2026-08-31），開發期資料的上界照 SPEC-010 的 `2026-08-31`。
  2026-09-09 以後的新聞照樣存（回補也會抓到），但研究腳本一律過濾掉，測試強制。
- **D-141**：研究收線，只有「新資料」能再開；新聞是新資料，但基礎率不等於模型優勢——任何倍率、任何 IC 都要附同一套對照（沒有新聞因子的同一個模型）。
- **D-166／D-168**：公告與籌碼的事件研究扣成本後沒有優勢；「有關」≠「會漲跌」。本文件的網站功能（相似新聞、搜尋、語意對到的股票）
  仍是資訊功能（D-191 §一，看得更快更清楚），不做暗示訊號的標記。
- 黃金規則 1：因子只能透過 `PitView` 讀資料；新聞表進倉儲後才算因子的輸入。黃金規則 10：`factors/` 不得連網、不得寫檔。
- 全部免費額度（使用者）；本機有 RTX 4070（12 GB），歷史語料在本機算向量，不花雲端額度。

## 0. 給實作者的一頁

1. **兩層、同一個模型**：雲端（新聞服務的 Supabase 專案）裝 pgvector，每則新進新聞在 ingest 時用 Workers AI 的 `@cf/baai/bge-m3` 算 1024 維向量，
   給網站用（相似新聞、語意搜尋、語意對股票）；本機 PG 換成 `pgvector/pgvector:pg16` 映像，歷史語料用同一個 bge-m3（sentence-transformers，GPU）算向量，給研究用。
   向量只做「找相似」與「投影成特徵」兩件事；**相似不是因果**。
2. **本機是研究正本，雲端是每日資料的權威**（記憶：local≠cloud → 從雲端拉到本機，不反向）。每天 pipeline 多一步 `news pull`，把新聞服務的新聞、提及、向量拉進本機。
3. **向量不會「取代」別名比對**。一度（提到）仍以別名比對為準（SPEC-017 §5）；向量只產**草稿**提及（「可能相關」，虛線），
   核可後才升級——同 SPEC-017 的 AI 草擬規則。
4. 情緒：第一階段就做，但**方法分兩段**：（a）本機 Ollama 跑 qwen3 對分層抽樣 2 萬則貼標（−1／0／+1，對「每個被提到的股票」各一個標籤，不是一則一個）；
   （b）在 bge-m3 向量上訓一個線性探針（logistic regression）複製標籤，全語料用探針打分。探針跟標籤的一致度達門檻才用探針；
   不達就乖乖用 Ollama 把全語料貼完（時間換額度）。線上新聞：ingest 時雲端 qwen3 貼標，額度在新聞的 2,500 Neurons 內。
5. 因子（§5）全部是**日頻、逐股、純函式**：輸入是「截至 t 日 21:00 可得的新聞列」，輸出是 t 日的值，t+1 開盤用。歷史用 `published_at`，線上用 `first_seen_at`；
   兩者差多少要量（T-412 驗收），差太多就只信 `first_seen_at` 從上線那天起算。
6. 事前登記（D-201 §三）：門檻在看任何結果之前就寫死；開發期（≤ 2026-08-31）走 walk-forward 只決定「要不要送進保留期驗」；
   最後判定只在 2027-03-01 之後開封時做一次。**事後不改門檻**。
7. 每個任務一個 PR，正式碼 ≤ 400 行（不含測試、不含 migration）；前一個上線看過再做下一個。

## 1. 名詞

- **向量（embedding）**：一段文字經 bge-m3 變成的 1024 維數字；兩段文字越像，向量夾角越小（cosine 越接近 1）。
- **相似新聞**：向量 cosine 最高的其他新聞；同事件（cosine ≥ `cluster_threshold`）併成一群，只「新的併進舊的」。
- **實體輪廓（entity profile）**：一檔股票（或族群、主題）的代表向量＝它被核可提到的新聞向量的平均（再加公司簡介文字的向量）；
  一則新聞沒有別名對到任何股票時，拿它的向量去找最近的輪廓 → 草稿提及。
- **新聞因子（news factor）**：從新聞列算出來的逐股日頻數值，進因子登記簿、進特徵矩陣，跟價量籌碼因子一樣被 LightGBM 吃。
- **情緒（sentiment）**：對「這則新聞對這檔股票」的方向判斷，三類：負（−1）、中性（0）、正（+1）。

## 2. 架構

```
雲端（新聞服務，Supabase 新加坡）                       本機（研究正本，PG + pgvector）
raw_items ─translate─mention─cooccur─┐                 news_hist.items  ←─ 鉅亨網回補 2021-01～（T-412）
                                      ├─ embed（bge-m3，Workers AI）      ←─ MOPS：serving.material_news（既有，52 萬列）
news.embeddings（halfvec 1024, HNSW）─┤                 ←─ news pull（每天，從雲端 API 拉線上新聞）
news.entity_profiles ─────────────────┤                 news_hist.embeddings（bge-m3，本機 GPU）
news.mentions（method＝semantic，草稿）┘                 news_hist.mentions（node 腳本重用 mention.ts）
   │                                                    news_hist.sentiment_labels（Ollama qwen3 抽樣）／sentiment_scores（探針）
   ▼                                                    news_hist.features_daily（§5 因子，日頻逐股）
網站：相似新聞、搜尋、「可能相關」                           │
                                                        ▼  PitView
                                                     factors/news.py → factor_values（類別 news）→ ml dataset → LightGBM 試驗（T-415）
```

- 雲端算向量：ingest 多第五步 `embed`，每次最多 `embed.batch`（50）則；文字＝`title_zh ?? title` ＋ `summary_zh ?? summary`（翻譯沒好先用原文，翻譯好了**不重算**——
  bge-m3 是多語模型，中英向量落在同一空間，T-410 驗收要量「原文向量 vs 譯文向量」的 cosine 中位數 ≥ 0.85，不達就改成等翻譯）。
- **2026-10-20 實測（T-410，[報告](../reports/news-vectors-t410-2026-10-20.md)）**：原文 vs 自己的譯文 cosine 中位數 **0.805 < 0.85** → 照上面的規則改成**英文新聞等翻譯完才算**（ok 用譯文、翻譯沒過用原文）。同事件門檻 0.85、聯準會新聞稿（`fed-press`，同範本換名字）不合併；上線合併 15 則人工看全對。
- **新聞服務一律指定在新加坡執行**（`x-region: ap-southeast-1`，網站轉接與排程都帶）：不指定會被派到東京、每個查詢跨區，實測相似新聞 2.25 → 0.60 秒、搜尋 1.90 → 0.62 秒、關係圖 2.58 → 0.68 秒。
- 雲端查詢：`/v1/similar?id=`（同事件群＋最近 10 則）、`/v1/search?q=`（問句算向量 → HNSW 最近 20 則，當天額度內）、`/v1/graph` 的草稿提及走同一條線。
- 本機算向量：`twquant news embed`（CLI，資料層），sentence-transformers `BAAI/bge-m3`，fp16，批次 128，只算還沒有向量的列；可重跑、冪等。
- 兩邊同一個模型但不同執行環境（Workers AI vs 本機 torch），向量會有微小差異。T-412 驗收：抽 200 則兩邊都算，cosine 中位數 ≥ 0.99；
  不達 → 本機全部自己重算（本機是研究正本），雲端的向量只給網站用。

## 3. 資料

### 3.1 雲端（schema `news`，只增不改，同 SPEC-017 §2）

| 表 | 欄位 | 說明 |
|---|---|---|
| `embeddings` | `item_id` PK、`model`（`bge-m3`）、`model_version`（Workers AI 模型字串）、`text_sha`、`vec halfvec(1024)`、`created_at` | 一則一列；文字改版（`raw_item_versions`）不重算 |
| `entity_profiles` | `entity_id`、`version`、`vec halfvec(1024)`、`built_from_items int`、`built_at`、`valid_to` | 每週重建一版（pg_cron），舊版補 `valid_to`；只用 `status=approved` 的提及 |
| `mentions`（既有）多一種 `method='semantic'` | `score`（cosine）、`status`（draft／approved／rejected）、`profile_version` | 草稿提及；核可頁（T-403）一併處理 |
| `clusters` | `cluster_id`、`item_id`、`seed_item_id`、`score`、`created_at` | 同事件群；只新併舊，不回頭重分 |
| `llm_usage`（既有） | 多 `kind='embed'` | 向量的 Neurons 帳，含在新聞 2,500／天內 |

索引：`embeddings` 建 HNSW（`vector_cosine_ops`，m 16、ef_construction 64；14 MB 的庫幾秒建完）。
**容量（2026-10-20 實測改正）**：一則向量約 2.9 KB（資料）＋2.7 KB（HNSW 索引）＝5.6 KB，原本估的「一則 2 KB 含索引」低估將近三倍；
加上原文、譯文、提及、事件群，一則約 9 KB。來源加到 20 個後一天約 700 則 → 不設上限約兩個半月就滿 500 MB。
所以雲端只留近 `embed.retention_days`（45）天的向量：ingest 第五步先刪過期的、也不再補算（原文、提及、事件群都留）；
健康檢查回報資料庫用量，超過七成網站狀態列變黃。原文與提及的長期存放（雲端只留一段、舊的歸檔到別處）還沒決定。

### 3.2 本機（schema `news_hist`；本機 PG 映像換成 pgvector）

| 表 | 欄位 | 說明 |
|---|---|---|
| `items` | `id`（`cnyes:<newsId>` ／ `mops:<stock_id>:<available_at>:<sha>` ／ 線上的 `news:<uuid>`）、`source`、`published_at`、`available_at`、`title`、`summary`、`content`（MOPS 用主旨＋前 `mops_content_chars` 字）、`url`、`stock_tags`（鉅亨網自帶的 `stock` 欄）、`fetched_at` | 歷史正本。`available_at`：鉅亨＝`publishAt`；MOPS＝公告時間（既有欄）；線上＝`first_seen_at` |
| `embeddings` | 同雲端，多 `runtime`（`local-torch` ／ `workers-ai`） | |
| `mentions` | `item_id`、`entity_id`、`method`（alias／semantic／tag）、`rule_version`、`score` | `tag`＝鉅亨網自帶的股票標籤（免費送的一度，但要跟別名比對交叉驗證） |
| `sentiment_labels` | `item_id`、`entity_id`、`label`（−1／0／+1）、`model`、`prompt_version`、`created_at` | Ollama 抽樣標籤；線上新聞的雲端標籤也拉進這張 |
| `sentiment_scores` | `item_id`、`entity_id`、`p_neg`、`p_neu`、`p_pos`、`probe_version` | 探針輸出，全語料 |
| `topics` | `topic_id`、`version`、`centroid halfvec(1024)`、`label`、`fit_start`、`fit_end` | §5 主題投影的中心，只在訓練期上擬合、凍結 |
| `features_daily` | `date`、`stock_id`、§5 各欄 | 日頻逐股；由 `twquant news features --from --to` 產生，再進 `factor_values`（類別 `news`） |

- `news_hist.items` 不複製 `serving.material_news` 的全文；只存 id、時間、主旨與截短的內容（`mops_content_chars` 800），全文留原表。
- 所有 timestamp tz-aware Asia/Taipei；股票代號字串；缺值 null 不補 0（黃金規則 2～5）。

### 3.3 本機 PG 映像切換（使用者決定：換成有 pgvector 的映像）

- `docker-compose.yml` 的 `pg` 改 `image: pgvector/pgvector:pg16`（同為 PG 16，資料目錄格式相容，named volume 直接沿用）。
- **先 `pg_dump` 再換**（記憶 2026-09-10：Docker 重設曾把整顆 PG 磁碟刪掉；大型 PG 工作前後都要 dump；Git Bash 下 `pg_dump` 路徑會被 MSYS 轉壞，照 WHERE-IS-EVERYTHING ⑬）。
- 官方 pgvector 映像是 Debian（glibc），現在的 `postgres:16-alpine` 是 musl：**文字欄位的排序規則版本會不同**。切換後必做
  `ALTER DATABASE twquant REFRESH COLLATION VERSION` 並 `REINDEX DATABASE twquant`（否則 PG 會一直警告、B-tree 索引可能壞）。REINDEX 時間要先在 dump 還原的副本上量，寫進 T-412 完成註記。
  - **實做紀錄（2026-10-20，T-412）**：`REFRESH COLLATION VERSION` 回「invalid collation version change」——alpine 映像沒記過版本，略過無害。
    `REINDEX DATABASE` 跑到一半後端 segfault（平行維護工人），PG 自動進復原、沒掉資料；改成只重建 114 個索引欄含文字的索引、
    每條前 `SET max_parallel_maintenance_workers = 0`（`scripts/pg_reindex_collatable.sh`），約 2 小時，資料庫 70 → 57 GB。
    切換前的 dump：`data/backup/twquant_serving_20261020-2333_pre-pgvector.dump`（11.2 GB）。
- `CREATE EXTENSION vector` 一次；`twquant serving migrate` 檢查擴充存在，沒有就 raise（不靜默退回無向量模式）。
- 切換當天不跑任何回測、訓練或 pipeline（記憶：不要同時跑重工作）。

## 4. 歷史語料（T-412）

| 來源 | 範圍 | 量 | 方式 |
|---|---|---|---|
| 鉅亨網（非官方 API `api.cnyes.com/media/api/v1/newslist/category/<cat>?startAt&endAt&limit&page`） | 2021-01-04 起（更早沒有） | 頭條約 150～250 則／天、台股約 110 則／天，去重後估 250／天 → 約 **53 萬則** | 類別 `headline`、`tw_stock`；按天分頁，每頁 30，請求間隔 ≥ `cnyes.backfill_interval_seconds`（1 秒），遇 429 退避；全程約 18,000 個請求、6～8 小時，可中斷續跑（按天記進度表 `news_hist.backfill_runs`） |
| MOPS 重大訊息 | 2019-12-31～2026-09-28（本機 `serving.material_news` 52 萬列）；之後接 `serving.mops_announcements`（主旨） | 52 萬列 | 本機 SQL 搬運，不連網 |
| 線上新聞服務 | 2026-10-19 起 | 約 250／天 | `twquant news pull --since`，走 `/v1/export?since=`（新端點，只給擁有者金鑰） |

- 鉅亨網自帶 `stock` 欄（它自己標的相關股票）——當 `method='tag'` 的提及存起來，T-412 驗收要量「tag vs 別名比對」的一致率並抽 100 則人看，
  才決定因子用哪一種當一度（預設：兩者聯集、各記方法）。
- 使用條款：非官方 API 只做研究，只存本機，不對外重發；線上網站只用自己的進料（SPEC-017 的來源清單）。
- 向量：53 萬＋52 萬＝約 105 萬段文字，bge-m3 fp16 在 4070 上估 300 段／秒 → 約 1 小時；向量＋HNSW 約 4 GB，本機 PG 現在 70 GB，可接受。
- 依賴：`torch`、`sentence-transformers` 放 uv 的**選用群組 `embed`**，不進主依賴（CI 不裝、閘門不變慢）；`factors/`、`ml/` 不得 import 它們（IO 邊界測試加這兩個名字）。

## 5. 新聞因子（T-414；類別 `news`，登記簿 `_CATEGORIES` 加 `"news"`）

全部日頻逐股、純函式、參數在 `configs/news_factors.yaml`。**可得時點**：t 日的值只用 `available_at ≤ t 日 `cutoff_hour`（21:00）` 的新聞（本機 pipeline 21:13 跑，之後的新聞算隔天）。

| 因子 | 定義 | 直覺（給使用者的白話） |
|---|---|---|
| `news.count_1d` | log1p(t 日提到它的新聞則數) | 今天多少人在講它 |
| `news.attention_5d` | 近 5 日則數 ÷ 自己近 60 日的日均則數（都 +1 平滑），取 log | 比它平常熱多少 |
| `news.attention_20d` | 同上，20 日 | |
| `news.novelty` | 1 − max cosine（近 5 日提到它的新聞 vs 它過去 60 日的新聞）；沒舊新聞＝null | 是新消息，還是舊聞重炒 |
| `news.sent_1d`／`sent_5d` | 近 1／5 日對它的情緒平均（p_pos − p_neg），沒新聞＝null | 講它的是好話還是壞話 |
| `news.sent_shift` | sent_5d − 前 20 日平均情緒 | 風向有沒有變 |
| `news.sector_heat` | 它所屬族群（`stock_group`）近 5 日被提到的則數 z 分數（對該族群近 120 日） | 整個族群在不在話題上 |
| `news.topic_<k>` | 近 5 日提到它的新聞向量平均，對第 k 個主題中心的 cosine（k＝1～`topics.k`，16） | 它最近的新聞偏哪一類（法說、擴產、訂單…） |
| `news.mops_count_5d` | 近 5 日它的重大訊息則數 | 公告多不多 |
| `news.mops_novelty` | 1 − max cosine（最新一則公告 vs 它過去 180 日的公告） | 公告是例行（每月營收）還是少見 |

- 主題中心（`topics`）只在訓練期（第一折的訓練視窗）上用 k-means 擬合並凍結、記版本；之後所有折與線上都用同一組，避免未來資料滲進中心。
- 每個因子的測試檔必須呼叫 `assert_no_lookahead`，並加一條「把截點往前移，值只能變少或不變」的性質測試（則數類）。
- 缺值規則：沒有新聞＝null（LightGBM 原生處理），**不補 0**；但 `count_1d` 沒新聞＝0 是真值不是缺值（唯一例外，在 schema 註明）。
- 標準化：同既有因子，逐日橫斷面 z（`standardize`）。
- **實做紀錄（2026-10-21，T-414）**：特徵由資料層 `data/news_features.py` 先算進主庫 `news_features`（含 `available_at`），
  `factors/news.py` 只讀表（跟 ml_scores 同套路）。跟上表的差異：
  （a）`cutoff_hour` 是 **19:00** 不是 21:00——本機排程 19:00 跑，截點要 ≤ 排程時間；只算截點已過的日子，不蓋未來時間；
  PitView 的訊號截點 20:00，19:00 的列當天看得到；
  （b）novelty 的「過去 60 日」以每則新聞自己的日期往回算（先算每則對舊聞的最大 cosine、存 `news_hist.item_stock_sim`，再對近 5 日取最大）；
  （c）族群成員用現在的 `stock_group` 快照（主庫沒有歷史，D-177）；
  （d）attention 的分母在語料開頭用「已有天數」不是 60；
  （e）MOPS 只算公告公司本身（tag），內文提到的對手方不算它的公告；
  （f）mops_novelty 只看最近 20 天內的最新公告，更久沒公告＝null。
  主題中心：≤ 2021-12-31 的 36,478 則、k-means 16、seed 7、版本 2026-10-21.1，寫在每列的 `topic_version`。

## 6. 情緒（T-413）

- 標籤定義：對「這則新聞對這檔股票的短期（一個月內）影響」，負／中性／正；提示詞附 6 個例句（含「營收創高但低於預期＝負」這種反例）；輸出只准三個字之一。
- 抽樣：按年份 × 來源（鉅亨／MOPS）× 有無情緒詞 分層抽 20,000 則-股票對；本機 Ollama `qwen3:8b`（4070 可跑，估 1～2 秒／則，6～10 小時）。
- 探針：在 bge-m3 向量上 logistic regression（三類），訓練 16,000、驗證 4,000。**門檻（事前）**：驗證集 macro-F1 ≥ 0.70 且「正 vs 負」二分 AUC ≥ 0.85 才用探針打全語料；
  不達 → 全語料用 Ollama 貼完（時間成本約 2～3 天 GPU），探針棄用。
- 人工抽查：200 則由實作者判讀，跟 LLM 標籤的一致率寫進報告（不是門檻，是誠實紀錄；MOPS 的中性比例預期很高）。
- 線上：ingest 第六步 `sentiment`，雲端 qwen3 對每則的每個核可提及貼標；每天額度估 250 則 × 3 Neurons ≈ 750，連同翻譯與向量都在 2,500 內；超過就停貼、隔天補（不擋進料）。

## 7. 事前登記（D-201 §三；完整條文在 DECISIONS.md）

- **開發期**（2023-01～2026-08-31，走 SPEC-010 的 walk-forward、embargo、宇宙、標籤與成本模型，一字不改）：
  - H1 單因子：至少一個 §5 因子的 RankIC（h=20）t ≥ 3，且方向跟事前寫的一致（attention 正、novelty 正、sent 正、mops_novelty 正；寫錯方向算沒過）。
  - H2 模型：同一個 LightGBM、同折、同 seed，加新聞因子 vs 不加：**逐折 OOS RankIC 平均差 ≥ +0.005** 且 **前 20 檔扣成本超額 ≥ 0**（對不加的那個）。
  - 兩個都過 → 凍結因子版本、主題中心、探針版本、模型設定，commit 後登記進 D-109 開封清單；任一沒過 → 收線，寫報告，不調門檻再跑。
- **保留期**（2027-03-01 後開封、只看一次）：H3 新聞模型 vs 當時的正式排序模型，保留期 RankIC 差 ≥ +0.005 且前 20 檔扣成本超額 ≥ 正式模型。過才能上線取代。
- 禁止：在開封前對 2026-09-09 以後的任何新聞×報酬算數字；研究 CLI 一律 `--to 2026-08-31` 上限，測試強制。

## 8. 任務與驗收

第一階段：T-410、T-411。第二階段（使用者說開始才做）：T-412～T-416。

| 任務 | 內容 | 驗收（AC） |
|---|---|---|
| T-410 雲端向量 | 新聞庫裝 pgvector；ingest 第五步 embed；`embeddings`／`clusters`；`/v1/similar`、`/v1/search`；網站每則「相似新聞」、新聞頁搜尋框 | AC1 新進新聞 30 分鐘內有向量；AC2 原文 vs 譯文 cosine 中位數 ≥ 0.85；AC3 `/v1/search` 回應 < 1.5 秒（冷啟動除外）；AC4 Neurons 帳 `kind=embed`；AC5 npm test 純函式（分群只新併舊） |
| T-411 語意對股票 | `entity_profiles` 週建；沒別名命中的新聞找最近輪廓 → 草稿提及（cosine ≥ `semantic.min_score`）；關係圖虛線「可能相關」；核可頁一併處理 | AC1 抽 100 則草稿人看，精確率寫進報告（不是門檻）；AC2 草稿不進 `/v1/stock-news` 的計數、不進晨報事實句；AC3 核可後 `method` 不變、`status` 改 approved 並記時間 |
| T-412 歷史語料 | 本機 PG 換 pgvector 映像（dump → 換 → REFRESH COLLATION → REINDEX）；鉅亨網回補；MOPS 搬運；`news pull`；本機 embed；mention.ts 回補；一致性量測 | AC1 切換前後 `pg_dump` 檔案存在且列數一致；AC2 回補按天可續跑、重跑不重複；AC3 兩邊向量 cosine 中位數 ≥ 0.99；AC4 鉅亨 tag vs 別名一致率報告；AC5 `published_at` vs `first_seen_at` 差距分布（線上重疊的那段） |
| T-413 情緒 | 標籤定義與提示詞；Ollama 抽樣貼標；探針；全語料打分；線上貼標 | AC1 探針門檻（F1 ≥ 0.70、AUC ≥ 0.85）過或明確改走全貼；AC2 200 則人工一致率報告；AC3 線上每天額度內 |
| T-414 新聞因子 | `factors/news.py`、`configs/news_factors.yaml`、登記簿 `news` 類別、`features_daily` 物化、PitView 表來源 | AC1 每個因子 `assert_no_lookahead`；AC2 截點前移性質測試；AC3 缺值 null、`count_1d` 例外有註明；AC4 IO 邊界測試含 torch／sentence_transformers |
| T-415 模型試驗 | 事前登記的 H1／H2，報告 `docs/reports/news-factors-dev-<日期>.md` | AC1 對照模型同折同 seed；AC2 報告含每折數字與門檻判定；AC3 過 → 版本凍結與 D-109 登記；不過 → 收線 |
| T-416 保留期驗證 | 2027-03-01 後，與 D-109 其他假說同一次開封 | AC1 只跑一次、結果進 DECISIONS |

## 9. 成本與容量（估）

| 項目 | 估 | 來源 |
|---|---|---|
| 雲端向量 | 250 則／天 × 約 150 token ≈ 40 Neurons／天 | bge-m3 約 1,100 Neurons／百萬 token |
| 雲端情緒 | 約 750 Neurons／天 | 翻譯實測 113／35 則推 |
| 雲端資料庫 | 一則約 9 KB（向量＋索引 5.6 KB）；一天約 700 則；向量只留 45 天約 180 MB，原文提及一天約 2 MB；免費 500 MB | 2026-10-20 實測；原文長期存放待決定 |
| 本機 GPU | 歷史向量約 1 小時；Ollama 貼標 6～10 小時（抽樣）或 2～3 天（全貼） | 4070 12 GB |
| 本機磁碟 | 向量＋索引約 4 GB；items 約 1 GB | |
| 鉅亨網請求 | 約 18,000 次、6～8 小時 | 1 秒一次 |

## 10. 使用者要動手的事

第一階段只需要第 2、4 項；第 1、3 項到第二階段才需要。

1. 本機 PG 換映像那天：關掉其他重工作、確認 dump 完成（T-412 開始前會先回報）。
2. 雲端：Supabase 新聞專案 `create extension vector`（SQL 一行，可由我透過 Management API 執行）；Edge Function 重新部署。
3. 本機裝 Ollama 並拉 `qwen3:8b`（或授權我裝）。
4. 核可頁上處理語意草稿提及（跟供應商草稿同一頁）。
