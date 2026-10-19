// GET /api/news/stock?id=tw:2382：個股頁「相關新聞」（反向看圖），轉給新聞服務 /v1/stock-news。每個人看到都一樣：同一執行個體 60 秒暫存。
import { cached, cacheKey } from '../../_lib/cache'
import { proxy, type NewsEnv } from '../../_lib/news-proxy'

export const onRequestGet: PagesFunction<NewsEnv> = ({ request, env }) => cached(cacheKey(request), () => proxy(env, '/v1/stock-news', request.url))
