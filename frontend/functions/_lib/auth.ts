// 雲端版登入狀態（T-435／D-210：Supabase Auth；過渡期 Cloudflare Access 的權杖照收）。
// 網站外殼與新聞公開；要身分的 /api 端點各自驗權杖（_lib/identity.ts）。
import { userEmail, type IdentityEnv, type UserOpts } from './identity.ts'

const NO_STORE = { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' }

/** GET /api/me：目前登入的 email（只回驗得過的權杖，不信任何標頭）。 */
export async function me(request: Request, env: IdentityEnv, opts?: UserOpts): Promise<Response> {
  const email = await userEmail(request, env, opts)
  if (!email) return new Response(JSON.stringify({ detail: '沒有登入身分' }), { status: 401, headers: NO_STORE })
  return new Response(JSON.stringify({ email }), { headers: NO_STORE })
}
