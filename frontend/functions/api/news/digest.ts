// GET /api/news/digest：新聞三張卡（T-417：熱門主題、多家同報、股票熱度），轉給新聞服務 /v1/digest。每個人看到都一樣：同一執行個體 60 秒暫存。
import { cached, cacheKey } from '../../_lib/cache'
import { proxy, type NewsEnv } from '../../_lib/news-proxy'

export const onRequestGet: PagesFunction<NewsEnv> = ({ request, env }) => cached(cacheKey(request), () => proxy(env, '/v1/digest', request.url))
