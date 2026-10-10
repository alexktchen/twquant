// 雲端版登入狀態。Cloudflare Access 只保護 /api/*，網站外殼公開：
// 沒登入的人看到模糊的假名單與登入按鈕，真資料只會從 /api 給已登入的人。
import { EMAIL_HEADER } from './logout.ts'

const NO_STORE = { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' }

/** GET /api/me：目前登入的 email（Access 驗過身分才會進到這裡，並帶上 email 標頭）。 */
export function me(request: Request): Response {
  const email = request.headers.get(EMAIL_HEADER)
  if (!email) return new Response(JSON.stringify({ detail: '沒有登入身分' }), { status: 401, headers: NO_STORE })
  return new Response(JSON.stringify({ email }), { headers: NO_STORE })
}

/** GET /api/login：Access 先把沒登入的人送去 Google，登入完回到這裡，再轉回首頁。 */
export function afterLogin(request: Request): Response {
  return new Response(null, {
    status: 302,
    headers: { location: new URL('/', request.url).toString(), 'cache-control': 'no-store' },
  })
}
