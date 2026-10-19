// GET /api/news/health：新聞服務最後抓取時間與各來源狀態（T-400），轉給 /v1/health。60 秒暫存。
import { cached, cacheKey } from '../../_lib/cache'
import { proxy, type NewsEnv } from '../../_lib/news-proxy'

export const onRequestGet: PagesFunction<NewsEnv> = ({ request, env }) => cached(cacheKey(request), () => proxy(env, '/v1/health', request.url))
