# news-service — 新聞洞悉層微服務

規格：[docs/specs/SPEC-017-news-insight.md](../docs/specs/SPEC-017-news-insight.md)；裁決 D-200。
自己的資料庫（Supabase 第二個免費專案）、自己的排程與部署；其他程式只透過 API v1 讀，不碰它的表。

```
news-service/
├── src/            純函式（解析、進料流程、API 參數與健康狀態）——唯一的一份新聞邏輯，node 測試
├── config/         configs/news.yaml 的 JSON 鏡像（Edge Function 讀不到 repo；tests/test_news_config.py 核對一致）
├── supabase/
│   ├── migrations/ 資料表（只增不改，觸發器擋 UPDATE／DELETE）
│   ├── functions/  ingest（排程進料）、api（/v1/items、/v1/health）、_shared（資料庫與 HTTP）
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
npx supabase functions deploy ingest api
```

接著在 Supabase 後台 SQL Editor：先執行 `supabase/setup/schedule.sql` 檔頭那兩行 `vault.create_secret`（專案網址、第一把金鑰），再執行整個檔案。

確認：

```bash
curl -H "x-news-key: <第二把>" https://<專案代號>.supabase.co/functions/v1/api/v1/health
```

半小時內 `last_ok_at` 會有時間；`sources` 裡 `failing: true` 的來源看 `last_error`。

## 之後改程式

- 改 `configs/news.yaml` → 同步改 `config/news.json` → `npx supabase functions deploy ingest api`。
- 改資料表：只能新增 migration，不改舊的。
