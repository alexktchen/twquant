// GET /api/news/graph?id=…：一則新聞的關係圖（T-402／T-404），轉給新聞服務 /v1/graph。每個人看到都一樣：同一執行個體 60 秒暫存。
import { cached, cacheKey } from '../../_lib/cache'
import { proxy, type NewsEnv } from '../../_lib/news-proxy'

export const onRequestGet: PagesFunction<NewsEnv> = ({ request, env }) => cached(cacheKey(request), () => proxy(env, '/v1/graph', request.url))
