// GET /pub/news/evidence?ids=<新聞 id,…>&entities=<實體 id,…>：證據句（T-437）——比對名字時存下的那段字，轉給新聞服務 /v1/evidence。
// 公開（T-438，不用登入）：同一執行個體 60 秒暫存、瀏覽器也留 60 秒。
import { cached, cacheKey } from '../../_lib/cache'
import { pubProxy, type NewsEnv } from '../../_lib/news-proxy'

export const onRequestGet: PagesFunction<NewsEnv> = ({ request, env }) => cached(cacheKey(request), () => pubProxy(env, '/v1/evidence', request.url))
