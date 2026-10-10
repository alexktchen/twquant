import assert from 'node:assert/strict'
import test from 'node:test'
import { CLEAR_COOKIE, EMAIL_HEADER, logout } from '../functions/_lib/logout.ts'

const env = { CF_ACCESS_API_TOKEN: 'tok', CF_ACCOUNT_ID: 'acc' }
const req = (email?: string) =>
  new Request('https://twquant.pages.dev/api/logout', { method: 'POST', headers: email ? { [EMAIL_HEADER]: email } : {} })
const api = (status: number, body: unknown) => async () => new Response(JSON.stringify(body), { status })

test('T-332 登出：撤銷這位使用者的 Access 登入並清 cookie', async () => {
  const calls: { url: string; init: RequestInit }[] = []
  const fake = (async (url: string, init: RequestInit) => {
    calls.push({ url, init })
    return new Response(JSON.stringify({ success: true }), { status: 200 })
  }) as typeof fetch
  const res = await logout(req('a@example.com'), env, fake)
  assert.equal(res.status, 200)
  assert.equal(res.headers.get('set-cookie'), CLEAR_COOKIE)
  assert.equal(res.headers.get('cache-control'), 'no-store')
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, 'https://api.cloudflare.com/client/v4/accounts/acc/access/organizations/revoke_user')
  assert.equal((calls[0].init.headers as Record<string, string>).authorization, 'Bearer tok')
  assert.deepEqual(JSON.parse(String(calls[0].init.body)), { email: 'a@example.com' })
})

test('T-332 登出：沒有身分、未設定、Cloudflare 失敗都不回 200（前端改走 Cloudflare 登出）', async () => {
  const never = (async () => assert.fail('不該呼叫 API')) as typeof fetch
  assert.equal((await logout(req(), env, never)).status, 401)
  assert.equal((await logout(req('a@example.com'), {}, never)).status, 503)
  assert.equal((await logout(req('a@example.com'), env, api(403, { success: false }) as typeof fetch)).status, 502)
  assert.equal((await logout(req('a@example.com'), env, api(200, { success: false }) as typeof fetch)).status, 502)
  const notJson = (async () => new Response('<html>', { status: 200 })) as typeof fetch
  const res = await logout(req('a@example.com'), env, notJson)
  assert.equal(res.status, 502)
  assert.equal(res.headers.get('set-cookie'), null)
})
