// GET /pub/news/digest：新聞三張卡（T-417：熱門主題、多家同報、股票熱度），轉給新聞服務 /v1/digest。公開（T-438，不用登入）：同一執行個體 60 秒暫存、瀏覽器也留 60 秒。
import { cached, cacheKey } from '../../_lib/cache'
import { pubProxy, type NewsEnv } from '../../_lib/news-proxy'

export const onRequestGet: PagesFunction<NewsEnv> = ({ request, env }) => cached(cacheKey(request), () => pubProxy(env, '/v1/digest', request.url))
