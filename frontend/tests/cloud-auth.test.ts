import assert from 'node:assert/strict'
import test from 'node:test'
import { afterLogin, me } from '../functions/_lib/auth.ts'
import { EMAIL_HEADER } from '../functions/_lib/logout.ts'
import { LOGIN_URL, LoginRequired, guestCandles, guestPicks, needsLogin } from '../src/cloud/auth.ts'

const res = (type: string, status: number, ct: string) => ({
  type,
  status,
  ok: status >= 200 && status < 300,
  headers: new Headers({ 'content-type': ct }),
})

test('T-332 沒登入：Access 的導向、401／403、拿到 HTML 登入頁都算要登入', () => {
  assert.equal(needsLogin(res('opaqueredirect', 0, '')), true)
  assert.equal(needsLogin(res('basic', 401, 'application/json')), true)
  assert.equal(needsLogin(res('basic', 403, 'text/html')), true)
  assert.equal(needsLogin(res('basic', 200, 'text/html')), true)
  assert.equal(needsLogin(res('basic', 200, 'application/json; charset=utf-8')), false)
  assert.equal(needsLogin(res('basic', 404, 'application/json')), false)
  assert.equal(needsLogin(res('basic', 500, 'text/html')), false)
  assert.ok(new LoginRequired() instanceof Error)
  assert.ok(LOGIN_URL.startsWith('/api/'), '登入入口必須在受 Access 保護的 /api 底下')
})

test('T-332 沒登入：預覽用的假名單與假 K 線固定、和真實股票無關', () => {
  const picks = guestPicks()
  assert.deepEqual(picks, guestPicks())
  assert.equal(picks.length, 10)
  for (const p of picks) {
    assert.equal(p.sid, '0000')
    assert.equal(p.recent.length, 6)
    assert.equal(p.score, null)
  }
  const bars = guestCandles()
  assert.deepEqual(bars, guestCandles())
  for (const b of bars) assert.ok(b.h >= Math.max(b.o, b.c) && b.l <= Math.min(b.o, b.c))
})

test('T-332 登入：/api/me 只回 Access 給的 email，/api/login 登入後轉回首頁', async () => {
  const anon = me(new Request('https://twquant.pages.dev/api/me'))
  assert.equal(anon.status, 401)
  const ok = me(new Request('https://twquant.pages.dev/api/me', { headers: { [EMAIL_HEADER]: 'a@example.com' } }))
  assert.equal(ok.status, 200)
  assert.equal(ok.headers.get('cache-control'), 'no-store')
  assert.deepEqual(await ok.json(), { email: 'a@example.com' })
  const back = afterLogin(new Request('https://twquant.pages.dev/api/login'))
  assert.equal(back.status, 302)
  assert.equal(back.headers.get('location'), 'https://twquant.pages.dev/')
})
