// GET /api/news/search?q=…：新聞語意搜尋（T-410），轉給新聞服務 /v1/search。同一個字 60 秒內不重算（同一執行個體暫存，省 AI 額度）。
import { cached, cacheKey } from '../../_lib/cache'
import { proxy, type NewsEnv } from '../../_lib/news-proxy'

export const onRequestGet: PagesFunction<NewsEnv> = ({ request, env }) => cached(cacheKey(request), () => proxy(env, '/v1/search', request.url))
