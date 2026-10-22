// GET /pub/news/summary?id=…：點開一則英文新聞時才翻它的摘要（D-207：排程只翻標題），轉給新聞服務 /v1/summary。
// 翻過的直接給；沒翻過要等模型幾秒，所以逾時給長一點。公開（T-434，不用登入）：同一執行個體 60 秒暫存、瀏覽器也留 60 秒。
import { cached, cacheKey } from '../../_lib/cache'
import { pubProxy, SUMMARY_TIMEOUT_MS, type NewsEnv } from '../../_lib/news-proxy'

export const onRequestGet: PagesFunction<NewsEnv> = ({ request, env }) =>
  cached(cacheKey(request), () => pubProxy(env, '/v1/summary', request.url, fetch, { timeoutMs: SUMMARY_TIMEOUT_MS }))
