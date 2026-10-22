// T-430／D-208：每日名單與績效只給 admin（使用者 2026-10-22：「可以把每日名單跟績效變成admin才能看到的功能」）。
// 後端每支端點自己擋、暫存在擋之後；首頁與個股評分依權限分開暫存；前端沒權限就沒有分頁、首頁卡與名單標記。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { ACCESS_AUD, ACCESS_TEAM, JWT_HEADER } from '../functions/_lib/identity.ts'
import { allowed, denied, gatedKey } from '../functions/_lib/gate.ts'
import { FEATURE_ROLES, ROLE_MEMBERS, can, featuresOf } from '../functions/_lib/roles.ts'
import { FEATURES, lockedText, tabAllowed, visibleLayout } from '../src/cloud/access.ts'
import { defaultLayout } from '../src/cloud/layout.ts'

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8')

// ── 身分：Cloudflare Access 簽的權杖（同 cloud-watchlist.test.ts） ─────────────────
const enc = (o: unknown) => Buffer.from(JSON.stringify(o)).toString('base64url')
async function signer(kid: string) {
  const pair = (await crypto.subtle.generateKey(
    { name: 'RSASSA-PKCS1-v1_5', modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256' },
    true,
    ['sign', 'verify'],
  )) as CryptoKeyPair
  const jwk = await crypto.subtle.exportKey('jwk', pair.publicKey)
  const certs = (async () => new Response(JSON.stringify({ keys: [{ kid, kty: 'RSA', alg: 'RS256', use: 'sig', e: jwk.e, n: jwk.n }] }))) as typeof fetch
  const sign = async (email: string) => {
    const body = `${enc({ alg: 'RS256', kid })}.${enc({ aud: [ACCESS_AUD], iss: ACCESS_TEAM, exp: NOW / 1000 + 3600, email })}`
    const sig = await crypto.subtle.sign('RSASSA-PKCS1-v1_5', pair.privateKey, new TextEncoder().encode(body))
    return `${body}.${Buffer.from(sig).toString('base64url')}`
  }
  return { sign, certs }
}
const NOW = Date.UTC(2026, 9, 22, 8, 0, 0)
const req = (token?: string) => new Request('https://twquant.pages.dev/api/picks', { headers: token ? { [JWT_HEADER]: token } : {} })

test('T-430 AC1 角色：admin（目前 cktalex@gmail.com）才能用每日名單與績效；前端認得的功能與後端相同', () => {
  assert.deepEqual(ROLE_MEMBERS.admin, ['cktalex@gmail.com'])
  assert.deepEqual(FEATURE_ROLES.picks, ['admin'])
  assert.deepEqual(FEATURE_ROLES.score, ['admin'])
  for (const f of ['picks', 'score'] as const) {
    assert.ok(can('CKTalex@gmail.com', f), f)
    assert.ok(!can('friend@example.com', f), f)
    assert.ok(!can(null, f), f)
  }
  assert.deepEqual(featuresOf('friend@example.com'), [])
  assert.deepEqual([...FEATURES].sort(), Object.keys(FEATURE_ROLES).sort())
})

test('T-430 AC2 擋人：沒權杖 401、不是 admin 403、admin 放行；只看驗過的權杖', async () => {
  const s = await signer('kid-t429-gate')
  const opts = { fetchImpl: s.certs, now: NOW }
  assert.equal(await denied(req(await s.sign('cktalex@gmail.com')), 'picks', {}, opts), null)
  assert.equal(await denied(req(await s.sign('cktalex@gmail.com')), 'score', {}, opts), null)
  const other = await denied(req(await s.sign('friend@example.com')), 'picks', {}, opts)
  assert.equal(other?.status, 403)
  assert.deepEqual(await other?.json(), { detail: '沒有權限' })
  assert.equal((await denied(req(), 'score', {}, opts))?.status, 401)
  const spoof = new Request('https://twquant.pages.dev/api/picks', { headers: { 'cf-access-authenticated-user-email': 'cktalex@gmail.com' } })
  assert.equal((await denied(spoof, 'picks', {}, opts))?.status, 401) // 冒用 email 標頭沒用
  // 首頁、個股評分：不擋，只決定多不多那幾欄；兩種內容暫存鍵不同
  assert.equal(await allowed(req(await s.sign('cktalex@gmail.com')), 'picks', {}, opts), true)
  assert.equal(await allowed(req(await s.sign('friend@example.com')), 'picks', {}, opts), false)
  assert.equal(await allowed(req(), 'picks', {}, opts), false)
  assert.notEqual(gatedKey('/api/warroom', true), gatedKey('/api/warroom', false))
})

test('T-430 AC2 每支名單／績效端點都自己擋，而且在讀暫存之前擋', () => {
  const gated: [string, string][] = [
    ['functions/api/picks/index.ts', 'picks'],
    ['functions/api/picks/dates.ts', 'picks'],
    ['functions/api/stocks/[sid]/picks.ts', 'picks'],
    ['functions/api/scorecard/index.ts', 'score'],
    ['functions/api/scorecard/trades.ts', 'score'],
  ]
  for (const [f, feature] of gated) {
    const src = read(f)
    assert.match(src, new RegExp(`\\(await denied\\((ctx\\.)?request, '${feature}', (ctx\\.)?env\\)\\) \\?\\? cached\\(cacheKey\\(`), f)
    assert.equal(src.match(/cached\(/g)?.length, 1, f) // 沒有別條路繞過權限直接讀暫存
  }
  // 公開的日期清單照舊只給日期（不含任何股票、不含檔數）
  assert.match(read('functions/pub/dates.ts'), /listDates\(request, env, '資料暫時無法取得'\)/)
  assert.match(read('functions/api/picks/dates.ts'), /if \(publicDetail !== undefined \|\| dates\.length === 0\) return json\(\{ dates \}\)/)
})

test('T-430 AC3 首頁與個股評分：沒有名單權限就不讀名單（名單速覽、「名單」標記、在哪條線上）', () => {
  const war = read('functions/api/warroom/index.ts')
  assert.match(war, /const full = await allowed\(ctx\.request, 'picks', ctx\.env\)/)
  assert.match(war, /const pickDates = withPicks\s*\? await readRows/)
  assert.match(war, /handle\(ctx\.env, full\)/)
  const scores = read('functions/api/stocks/[sid]/scores.ts')
  assert.match(scores, /const full = await allowed\(ctx\.request, 'picks', ctx\.env\)/)
  assert.match(scores, /const lists = withLists\s*\? await db/)
  assert.match(scores, /handle\(String\(ctx\.params\.sid\), ctx\.env, full\)/)
})

test('T-430 AC4 前端：沒權限沒有分頁與首頁卡，開到了只寫一句話', () => {
  assert.ok(tabAllowed('home', []) && tabAllowed('news', []) && tabAllowed('watch', []))
  for (const t of ['picks', 'score', 'line'] as const) assert.ok(!tabAllowed(t, []), t)
  assert.ok(tabAllowed('picks', ['picks']) && tabAllowed('score', ['score']) && tabAllowed('line', ['line']))
  assert.ok(!tabAllowed('score', ['picks']))
  assert.equal(lockedText('picks', false), '這個帳號沒有「每日名單」的權限')
  assert.equal(lockedText('score', false), '這個帳號沒有「績效」的權限')
  assert.equal(lockedText('line', false), '這個帳號沒有「LINE 群組」的權限')
  assert.equal(lockedText('picks', true), '登入後才看得到')

  const base = defaultLayout()
  const mine = visibleLayout({ order: base.order, collapsed: [...base.collapsed, 'picks'] }, [])
  assert.ok(!mine.order.includes('picks') && !mine.order.includes('score') && !mine.collapsed.includes('picks'))
  assert.deepEqual(mine.order, base.order.filter((id) => id !== 'picks' && id !== 'score')) // 其他卡順序不動
  assert.deepEqual(visibleLayout(base, ['picks', 'score']), base)
  assert.deepEqual(visibleLayout(base, ['picks']).order, base.order.filter((id) => id !== 'score'))
})

test('T-430 AC4 前端：分頁、名單、日期、K 線推薦日、名單成績都要權限才顯示或去抓', () => {
  const app = read('src/cloud/CloudApp.tsx')
  assert.match(app, /\{canPicks && \(\s*<button className=\{tab === 'picks'/)
  assert.match(app, /\{features\.includes\('score'\) && \(\s*<button className=\{tab === 'score'/)
  assert.match(app, /if \(guest \|\| !canPicks \|\| \(tab !== 'picks' && tab !== 'group'\)\) return/) // T-431：盤勢分頁標「名單」要用
  assert.match(app, /if \(guest \|\| !canPicks\) return\s*let stale = false\s*fetchPickDates\(\)/)
  assert.equal(app.match(/fetchPickDates\(\)/g)?.length, 1)
  assert.match(app, /showPicks \? fetchStockPicks\(sid\) : Promise\.resolve/)
  assert.match(app, /!tabAllowed\(tab, features\) \?/)
  const war = read('src/cloud/WarRoom.tsx')
  assert.match(war, /visibleLayout\(saved, features\)/)
  assert.match(war, /if \(!canScore\) return\s*let stale = false\s*for \(const code of SCORE_LINES\)/)
  assert.equal(war.match(/fetchScorecard\(/g)?.length, 1)
  assert.match(war, /withList=\{features\.includes\('picks'\)\}/)
})
