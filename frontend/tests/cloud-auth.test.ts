import assert from 'node:assert/strict'
import test from 'node:test'
import { LoginRequired, guestCandles, guestDay, needsLogin } from '../src/cloud/auth.ts'

const res = (type: string, status: number, ct: string) => ({
  type,
  status,
  ok: status >= 200 && status < 300,
  headers: new Headers({ 'content-type': ct }),
})

test('T-332 沒登入：Access 的導向、401／403、拿到 HTML 登入頁都算要登入；LoginRequired 記得是被導走還是被拒', () => {
  assert.equal(needsLogin(res('opaqueredirect', 0, '')), true)
  assert.equal(needsLogin(res('basic', 401, 'application/json')), true)
  assert.equal(needsLogin(res('basic', 403, 'text/html')), true)
  assert.equal(needsLogin(res('basic', 200, 'text/html')), true)
  assert.equal(needsLogin(res('basic', 200, 'application/json; charset=utf-8')), false)
  assert.equal(needsLogin(res('basic', 404, 'application/json')), false)
  assert.equal(needsLogin(res('basic', 500, 'text/html')), false)
  assert.ok(new LoginRequired('denied') instanceof Error)
  // T-435 過渡期：有 Supabase 登入卻被 Cloudflare Access 導走（redirect）＝管理者還沒關 Access；401（denied）＝權杖失效
  assert.equal(new LoginRequired('redirect').reason, 'redirect')
  assert.equal(new LoginRequired('denied').reason, 'denied')
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
