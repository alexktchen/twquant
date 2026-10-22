import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import test from 'node:test'
import { PUBLIC_MAX_AGE_S, pubProxy } from '../functions/_lib/news-proxy.ts'
import { guestTab } from '../src/cloud/tabs.ts'

// T-434／D-210：新聞頁公開。只讀的新聞端點搬到 /pub/news/*（Cloudflare Access 不保護 /pub），
// 沒登入也看得到；核可（擁有者）與語意搜尋（花 AI 額度）留在 /api/news/* 要登入。
const read = (p: string): string => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8')
const env = { NEWS_API_URL: 'https://abc.supabase.co/functions/v1/api/', NEWS_API_KEY: 'k3y' }

const PUBLIC = ['items', 'graph', 'stock', 'stock-counts', 'health', 'thread', 'similar', 'summary', 'galaxy', 'stock-days', 'digest']
const PRIVATE = ['review', 'search']

test('T-434 AC1 端點位置：只讀的在 functions/pub/news/，核可與搜尋留在 functions/api/news/', () => {
  for (const name of PUBLIC) {
    assert.ok(existsSync(new URL(`../functions/pub/news/${name}.ts`, import.meta.url)), `pub/news/${name}.ts`)
    assert.ok(!existsSync(new URL(`../functions/api/news/${name}.ts`, import.meta.url)), `api/news/${name}.ts 不該還在`)
  }
  for (const name of PRIVATE) {
    assert.ok(existsSync(new URL(`../functions/api/news/${name}.ts`, import.meta.url)), `api/news/${name}.ts`)
    assert.ok(!existsSync(new URL(`../functions/pub/news/${name}.ts`, import.meta.url)), `pub/news/${name}.ts 不該公開`)
  }
})

test('T-434 AC2 公開端點：同一執行個體暫存＋讓瀏覽器快取 60 秒；錯誤不快取', async () => {
  for (const name of PUBLIC) {
    const src = read(`functions/pub/news/${name}.ts`)
    assert.ok(src.includes('cached(cacheKey('), `pub/news/${name}.ts 要用共用暫存`)
    assert.ok(src.includes('pubProxy('), `pub/news/${name}.ts 要用 pubProxy（帶公開快取標頭）`)
    assert.ok(!src.includes('accessEmail'), `pub/news/${name}.ts 不該看身分`)
  }
  const ok = (async () => new Response('{"items":[]}', { status: 200, headers: { 'content-type': 'application/json' } })) as unknown as typeof fetch
  const res = await pubProxy(env, '/v1/items', 'https://x/pub/news/items', ok)
  assert.equal(res.status, 200)
  assert.equal(res.headers.get('cache-control'), `public, max-age=${PUBLIC_MAX_AGE_S}`)
  assert.equal(await res.text(), '{"items":[]}')
  const bad = (async () => new Response('{"detail":"x"}', { status: 500, headers: { 'content-type': 'application/json' } })) as unknown as typeof fetch
  const err = await pubProxy(env, '/v1/items', 'https://x/pub/news/items', bad)
  assert.equal(err.status, 503)
  assert.equal(err.headers.get('cache-control'), 'private, no-store')
  const miss = await pubProxy({}, '/v1/items', 'https://x/pub/news/items', ok)
  assert.equal(miss.status, 503)
  assert.equal(miss.headers.get('cache-control'), 'private, no-store')
})

test('T-434 AC3 網站的新聞請求：只讀的打 /pub/news/，核可與搜尋打 /api/news/；/pub 讓瀏覽器照快取標頭', () => {
  const api = read('src/cloud/api.ts')
  const paths = [...api.matchAll(/['`](\/(?:api|pub)\/news\/[a-z-]+)/g)].map((m) => m[1])
  const pub = new Set(paths.filter((p) => p.startsWith('/pub/')).map((p) => p.slice('/pub/news/'.length)))
  const priv = new Set(paths.filter((p) => p.startsWith('/api/')).map((p) => p.slice('/api/news/'.length)))
  assert.deepEqual([...pub].sort(), [...PUBLIC].sort())
  assert.deepEqual([...priv].sort(), [...PRIVATE].sort())
  // 其他端點一律 no-store（登出後不能留在快取裡）；/pub 的資料每個人都一樣，才讓瀏覽器照 max-age 留 60 秒
  assert.match(api, /cache: path\.startsWith\('\/pub\/'\) \? 'default' : 'no-store'/)
})

test('T-434 AC4 沒登入：新聞分頁不蓋登入卡片；沒指定分頁的訪客直接落在新聞', () => {
  const app = read('src/cloud/CloudApp.tsx')
  assert.ok(!app.includes('登入後看新聞'), '新聞分頁的登入卡片要拿掉')
  assert.match(app, /<NewsPage\s[^>]*guest=\{guest\}/s)
  assert.equal(guestTab('home', null), 'news')
  assert.equal(guestTab('home', 'home'), 'home', '網址明寫 ?tab=home 就照它')
  assert.equal(guestTab('group', null), 'group', '已經在別的分頁不動')
  assert.equal(guestTab('news', null), 'news')
})

test('T-434 AC5 沒登入的新聞頁：不去抓核可清單、搜尋停用並說明要登入', () => {
  const page = read('src/cloud/NewsPage.tsx')
  assert.match(page, /guest: boolean/)
  assert.match(page, /if \(guest\) return\s*\n\s*fetchNewsReview\(\)/, '沒登入不抓核可清單（會被當成登入過期）')
  assert.match(page, /disabled=\{guest\}/)
  assert.match(page, /登入後才能搜尋/)
})

test('T-434 AC6 公開頁與搜尋引擎：介紹頁說新聞不用登入；首頁有 description；登入頁不再說新聞要登入', () => {
  assert.match(read('public/about.html'), /新聞.{0,12}不用登入/)
  assert.match(read('index.html'), /<meta name="description" content="[^"]{20,}"/)
  const login = read('src/cloud/LoginPage.tsx')
  assert.ok(!/<b>新聞<\/b>/.test(login), '新聞已公開，登入頁不該把它列成登入後才有的功能')
  assert.match(login, /新聞搜尋/)
})
