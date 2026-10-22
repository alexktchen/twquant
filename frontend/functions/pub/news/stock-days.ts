// GET /pub/news/stock-days?id=tw:2330：個股頁 K 線上的新聞標記（T-419），轉給新聞服務 /v1/stock-news-days。公開（T-438，不用登入）：同一執行個體 60 秒暫存、瀏覽器也留 60 秒。
import { cached, cacheKey } from '../../_lib/cache'
import { pubProxy, type NewsEnv } from '../../_lib/news-proxy'

export const onRequestGet: PagesFunction<NewsEnv> = ({ request, env }) => cached(cacheKey(request), () => pubProxy(env, '/v1/stock-news-days', request.url))
