// GET /api/news/thread?id=…：這件事的來龍去脈（T-420），轉給新聞服務 /v1/thread。每個人看到都一樣：同一執行個體 60 秒暫存。
import { cached, cacheKey } from '../../_lib/cache'
import { proxy, type NewsEnv } from '../../_lib/news-proxy'

export const onRequestGet: PagesFunction<NewsEnv> = ({ request, env }) => cached(cacheKey(request), () => proxy(env, '/v1/thread', request.url))
