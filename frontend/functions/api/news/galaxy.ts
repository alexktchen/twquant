// GET /api/news/galaxy：新聞全景星雲圖（T-418），轉給新聞服務 /v1/galaxy（排程算好的最新一筆，一天更新幾次）。每個人看到都一樣：同一執行個體 60 秒暫存。
import { cached, cacheKey } from '../../_lib/cache'
import { proxy, type NewsEnv } from '../../_lib/news-proxy'

export const onRequestGet: PagesFunction<NewsEnv> = ({ request, env }) => cached(cacheKey(request), () => proxy(env, '/v1/galaxy', request.url))
