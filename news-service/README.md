# news-service — 新聞洞悉層微服務

規格：[docs/specs/SPEC-017-news-insight.md](../docs/specs/SPEC-017-news-insight.md)；裁決 D-200。
自己的資料庫（Supabase 第二個免費專案）、自己的排程與部署；其他程式只透過 API v1 讀，不碰它的表。

```
news-service/
├── src/            純函式（解析、進料、翻譯與檢查、API 參數與健康狀態）——唯一的一份新聞邏輯，node 測試
├── config/         configs/news.yaml 的 JSON 鏡像（Edge Function 讀不到 repo；tests/test_news_config.py 核對一致）
├── supabase/
│   ├── migrations/ 資料表（只增不改，觸發器擋 UPDATE／DELETE）
│   ├── functions/  ingest（排程：進料→翻譯）、api（/v1/items、/v1/health）、_shared（資料庫、HTTP、Workers AI）
│   └── setup/      排程 SQL（含金鑰，不放 migrations）
└── tests/          npm test（錄好的真實回應，不連網）
```

## 測試

```bash
cd news-service && npm test
```

## 第一次部署（使用者執行；AI 的自動模式擋正式部署）

需要：新聞專案的專案代號（`TWQUANT_NEWS_PROJECT_REF`）。兩把金鑰自己產生、只放 Supabase 祕密與 `.env`，不貼對話：

```bash
cd news-service
npx supabase login                                   # 開瀏覽器登入
npx supabase link --project-ref <專案代號>
npx supabase db push                                 # 建表（migrations/）
node -e "console.log(crypto.randomUUID())"           # 產生兩次，一把給排程、一把給 API
npx supabase secrets set NEWS_INGEST_KEY=<第一把> NEWS_API_KEY=<第二把>
# 翻譯（T-401）：同 .env 的 CLOUDFLARE_ACCOUNT_ID、CLOUDFLARE_AI_TOKEN（Workers AI）
npx supabase secrets set CLOUDFLARE_ACCOUNT_ID=<帳號> CLOUDFLARE_AI_TOKEN=<token>
npx supabase functions deploy ingest api --use-api        # 沒有 Docker 就加 --use-api
```

接著在 Supabase 後台 SQL Editor：先執行 `supabase/setup/schedule.sql` 檔頭那兩行 `vault.create_secret`（專案網址、第一把金鑰），再執行整個檔案。

不想開後台的話，用 Management API 跑 SQL（2026-10-19 實際這樣部署的；token 放 `.env` 的 `SUPABASE_NEWS_ACCESS_TOKEN`，CLI 也吃 `SUPABASE_ACCESS_TOKEN` 環境變數）：

```bash
curl -X POST https://api.supabase.com/v1/projects/<專案代號>/database/query \n  -H "authorization: Bearer $SUPABASE_NEWS_ACCESS_TOKEN" -H 'content-type: application/json' \n  -d '{"query":"select vault.create_secret(...); ..."}'
```

部署函式沒有 Docker 時加 `--use-api`；`config.toml` 已把兩個函式設成 `verify_jwt = false`（只認 `x-news-key`）。
**陷阱**：往 `.env` 追加時，上一行若沒有換行，新的一行會黏在上一行後面（2026-10-19 金鑰黏到密碼後面、Vault 存成空字串、ingest 回 403）——追加前先補換行、追加後核對每個值的長度。

確認：

```bash
curl -H "x-news-key: <第二把>" https://<專案代號>.supabase.co/functions/v1/api/v1/health
```

半小時內 `last_ok_at` 會有時間；`sources` 裡 `failing: true` 的來源看 `last_error`。

## 關係圖的實體與線（T-402／T-404）

```bash
uv run --all-groups python scripts/news_sync_entities.py --dry-run   # 先看會改什麼
uv run --all-groups python scripts/news_sync_entities.py             # 主庫 stock_meta／stock_group＋configs/news_entities.yaml、news_edges.yaml → news.entities／graph_edges
```

主庫族群表每 7 天重算，同步跟著跑就好（只改族群名次的線原地更新，其他變動關舊插新）。改了 `configs/news.yaml` 的 `mention`（停用詞、擋字）要把 `rule_version` 加一號，下一輪 ingest 自動重掃全部新聞。

## 核可頁（T-403）

AI 草擬的線（`graph_edges.status = draft`，來自 `configs/news_edges.yaml`）要擁有者核可：`GET／POST /v1/review`。核可＝草稿補 `valid_to`、插一條 approved（`valid_from`＝核可時間）；退回＝只補 `valid_to`；每個決定記在 `news.reviews`（只增）。

- 擁有者名單是 Edge Function 祕密 `NEWS_OWNER_EMAILS`（逗號分隔，不進版控）：`npx supabase secrets set NEWS_OWNER_EMAILS=<email>`。
- 登入者由網站驗過 Cloudflare Access 權杖後放在 `x-news-user` 轉來；其他呼叫端不帶 → `can_review: false`。
- 同步腳本（`scripts/news_sync_entities.py`）讀 `news.reviews` 最新決定：核可過的維持 approved、退回的不再插回去。
- 非擁有者送出回 400（不回 403：網站把 403 當成要重新登入）。

## 向量（T-410）

ingest 第五步用 Workers AI bge-m3 算向量（`news.embeddings`）、同事件合併（`news.clusters`，只新併舊）；英文新聞翻譯完才算。端點 `/v1/similar?id=`、`/v1/search?q=`。設定在 `configs/news.yaml` 的 `embed`。

**一律指定區域**：呼叫函式時帶 `x-region: ap-southeast-1`（資料庫在新加坡；不帶會在東京跑、每個查詢跨區，慢 3～4 倍）。`supabase/setup/schedule.sql` 已帶；改過排程要 `cron.unschedule` 再重跑。量測見 `docs/reports/news-vectors-t410-2026-10-20.md`。

## 之後改程式

- 改 `configs/news.yaml` → 同步改 `config/news.json` → `npx supabase functions deploy ingest api`。
- 改資料表：只能新增 migration，不改舊的。
