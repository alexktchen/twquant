// GET /pub/news/galaxy：新聞全景星雲圖（T-418），轉給新聞服務 /v1/galaxy（排程算好的最新一筆，一天更新幾次）。公開（T-434，不用登入）：同一執行個體 60 秒暫存、瀏覽器也留 60 秒。
import { cached, cacheKey } from '../../_lib/cache'
import { pubProxy, type NewsEnv } from '../../_lib/news-proxy'

export const onRequestGet: PagesFunction<NewsEnv> = ({ request, env }) => cached(cacheKey(request), () => pubProxy(env, '/v1/galaxy', request.url))
