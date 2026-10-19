// GET /api/news/items：新聞流（T-400），轉給新聞服務 /v1/items。每個人看到都一樣：同一執行個體 60 秒暫存。
import { cached, cacheKey } from '../../_lib/cache'
import { proxy, type NewsEnv } from '../../_lib/news-proxy'

export const onRequestGet: PagesFunction<NewsEnv> = ({ request, env }) => cached(cacheKey(request), () => proxy(env, '/v1/items', request.url))
