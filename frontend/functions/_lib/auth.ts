// 雲端版登入狀態（T-439／D-210：Supabase Auth；過渡期 Cloudflare Access 的權杖照收）。
// 網站外殼與新聞公開；要身分的 /api 端點各自驗權杖（_lib/identity.ts）。
import { userEmail, type IdentityEnv, type UserOpts } from './identity.ts'

const NO_STORE = { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' }

/** GET /api/me：目前登入的 email（只回驗得過的權杖，不信任何標頭）。 */
export async function me(request: Request, env: IdentityEnv, opts?: UserOpts): Promise<Response> {
  const email = await userEmail(request, env, opts)
  if (!email) return new Response(JSON.stringify({ detail: '沒有登入身分' }), { status: 401, headers: NO_STORE })
  return new Response(JSON.stringify({ email }), { headers: NO_STORE })
}

const redirect = (request: Request, path: string): Response =>
  new Response(null, {
    status: 302,
    headers: { location: new URL(path, request.url).toString(), 'cache-control': 'no-store' },
  })

/** GET /api/login（T-457）：受主應用（/api）保護，Access 驗完身分才會進到這裡，轉回首頁。 */
export function afterLogin(request: Request): Response {
  return redirect(request, '/')
}

/** GET /api/login/google（T-457）：Cloudflare 上是另一個 Access 應用（只開 Google＋Instant Auth），按了直接到 Google。
 *  它發的通行證 AUD 不是主應用的，直接回首頁的話每個 /api 都會被 Access 導走、畫面停在沒登入；
 *  先整頁轉 /api/login，Access 用剛建立的全域登入換主應用的通行證，不會再問一次。那個應用不在時這裡歸主應用，一樣走得通。 */
export function viaMainLogin(request: Request): Response {
  return redirect(request, '/api/login')
}
