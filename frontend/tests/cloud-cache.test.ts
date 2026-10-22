import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { CACHE_MAX_KEYS, CACHE_TTL_MS, cacheKey, cached, clearCache } from '../functions/_lib/cache.ts'

const ok = (body: string) => async () =>
  new Response(body, { status: 200, headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'private, no-store' } })

test('暫存：60 秒內同一個網址不再產生（不連資料庫），回應內容與標頭相同', async () => {
  clearCache()
  let calls = 0
  const make = async () => {
    calls += 1
    return ok(`{"n":${calls}}`)()
  }
  const a = await cached('/api/picks', make, 1_000)
  const b = await cached('/api/picks', make, 1_000 + CACHE_TTL_MS - 1)
  assert.equal(calls, 1)
  assert.equal(await a.text(), '{"n":1}')
  assert.equal(await b.text(), '{"n":1}')
  assert.equal(b.headers.get('cache-control'), 'private, no-store') // 瀏覽器端照樣不存（登出後不能拿到舊名單）
  const c = await cached('/api/picks', make, 1_000 + CACHE_TTL_MS)
  assert.equal(await c.text(), '{"n":2}')
  assert.equal(calls, 2)
})

test('暫存：錯誤（503、404）不存，下一次照樣重試；不同查詢字串分開存', async () => {
  clearCache()
  let calls = 0
  const flaky = async () => {
    calls += 1
    return calls === 1 ? new Response('{"detail":"x"}', { status: 503 }) : ok('{"ok":1}')()
  }
  assert.equal((await cached('/pub/dates', flaky, 0)).status, 503)
  assert.equal((await cached('/pub/dates', flaky, 1)).status, 200)
  assert.equal(calls, 2)
  await cached('/api/stocks/2330/ohlc?days=500', ok('A'), 0)
  assert.equal(await (await cached('/api/stocks/2330/ohlc?days=60', ok('B'), 0)).text(), 'B')
  assert.equal(cacheKey(new Request('https://twquant.pages.dev/api/stocks/search?q=23')), '/api/stocks/search?q=23')
})

test('暫存：最多存固定數量，滿了先清過期的；存不下就照常回應不存', async () => {
  clearCache()
  for (let i = 0; i < CACHE_MAX_KEYS; i++) await cached(`/k${i}`, ok('x'), 0)
  let calls = 0
  const make = async () => {
    calls += 1
    return ok('y')()
  }
  // 都還沒過期：存不下，但回應照常
  assert.equal(await (await cached('/new', make, 10)).text(), 'y')
  assert.equal(await (await cached('/new', make, 11)).text(), 'y')
  assert.equal(calls, 2)
  // 全部過期：清掉後存得下
  await cached('/new', make, CACHE_TTL_MS + 5)
  await cached('/new', make, CACHE_TTL_MS + 6)
  assert.equal(calls, 3)
})

test('暫存只用在每個人都一樣的資料：追蹤股、登入身分、登出、即時報價的端點不准包', () => {
  const read = (p: string) => readFileSync(new URL(`../functions/${p}`, import.meta.url), 'utf8')
  for (const p of ['api/watchlist/index.ts', 'api/watchlist/[sid].ts', 'api/me.ts', 'api/logout.ts', 'api/login.ts', 'api/quotes.ts']) {
    assert.ok(!read(p).includes('_lib/cache'), `${p} 不應該用共用暫存`)
  }
  for (const p of ['api/picks/index.ts', 'api/picks/dates.ts', 'pub/dates.ts', 'api/stocks/[sid]/ohlc.ts', 'api/stocks/[sid]/picks.ts', 'api/stocks/search.ts']) {
    assert.ok(read(p).includes('cached(cacheKey('), `${p} 應該用共用暫存`)
  }
  // T-429：有名單權限的人多幾欄 → 依權限分開暫存（鍵不一樣），不能共用同一份
  for (const p of ['api/stocks/[sid]/scores.ts', 'api/warroom/index.ts']) {
    assert.ok(read(p).includes('cached(gatedKey(cacheKey(ctx.request), full)'), `${p} 應該依權限分開暫存`)
  }
})
