// 登出。Cloudflare Access 免費方案不能自訂登入／登出頁，所以登出改由本站處理：
// 1. 伺服器端撤銷這位使用者的所有 Access 登入（含 cloudflareaccess.com 上的全域登入；
//    只清本站 cookie 的話，按「重新登入」會不經 Google 直接回來，無法換帳號）。
// 2. 清掉本站的 CF_Authorization cookie。前端接著導到我們自己的 /signed-out。
export type LogoutEnv = { CF_ACCESS_API_TOKEN?: string; CF_ACCOUNT_ID?: string }

export const EMAIL_HEADER = 'cf-access-authenticated-user-email'
export const CLEAR_COOKIE = 'CF_Authorization=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Lax'
const API = 'https://api.cloudflare.com/client/v4'

const reply = (status: number, body: unknown, extra: Record<string, string> = {}): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', ...extra },
  })

/** 撤銷登入並清 cookie；失敗回非 200，前端會改走 Cloudflare 的登出頁。 */
export async function logout(request: Request, env: LogoutEnv, fetchImpl: typeof fetch = fetch): Promise<Response> {
  const email = request.headers.get(EMAIL_HEADER)
  if (!email) return reply(401, { detail: '沒有登入身分' })
  if (!env.CF_ACCESS_API_TOKEN || !env.CF_ACCOUNT_ID) {
    return reply(503, { detail: '登出未設定：缺 CF_ACCESS_API_TOKEN 或 CF_ACCOUNT_ID' })
  }
  const res = await fetchImpl(`${API}/accounts/${env.CF_ACCOUNT_ID}/access/organizations/revoke_user`, {
    method: 'POST',
    headers: { authorization: `Bearer ${env.CF_ACCESS_API_TOKEN}`, 'content-type': 'application/json' },
    body: JSON.stringify({ email }),
  })
  const body = (await res.json().catch(() => null)) as { success?: boolean } | null
  if (!res.ok || body?.success !== true) return reply(502, { detail: `撤銷登入失敗（HTTP ${res.status}）` })
  return reply(200, { ok: true }, { 'set-cookie': CLEAR_COOKIE })
}
