import assert from 'node:assert/strict'
import test from 'node:test'
import { afterLogin, me } from '../functions/_lib/auth.ts'
import { EMAIL_HEADER } from '../functions/_lib/logout.ts'
import { LOGIN_URL, LoginRequired, guestCandles, guestDay, logoutDestination, needsLogin } from '../src/cloud/auth.ts'

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

test('T-332 沒登入：假名單每天每條線固定、切日期會變、代號不是真股票', () => {
  const day = guestDay('2026-10-08')
  assert.deepEqual(day, guestDay('2026-10-08'))
  assert.notDeepEqual(day.lines[0].picks, guestDay('2026-10-07').lines[0].picks)
  assert.notDeepEqual(day.lines[0].picks, day.lines[1].picks)
  assert.deepEqual(
    day.lines.map((l) => [l.code, l.name, l.picks.length]),
    // D-174（使用者 2026-10-11 決定）第三條「高分爆量」：多數日子很少，假名單只放 3 檔
    [['CORE', '大盤精選', 15], ['QUIET', '潛伏飆股', 15], ['QUIETV', '高分爆量', 3]],
  )
  for (const l of day.lines) {
    assert.equal(new Set(l.picks.map((p) => p.sid)).size, l.picks.length, '代號不重複（React key）')
    for (const p of l.picks) {
      assert.ok(/^00\d\d$/.test(p.sid) && p.sid < '0050', `${p.sid} 必須是台股不存在的代號`)
      assert.equal(p.recent.length, 6)
      assert.equal(p.score, null)
    }
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

test('登出去哪：撤銷成功或登入早已過期都回首頁；撤銷失敗、斷線才退回 Cloudflare 登出頁', () => {
  const json = new Headers({ 'content-type': 'application/json' })
  const res = (status: number, type = 'basic') => ({ type, status, ok: status >= 200 && status < 300, headers: json })
  assert.equal(logoutDestination(res(200)), '/')
  // 登入過期：Access 把 POST /api/logout 導去登入頁（manual 模式下是 opaqueredirect），或回 401／403
  assert.equal(logoutDestination({ type: 'opaqueredirect', status: 0, ok: false, headers: new Headers() }), '/')
  assert.equal(logoutDestination(res(401)), '/')
  assert.equal(logoutDestination(res(403)), '/')
  // 還登入著但撤銷失敗：要走 Cloudflare 的登出頁，確保登得出去
  assert.equal(logoutDestination(res(502)), '/cdn-cgi/access/logout')
  assert.equal(logoutDestination(res(503)), '/cdn-cgi/access/logout')
  assert.equal(logoutDestination(null), '/cdn-cgi/access/logout')
})
