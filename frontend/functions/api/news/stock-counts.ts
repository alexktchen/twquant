// GET /api/news/stock-counts?ids=tw:2330,tw:2382：名單上每檔近 3 天有幾則相關新聞（小標用），轉給新聞服務 /v1/stock-news-counts。60 秒暫存。
import { cached, cacheKey } from '../../_lib/cache'
import { proxy, type NewsEnv } from '../../_lib/news-proxy'

export const onRequestGet: PagesFunction<NewsEnv> = ({ request, env }) => cached(cacheKey(request), () => proxy(env, '/v1/stock-news-counts', request.url))
