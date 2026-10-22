// GET /pub/news/graph?id=…：一則新聞的關係圖（T-402／T-404），轉給新聞服務 /v1/graph。公開（T-434，不用登入）：同一執行個體 60 秒暫存、瀏覽器也留 60 秒。
import { cached, cacheKey } from '../../_lib/cache'
import { pubProxy, type NewsEnv } from '../../_lib/news-proxy'

export const onRequestGet: PagesFunction<NewsEnv> = ({ request, env }) => cached(cacheKey(request), () => pubProxy(env, '/v1/graph', request.url))
