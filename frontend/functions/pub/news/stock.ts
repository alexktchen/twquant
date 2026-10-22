// GET /pub/news/stock?id=tw:2382：個股頁「相關新聞」（反向看圖），轉給新聞服務 /v1/stock-news。公開（T-434，不用登入）：同一執行個體 60 秒暫存、瀏覽器也留 60 秒。
import { cached, cacheKey } from '../../_lib/cache'
import { pubProxy, type NewsEnv } from '../../_lib/news-proxy'

export const onRequestGet: PagesFunction<NewsEnv> = ({ request, env }) => cached(cacheKey(request), () => pubProxy(env, '/v1/stock-news', request.url))
