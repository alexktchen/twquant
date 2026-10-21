// GET /api/prototype/galaxy：公開星雲圖（使用者 2026-10-21：「我要這位置直接可以看」「我想要不登入」）。
// Cloudflare Access 只對這一條路徑開例外（scratchpad/cf_access_public_galaxy.py）：頁面與資料都在這個網址，
// 不連帶公開 /api/news/* 或其他端點。
//   ?format=json → 新聞服務 /v1/galaxy（排程算好的最新一筆，一天更新幾次；同一執行個體 60 秒暫存）
//   其他        → 網站本體（單一 index.html），main.tsx 看路徑只載入公開星雲圖
import { cached } from '../../_lib/cache'
import { proxy, type NewsEnv } from '../../_lib/news-proxy'

type Env = NewsEnv & { ASSETS: Fetcher }

export const onRequestGet: PagesFunction<Env> = async ({ request, env }) => {
  const url = new URL(request.url)
  if (url.searchParams.get('format') === 'json') {
    const data = new URL(request.url)
    data.search = ''
    return cached('/api/prototype/galaxy?format=json', () => proxy(env, '/v1/galaxy', data.toString()))
  }
  const page = await env.ASSETS.fetch(new URL('/', request.url))
  return new Response(page.body, { status: page.status, headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'public, max-age=300' } })
}
