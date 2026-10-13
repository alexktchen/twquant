import assert from 'node:assert/strict'
import test from 'node:test'
import { ACCESS_AUD, ACCESS_TEAM, JWT_HEADER, accessEmail } from '../functions/_lib/identity.ts'
import { MODELS, modelScores, searchPattern, type ModelDay, type ScoreRow } from '../functions/_lib/scores.ts'
import { WATCH_MAX, WATCHLIST_DDL, latestDate, parseOrder, sameOrigin, watchItems } from '../functions/_lib/watchlist.ts'

// ── 身分：Cloudflare Access 簽的權杖 ───────────────────────────────────────────
const enc = (o: unknown) => Buffer.from(JSON.stringify(o)).toString('base64url')

async function signer(kid: string) {
  const pair = (await crypto.subtle.generateKey(
    { name: 'RSASSA-PKCS1-v1_5', modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256' },
    true,
    ['sign', 'verify'],
  )) as CryptoKeyPair
  const jwk = await crypto.subtle.exportKey('jwk', pair.publicKey)
  let calls = 0
  const certs = (async () => {
    calls += 1
    return new Response(JSON.stringify({ keys: [{ kid, kty: 'RSA', alg: 'RS256', use: 'sig', e: jwk.e, n: jwk.n }] }))
  }) as typeof fetch
  const sign = async (claims: Record<string, unknown>, header: Record<string, unknown> = { alg: 'RS256', kid }) => {
    const body = `${enc(header)}.${enc(claims)}`
    const sig = await crypto.subtle.sign('RSASSA-PKCS1-v1_5', pair.privateKey, new TextEncoder().encode(body))
    return `${body}.${Buffer.from(sig).toString('base64url')}`
  }
  return { sign, certs, calls: () => calls }
}

const NOW = Date.UTC(2026, 9, 13, 8, 0, 0)
const good = { aud: [ACCESS_AUD], iss: ACCESS_TEAM, exp: NOW / 1000 + 3600, nbf: NOW / 1000 - 60, email: 'Someone@Example.com' }
const req = (token?: string) => new Request('https://twquant.pages.dev/api/watchlist', { headers: token ? { [JWT_HEADER]: token } : {} })

test('T-345 AC1: 只認 Cloudflare 簽過、給本站、還沒過期的權杖，email 轉小寫', async () => {
  const s = await signer('kid-ok')
  const ask = async (claims: Record<string, unknown>, header?: Record<string, unknown>) =>
    accessEmail(req(await s.sign(claims, header)), { fetchImpl: s.certs, now: NOW })
  assert.equal(await ask(good), 'someone@example.com')
  assert.equal(await ask({ ...good, aud: ['another-app'] }), null)
  assert.equal(await ask({ ...good, iss: 'https://evil.cloudflareaccess.com' }), null)
  assert.equal(await ask({ ...good, exp: NOW / 1000 - 1 }), null)
  assert.equal(await ask({ ...good, nbf: NOW / 1000 + 600 }), null)
  assert.equal(await ask({ ...good, email: undefined }), null)
  assert.equal(await ask(good, { alg: 'none', kid: 'kid-ok' }), null)
  // 公鑰抓一次就快取
  assert.equal(s.calls(), 1)
})

test('T-345 AC1: 偽造簽章、沒有權杖、亂碼都不給身分；冒用 email 標頭沒用', async () => {
  const s = await signer('kid-forge')
  const other = await signer('kid-forge') // 同 kid、不同私鑰＝偽造
  const forged = await other.sign(good)
  assert.equal(await accessEmail(req(forged), { fetchImpl: s.certs, now: NOW }), null)
  assert.equal(await accessEmail(req(), { fetchImpl: s.certs, now: NOW }), null)
  assert.equal(await accessEmail(req('a.b.c'), { fetchImpl: s.certs, now: NOW }), null)
  const spoof = new Request('https://twquant.pages.dev/api/watchlist', {
    headers: { 'cf-access-authenticated-user-email': 'victim@example.com' },
  })
  assert.equal(await accessEmail(spoof, { fetchImpl: s.certs, now: NOW }), null)
})

test('T-345 AC1: 金鑰輪換——不認得的 kid 重抓公鑰；公鑰抓不到是伺服器錯誤（丟錯，不當成沒登入）', async () => {
  const s = await signer('kid-rotated')
  assert.equal(await accessEmail(req(await s.sign(good)), { fetchImpl: s.certs, now: NOW }), 'someone@example.com')
  const down = (async () => new Response('', { status: 500 })) as typeof fetch
  const t = await signer('kid-unreachable')
  await assert.rejects(accessEmail(req(await t.sign(good)), { fetchImpl: down, now: NOW }), /Access 公鑰/)
})

// ── 模型分數 ────────────────────────────────────────────────────────────────
const [RANK, QUIET, RISK] = MODELS.map((m) => m.model_id)
const day = (model_id: string, date: string, total: number, avg: number | null = 0.1): ModelDay => ({ model_id, date, total, avg })
const row = (model_id: string, date: string, stock_id: string, score: number, rank: number, total: number): ScoreRow => ({
  model_id,
  date,
  stock_id,
  score,
  rank_pct: 1 - (rank - 1) / total,
  rank,
  total,
})

test('T-345 AC2: 三個模型依固定順序；最新一天的分數＋舊到新的歷史', () => {
  const days = [day(RANK, '2026-10-08', 1950), day(RANK, '2026-10-12', 1952), day(QUIET, '2026-10-12', 241, 0.09), day(RISK, '2026-10-12', 1956)]
  const rows = [
    row(RANK, '2026-10-12', '2330', 0.08, 12, 1952),
    row(RANK, '2026-10-08', '2330', 0.05, 40, 1950),
    row(RANK, '2026-10-12', '2317', 0.01, 900, 1952), // 別檔不能混進來
    row(RISK, '2026-10-12', '2330', 0.7, 100, 1956),
  ]
  const out = modelScores('2330', rows, days)
  assert.deepEqual(out.map((m) => m.code), ['rank', 'quiet', 'risk'])
  assert.equal(out[0].date, '2026-10-12')
  assert.equal(out[0].rank, 12)
  assert.equal(out[0].total, 1952)
  assert.deepEqual(out[0].history.map((h) => h.date), ['2026-10-08', '2026-10-12'])
  // 不在潛伏池：分數是 null，但池子大小與平均照樣給（畫面寫「潛伏池 241 檔」）
  assert.equal(out[1].score, null)
  assert.equal(out[1].rank, null)
  assert.equal(out[1].total, 241)
  assert.equal(out[1].avg, 0.09)
})

test('T-345 AC2: 某模型最新一天沒評到這檔就是 null，不拿前幾天的分數充當今天', () => {
  const days = [day(RANK, '2026-10-08', 1950), day(RANK, '2026-10-12', 1952)]
  const rows = [row(RANK, '2026-10-08', '2330', 0.05, 40, 1950)]
  const [rank, quiet] = modelScores('2330', rows, days)
  assert.equal(rank.date, '2026-10-12')
  assert.equal(rank.score, null)
  assert.equal(rank.history.length, 1)
  // 雲端完全沒有這個模型的分數：日期也是 null
  assert.equal(quiet.date, null)
  assert.equal(quiet.total, null)
})

test('T-345 AC3: 搜尋字串去空白、限長度、跳脫 LIKE 萬用字元', () => {
  assert.deepEqual(searchPattern(' 2330 '), { text: '2330', like: '2330' })
  assert.deepEqual(searchPattern('台積'), { text: '台積', like: '台積' })
  assert.deepEqual(searchPattern('a%_\\'), { text: 'a%_\\', like: 'a\\%\\_\\\\' })
  assert.equal(searchPattern(''), null)
  assert.equal(searchPattern('   '), null)
  assert.equal(searchPattern(null), null)
  assert.equal(searchPattern('x'.repeat(21)), null)
})

// ── 追蹤股清單 ──────────────────────────────────────────────────────────────
test('T-345 AC4: 清單每列的收盤、漲跌（缺前收不補 0）與最新分數，不帶歷史', () => {
  const list = [
    { sid: '2330', name: '台積電', added_at: '2026-10-13 08:00:00+00' },
    { sid: '6789', name: null, added_at: '2026-10-12 08:00:00+00' },
  ]
  const px = [
    { sid: '2330', date: '2026-10-09', close: 1000 },
    { sid: '2330', date: '2026-10-12', close: 1020 },
    { sid: '6789', date: '2026-10-12', close: 50 },
  ]
  const days = [day(RANK, '2026-10-08', 1950), day(RANK, '2026-10-12', 1952)]
  const rows = [row(RANK, '2026-10-12', '2330', 0.08, 12, 1952), row(RANK, '2026-10-08', '2330', 0.05, 40, 1950)]
  const [a, b] = watchItems(list, px, { rows, days })
  assert.equal(a.date, '2026-10-12')
  assert.equal(a.close, 1020)
  assert.equal(a.chg, 1020 / 1000 - 1)
  assert.equal(a.models[0].rank, 12)
  assert.deepEqual(a.models[0].history, [])
  assert.equal(b.chg, null)
  assert.equal(b.name, null)
  assert.equal(b.models[0].score, null)
  assert.equal(latestDate(days), '2026-10-12')
  assert.equal(latestDate([]), null)
})

test('T-345 AC5: 寫入只收本站送出的請求；表在 web schema（pipeline 的修剪碰不到）', () => {
  const put = (origin?: string) =>
    new Request('https://twquant.pages.dev/api/watchlist/2330', { method: 'PUT', headers: origin ? { origin } : {} })
  assert.equal(sameOrigin(put('https://twquant.pages.dev')), true)
  assert.equal(sameOrigin(put('https://evil.example')), false)
  assert.equal(sameOrigin(put()), true)
  assert.ok(WATCHLIST_DDL.join('\n').includes('web.watchlist'))
  assert.ok(!WATCHLIST_DDL.join('\n').includes('serving.'))
  assert.ok(WATCH_MAX >= 20)
})

test('T-346 AC1: 存順序只收 1～上限個、不重複、格式正確的代號', () => {
  assert.deepEqual(parseOrder({ order: ['5340', '2330', '2887G'] }), ['5340', '2330', '2887G'])
  assert.equal(parseOrder({ order: [] }), null)
  assert.equal(parseOrder({ order: ['2330', '2330'] }), null)
  assert.equal(parseOrder({ order: ['2330', "x';--"] }), null)
  assert.equal(parseOrder({ order: ['2330', 2317] }), null)
  assert.equal(parseOrder({ order: '2330' }), null)
  assert.equal(parseOrder(null), null)
  assert.equal(parseOrder({ order: Array.from({ length: WATCH_MAX + 1 }, (_, i) => String(1000 + i)) }), null)
})

test('T-346 AC2: 舊表升級補 position 欄，照原本的顯示順序（新加的在前）編號，只補還沒編號的', () => {
  const ddl = WATCHLIST_DDL.join(' ')
  assert.match(ddl, /ADD COLUMN IF NOT EXISTS position/)
  assert.match(ddl, /ORDER BY added_at DESC, stock_id/)
  assert.match(ddl, /position IS NULL/)
})
