# HANDOFF 2026-10-19 — 每則新聞都有關係圖（T-402＋T-404 接真資料）

使用者 2026-10-19 看了新聞分頁：「怎麼只有上面三則新聞？」——示範的三則是手工做的。這一輪把「新聞提到哪些公司」
自動比對出來、族群與供應商關係進新聞資料庫、每則新聞點一下就有心智圖。規格 [SPEC-017](specs/SPEC-017-news-insight.md) §5～§7、裁決 D-200。

分支 `claude/news-graph-real`（worktree `C:/t/wt-intraday`）。五個工作者同時做、**各自只碰自己名下的檔案**；介面照本文件，不自己發明。

## 0. 全貌

```
configs/news_entities.yaml（美股、國外公司、主題＋別名）─┐
configs/news_edges.yaml（草擬：供應商、主題→族群）────────┤
主庫 serving.stock_meta（台股名）＋ stock_group（族群）────┼→ scripts/news_sync_entities.py ──→ 新聞庫 news.entities／news.graph_edges
                                                            │
新聞庫 raw_items＋translations ──→ ingest 的第三步：比對別名（src/mention.ts）──→ news.mentions（一度，只增）
                                                            │
GET /v1/graph?id=… ──→ src/graphapi.ts：mentions＋edges → propagate（src/graph.ts，已有）→ 心智圖的 nodes
GET /v1/items ──→ 每則多 mentions:[{id,type,label}]（一度）
網站：新聞列每則有「關係圖」按鈕 → /api/news/graph?id= → 現有的 NewsGraph 心智圖
```

## 1. 設定（configs/news.yaml；鏡像 news-service/config/news.json，tests/test_news_config.py 核對一致）

```yaml
# T-402：新聞提到哪些實體（一度）——別名比對（SPEC-017 §6）。
mention:
  rule_version: "2026-10-19.1"
  per_run_limit: 400        # 每次 ingest 最多比對幾則（第一次會把既有的全部掃完）
  latin_min: 3              # 拉丁別名至少幾個字元；全大寫 2～5 字的 ticker 例外
  cjk_min: 2                # 中文別名至少幾個字
  stopwords: [ON, ARM, META, 中華, 台灣, 中國, 美國, 國際, 第一, 大成, 統一, 長榮, 中鋼, 台塑, 國泰, 富邦, 新光, 遠東, 東元, 大同, 華新, 正新, 台達]
graph:
  weights: {supplier: 0.6, belongs: 0.5, member: 0.5, peer: 0.4, theme: 0.6}
  fanout_power: 0.5
  max_degree: 3
  max_links: 26
  peers_per_group: 5        # 同族群線：每檔連到族群裡成交金額前幾名
  mentions_per_item: 6      # /v1/items 每則最多回幾個一度實體（台股優先）
```

`validateConfig`（src/ingest.ts）要驗這兩段：缺了或型別不對就 `news 設定錯誤：mention.…`。

## 2. 別名比對（src/mention.ts，純函式）

```ts
export type MentionConfig = { rule_version: string; per_run_limit: number; latin_min: number; cjk_min: number; stopwords: string[] }
export type EntityAliases = { id: string; label: string; aliases: string[] }
export type AliasIndex = { /* 實作自訂 */ }
export type MentionHit = { entity: string; alias: string; evidence: string }
/** 建索引：label 也算別名；去掉 stopwords、太短的；中文別名長的先比。 */
export function buildIndex(entities: EntityAliases[], cfg: MentionConfig): AliasIndex
/** texts＝[title_zh, summary_zh, title, summary]（null 跳過）。同一實體只回一次（第一個命中）。evidence＝命中字串前後各最多 20 字。 */
export function detectMentions(texts: Array<string | null | undefined>, index: AliasIndex): MentionHit[]
```

規則：
- **中文別名**（含任何 CJK 字）：子字串比對，**長的先比、命中的範圍不再給短的用**（「長榮航空」吃掉後「長榮」不會再命中；「台塑化」先於「台塑」）。
- **拉丁別名**：**大小寫要一樣、前後要字界**（`Apple` 命中 `Apple` 不命中 `apple`；`Nvidia`／`NVIDIA`／`NVDA` 各是一條別名）。
  全大寫 2～5 字當 ticker，也要字界；`latin_min` 以下的非 ticker 別名不建。
- `stopwords` 裡的字串不建索引（整條別名）。
- 結果依出現位置排；evidence 不含換行。
- 測試（news-service/tests/mention.test.ts，`npm test`）至少要有：長短中文名、英文大小寫與字界、ticker、stopword、同一實體多別名只回一次、evidence 擷取、空文字。

## 3. 新聞庫的表（news-service/supabase/migrations/20261019020000_news_graph.sql）

```sql
-- T-402／T-404：實體、關係線（參考資料，可更新 valid_to）；提及與比對紀錄（只增）。
create table news.entities (
  id          text primary key,            -- tw:2330 / us:NVDA / co:samsung / grp:半導體 / th:ev
  type        text not null check (type in ('tw','us','company','group','theme')),
  label       text not null,               -- 顯示名（中文）
  sub         text,                        -- 代號、ticker、國家、「族群」「主題」
  aliases     text[] not null default '{}',
  source      text not null,               -- stock_meta / stock_group / config
  valid_from  timestamptz not null default now(),
  valid_to    timestamptz
);
create table news.graph_edges (
  src text not null, dst text not null,
  type   text not null check (type in ('supplier','belongs','member','peer','theme')),
  status text not null check (status in ('approved','draft')),
  label  text,                             -- 線上的說明（同族群線寫族群名）
  rank   int,                              -- belongs：這檔在族群裡的成交金額名次（1 最大）
  source text not null,
  valid_from timestamptz not null default now(),
  valid_to   timestamptz,
  primary key (src, dst, type, valid_from)
);
create index graph_edges_active on news.graph_edges (src) where valid_to is null;
create table news.mentions (
  item_id      text not null references news.raw_items (id),
  entity_id    text not null,
  content_sha  text not null,              -- 比對的是哪一版內容
  method       text not null,              -- alias
  evidence     text not null,
  rule_version text not null,
  created_at   timestamptz not null default now(),
  primary key (item_id, entity_id, content_sha)
);
create table news.mention_runs (           -- 這一版內容比對過了（含 0 命中），免得每次重掃
  item_id      text not null references news.raw_items (id),
  content_sha  text not null,
  n            int not null,
  rule_version text not null,
  ran_at       timestamptz not null default now(),
  primary key (item_id, content_sha)
);
```
mentions／mention_runs 照既有表的做法加「擋 UPDATE／DELETE」的觸發器（看 20261019000000_news_ingest.sql 怎麼寫）；entities／graph_edges 不擋。

## 4. ingest 第三步（functions/ingest/index.ts → src/mention.ts）

翻譯之後跑 `detectPending(cfg.mention, deps)`（放在 src/mention.ts，純函式吃注入的 deps）：
```ts
export type PendingItem = { item_id: string; content_sha: string; title: string | null; summary: string | null; title_zh: string | null; summary_zh: string | null }
export type MentionDeps = {
  now: () => Date
  entities: () => Promise<EntityAliases[]>                 // news.entities where valid_to is null
  pending: (limit: number) => Promise<PendingItem[]>       // raw_items 目前這一版（同 /v1/items 的 lateral 取最新版）且 mention_runs 沒有 (item_id, content_sha) 的，新的先
  save: (hits: Array<{ item_id: string; content_sha: string; entity: string; evidence: string }>, runs: Array<{ item_id: string; content_sha: string; n: number }>, ruleVersion: string) => Promise<void>
}
export async function detectPending(cfg: MentionConfig, deps: MentionDeps): Promise<{ scanned: number; hits: number }>
```
`_shared/io.ts` 加 `mentionDeps(sql)` 實作三個查詢（SQL 只在這裡）。

## 5. 關係圖 API（functions/api/index.ts → src/graphapi.ts）

- `GET /v1/graph?id=<item id>`：找不到 → 404 `{detail:'沒有這則新聞'}`。回應：
```ts
{ item: { id, source_name, url, first_seen_at, title, title_zh }, rule_version: string, nodes: GNode[] }
// GNode 同 frontend/src/cloud/news-graph.ts：{ id, type, label, sub, chg?, degree, score, parent, via, via_label?, draft }
```
- `src/graphapi.ts`（純函式，有測試）：
```ts
export type EntityRow = { id: string; type: EntityType; label: string; sub: string | null }
export type EdgeRow = { src: string; dst: string; type: EdgeType; status: 'approved' | 'draft'; label: string | null; rank: number | null }
export type GraphApiConfig = GraphConfig & { peers_per_group: number }
/** 表裡的線 → 給 propagate 的線：belongs 照放；另外每個族群依 rank 取前 peers_per_group 檔，族群裡每一檔都連到這幾檔（peer，label＝族群名）；draft 狀態 → draft:true */
export function edgesFor(rows: EdgeRow[], entities: Map<string, EntityRow>, cfg: GraphApiConfig): Edge[]
export function buildNodes(mentions: string[], rows: EdgeRow[], entities: Map<string, EntityRow>, cfg: GraphApiConfig): GNode[]
```
  `buildNodes`：`propagate(mentions, edgesFor(...), cfg)` → 每個 Link 轉 GNode（照 news-service/scripts/demo-graph.ts 末段的轉法：`parent=parentOf(l)`、`via=最後一步`、`via_label=最後一步的 label`、`sub=entities.sub ?? id 冒號後面`、`score` 四捨五入到 4 位）；實體表沒有的 id 直接略過（不丟錯）。`chg` 這一版不填。
- `/v1/items` 每則多 `mentions: [{id, type, label}]`：目前這一版內容的 mentions join entities（valid_to is null），台股在前、再國外、再族群／主題，最多 `graph.mentions_per_item` 個。沒有 → `[]`。
- `rule_version` 回 `${cfg.mention.rule_version}`。

## 6. 實體與線的來源

### configs/news_entities.yaml（工作者 C）
```yaml
us:        # 美股：ticker、中文名、別名（英文名各種寫法）
  - {ticker: NVDA, label: 輝達, aliases: [Nvidia, NVIDIA, 輝達, 英偉達]}
companies: # 非美股的國外公司：id（小寫英文）、中文名、國家、別名
  - {id: samsung, label: 三星, country: 韓國, aliases: [Samsung, Samsung Electronics, 三星電子, 三星]}
themes:    # 主題：id、中文名、別名（會出現在標題裡的詞）
  - {id: memory, label: 記憶體, aliases: [記憶體, DRAM, NAND, HBM, memory chip, memory chips]}
```
數量：美股約 120 檔（台股供應鏈會提到的：半導體、AI、雲端、手機、電動車、記憶體、設備、零售龍頭、金融龍頭）、國外公司約 40（三星、SK海力士、ASML、台灣以外的晶圓與設備廠、日韓電子、車廠）、主題約 30（AI、記憶體、電動車、關稅、聯準會／利率、油價、地緣政治…）。別名必須是新聞標題裡真的會出現的寫法；不要把容易撞到普通字的詞當別名（例：`Micron` 可以、`micron` 不行；`Meta` 可以、`META` 放 stopwords）。

### configs/news_edges.yaml（工作者 C）
```yaml
# D-200 §三：國外公司→台灣供應商、主題→族群／台股 由 AI 依公開資訊草擬，使用者核可前都是 draft（網站畫虛線）。
supplier:  # src（us:/co:）→ 台股代號清單
  - {src: "us:NVDA", tw: ["2382", "6669", "2317", "3017", "2308", "3231", "3711"], status: draft}
theme:     # 主題 → 族群名（grp:…，名稱照主庫 stock_group 的 grp）或台股代號
  - {src: "th:memory", grp: ["半導體（記憶體）"], tw: ["2408", "2344", "2337", "8299"], status: draft}
peer:      # 國外公司 ↔ 台股同業（label：同業／結盟）
  - {src: "co:samsung", tw: ["2408"], label: 同業, status: draft}
```
把 news-service/scripts/demo-graph.ts 裡 DRAFT 的那些搬進來（它的 REAL 同族群線改由主庫 stock_group 同步，不搬），再補到約 60 家國外公司各 2～8 家台灣供應商。做完刪掉 demo-graph.ts。
族群名要跟主庫一致：用 `uv run --all-groups python -c "..."` 連 `TWQUANT_SUPABASE_DSN`（.env）查 `select distinct grp from serving.stock_group order by 1`（金鑰不印出來）。

### scripts/news_sync_entities.py（工作者 D）
`uv run --all-groups python scripts/news_sync_entities.py [--dry-run]`：
1. 讀 `D:/project/twquant/.env`（`TWQUANT_SUPABASE_DSN`、`SUPABASE_NEWS_ACCESS_TOKEN`、`TWQUANT_NEWS_PROJECT_REF`；不印值）。
2. 主庫（psycopg）：`serving.stock_meta`（`delist_date is null`）→ `tw:<stock_id>`，label＝name、sub＝stock_id、aliases＝[name]；
   `serving.stock_group`（stock_id, grp）＋ `serving.prices_daily` 最新一天的 `amount` → 每個族群內依 amount 排名 → belongs 線（rank）；族群實體 `grp:<grp>`、sub＝族群。
3. `configs/news_entities.yaml`、`configs/news_edges.yaml` → us／company／theme 實體與草擬線。
4. 新聞庫：用 Management API `POST https://api.supabase.com/v1/projects/<ref>/database/query`（header `authorization: Bearer <token>`，body `{"query": "..."}`）
   分批（每批 ≤ 500 列）執行：entities `insert … on conflict (id) do update set label, sub, aliases, source, valid_to = null`；
   線：現有 `valid_to is null` 的線不在新集合裡 → `update … set valid_to = now()`；新集合裡還沒有的 → insert（status 照設定；belongs＝approved）。
   SQL 字串只能用自己寫的 `sql_literal()` 跳脫（單引號加倍、陣列用 `array['…']::text[]`），測試要驗。
5. 印：實體幾個（分型別）、線幾條（新增／關閉）、花多久。`--dry-run` 只印不寫。
純邏輯（排名、差異、SQL 組字串）拆成函式放同一個檔案，`tests/test_news_sync_entities.py` 用假資料測，不連網；ruff／mypy 要過（`uv run --all-groups ruff check scripts/news_sync_entities.py tests/…`、`uv run --all-groups mypy scripts/news_sync_entities.py`）。

## 7. 網站（工作者 E）

- `frontend/functions/_lib/news-proxy.ts`：`ALLOWED` 多 `'/v1/graph': ['id']`；`frontend/functions/api/news/graph.ts` 轉 `/v1/graph`（照 items.ts 的寫法）。
- `frontend/src/cloud/news.ts`：`NewsRow` 多 `mentions?: { id: string; type: 'tw'|'us'|'company'|'group'|'theme'; label: string }[]`；`api.ts` 多 `fetchNewsGraph(id)` → `get<NewsGraphData>('/api/news/graph?id=…')`。
- `frontend/src/cloud/NewsPage.tsx`：每一列標題下面（meta 那一行前）列最多 3 個實體小標（名字，前面一個類別色點：台股橘 `#d95926`、國外藍 `#3987e5`、族群／主題青 `#199e70`，同 news-graph.css 的三色）＋超過的「+N」；有 mentions 的列多一顆「關係圖」按鈕（lucide `Network` 圖示＋兩個字），點了抓 `/api/news/graph` 開現有的 `NewsGraph`（lazy，照現在的寫法），讀取中按鈕寫「讀取中…」、失敗在列裡顯示一行錯誤。
  **拿掉**「關係圖（示範）」整區、`demoMode`、`news-graph-demo.json` 的動態載入；刪 `frontend/src/cloud/news-graph-demo.json`；`frontend/tests/cloud-news-graph.test.ts` 裡讀示範資料的那一個 test 刪掉；`frontend/tests/shot-news-graph.cjs` 改成點第一則有「關係圖」按鈕的列（mock 的 items 要給 mentions）。
- 字級照網站規則：小標 ≥ 12.5px、按鈕 ≥ 13px；手機 390px 截圖確認列不會擠爆（`frontend/tests/shot-news-graph.cjs` 跑在 `http://localhost:8804`，items／health 的 mock 在 `C:/t/mis/news-items-tr.json`、`C:/t/mis/news-health.json`——自己在 mock 裡給前幾則 mentions）。
- `npm test`、`npx tsc -b`、`VITE_APP_MODE=cloud npx vite build` 要過。

## 8. 檔案歸屬（不得越界）

| 工作者 | 檔案 |
|---|---|
| A 比對 | `news-service/src/mention.ts`、`news-service/tests/mention.test.ts`、`configs/news.yaml`（只加 mention／graph 兩段）、`news-service/config/news.json`（鏡像）、`news-service/src/ingest.ts`（只動 `NewsConfig` 型別與 `validateConfig`） |
| B 服務 | `news-service/supabase/migrations/20261019020000_news_graph.sql`、`news-service/src/graphapi.ts`、`news-service/tests/graphapi.test.ts`、`news-service/supabase/functions/api/index.ts`、`news-service/supabase/functions/ingest/index.ts`、`news-service/supabase/functions/_shared/io.ts` |
| C 資料 | `configs/news_entities.yaml`、`configs/news_edges.yaml`、`tests/test_news_entities.py`、刪 `news-service/scripts/demo-graph.ts` |
| D 同步 | `scripts/news_sync_entities.py`、`tests/test_news_sync_entities.py` |
| E 網站 | `frontend/functions/_lib/news-proxy.ts`、`frontend/functions/api/news/graph.ts`、`frontend/src/cloud/{news.ts,api.ts,NewsPage.tsx,news-v2.css}`、`frontend/tests/{cloud-news-graph.test.ts,shot-news-graph.cjs,cloud-news*.test.ts}`、刪 `frontend/src/cloud/news-graph-demo.json` |

B 需要 A 的 `src/mention.ts` 介面（§2、§4）——照本文件寫，不等 A；型別對不上由整合者修。
A 與 B 都會動 `src/ingest.ts`？不：B 不碰 `src/ingest.ts`。

## 9. 部署與回填（整合者）

`cd news-service && supabase db push` → `functions deploy ingest api --use-api --no-verify-jwt` → `uv run python scripts/news_sync_entities.py` →
`curl -X POST …/functions/v1/ingest`（第一次會把既有新聞全部比對）→ `/v1/items` 看 mentions → 抽 40 則人工／AI 核對命中是否正確。
