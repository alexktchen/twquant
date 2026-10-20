// GET／POST /api/news/review：核可頁（T-403），轉給新聞服務 /v1/review。登入者身分來自驗過的 Cloudflare Access 權杖（不信任何標頭），
// 放在 x-news-user 轉過去；是不是擁有者由新聞服務比對祕密名單決定。每個人看到的不一樣 → 不暫存。
import { accessEmail } from '../../_lib/identity'
import { proxy, reviewPost, type NewsEnv } from '../../_lib/news-proxy'

export const onRequestGet: PagesFunction<NewsEnv> = async ({ request, env }) =>
  proxy(env, '/v1/review', request.url, fetch, { user: await accessEmail(request) })

export const onRequestPost: PagesFunction<NewsEnv> = ({ request, env }) => reviewPost(request, env, (r) => accessEmail(r))
