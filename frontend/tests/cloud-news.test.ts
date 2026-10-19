import assert from 'node:assert/strict'
import test from 'node:test'
import { ALLOWED, proxy, upstreamUrl } from '../functions/_lib/news-proxy.ts'
import { ageLabel, appendPage, dayTitle, groupByDay, healthLine, nextUntil, shownTime, taipeiHm, timeLabel, type NewsHealth, type NewsRow } from '../src/cloud/news.ts'
import { tabFromUrl } from '../src/cloud/tabs.ts'

const BASE = 'https://abc.supabase.co/functions/v1/api/'
const env = { NEWS_API_URL: BASE, NEWS_API_KEY: 'k3y' }

test('T-400：轉給新聞服務只帶准許的參數（照字母排）；服務金鑰放標頭', async () => {
  assert.equal(
    upstreamUrl(BASE, '/v1/items', 'https://twquant.pages.dev/api/news/items?limit=60&evil=1&kind=intl&until=2026-10-19T00:00:00Z'),
    'https://abc.supabase.co/functions/v1/api/v1/items?kind=intl&limit=60&until=2026-10-19T00%3A00%3A00Z',
  )
  assert.equal(upstreamUrl(BASE, '/v1/health', 'https://x/api/news/health?limit=1'), 'https://abc.supabase.co/functions/v1/api/v1/health')
  assert.deepEqual(ALLOWED['/v1/health'], [])
  let seen: { url: string; key: string | null } | null = null
  const fake = (async (url: string, init?: RequestInit) => {
    seen = { url, key: new Headers(init?.headers).get('x-news-key') }
    return new Response('{"items":[]}', { status: 200, headers: { 'content-type': 'application/json' } })
  }) as typeof fetch
  const res = await proxy(env, '/v1/items', 'https://x/api/news/items?kind=tw', fake)
  assert.equal(res.status, 200)
  assert.equal(await res.text(), '{"items":[]}')
  assert.deepEqual(seen, { url: 'https://abc.supabase.co/functions/v1/api/v1/items?kind=tw', key: 'k3y' })
})

test('T-400：新聞服務沒設定、連不上、壞掉、回的不是 JSON → 503 不外洩上游；參數錯（400）照轉', async () => {
  const json = (status: number, body = '{"detail":"x"}') =>
    (async () => new Response(body, { status, headers: { 'content-type': 'application/json' } })) as unknown as typeof fetch
  const detail = async (r: Response) => ((await r.json()) as { detail: string }).detail
  const r0 = await proxy({}, '/v1/items', 'https://x/', json(200))
  assert.equal(r0.status, 503)
  assert.equal(await detail(r0), '新聞服務還沒設定')
  const r1 = await proxy(env, '/v1/items', 'https://x/', (async () => {
    throw new Error('ECONNRESET abc.supabase.co')
  }) as typeof fetch)
  assert.equal(r1.status, 503)
  assert.ok(!(await r1.text()).includes('supabase'))
  assert.equal((await proxy(env, '/v1/items', 'https://x/', json(500))).status, 503)
  assert.equal((await proxy(env, '/v1/items', 'https://x/', json(403))).status, 503, '金鑰錯也不轉 403（會被網站當成要登入）')
  const html = (async () => new Response('<html>', { status: 200, headers: { 'content-type': 'text/html' } })) as unknown as typeof fetch
  assert.equal((await proxy(env, '/v1/items', 'https://x/', html)).status, 503)
  const bad = await proxy(env, '/v1/items', 'https://x/', json(400, '{"detail":"limit 要是 1～200：999"}'))
  assert.equal(bad.status, 400)
  assert.equal(await detail(bad), 'limit 要是 1～200：999')
})

const row = (id: string, first: string, extra: Partial<NewsRow> = {}): NewsRow => ({
  id,
  source: 'cnbc-top',
  source_name: 'CNBC',
  kind: 'intl',
  lang: 'en',
  title: `T ${id}`,
  summary: null,
  url: 'https://x',
  published_at: null,
  first_seen_at: first,
  versions: 0,
  ...extra,
})

test('T-400：新聞流依抓到那天（台北）分組；今天／昨天／日期；時間顯示來源的發布時間，沒有才用抓到的', () => {
  const rows = [row('a', '2026-10-19T02:00:00Z'), row('b', '2026-10-18T17:30:00Z'), row('c', '2026-10-18T15:00:00Z'), row('d', '2026-10-17T01:00:00Z')]
  assert.deepEqual(
    groupByDay(rows).map((g) => [g.day, g.rows.map((r) => r.id)]),
    [
      ['2026-10-19', ['a', 'b']], // 10/18 17:30 UTC＝台北 10/19 01:30
      ['2026-10-18', ['c']],
      ['2026-10-17', ['d']],
    ],
  )
  assert.deepEqual(['2026-10-19', '2026-10-18', '2026-10-16'].map((d) => dayTitle(d, '2026-10-19')), ['今天', '昨天', '10/16（五）'])
  assert.equal(taipeiHm('2026-10-18T17:30:00Z'), '01:30')
  assert.equal(shownTime(row('x', '2026-10-19T02:00:00Z', { published_at: '2026-10-19T01:00:00Z' })), '2026-10-19T01:00:00Z')
  assert.equal(shownTime(row('x', '2026-10-19T02:00:00Z')), '2026-10-19T02:00:00Z')
  assert.equal(timeLabel(row('x', '2026-10-19T02:00:00Z', { published_at: '2026-10-19T01:00:00Z' }), '2026-10-19'), '09:00')
  assert.equal(timeLabel(row('x', '2026-10-19T02:00:00Z', { published_at: '2026-10-18T12:00:00Z' }), '2026-10-19'), '10/18 20:00', '昨晚發、今天才抓到')
})

test('T-400：最後抓取時間的說法；有來源壞掉、停擺、從沒抓到', () => {
  const now = new Date('2026-10-19T06:00:00Z')
  assert.equal(ageLabel('2026-10-19T05:59:40Z', now), '剛剛')
  assert.equal(ageLabel('2026-10-19T05:48:00Z', now), '12 分鐘前')
  assert.equal(ageLabel('2026-10-19T03:00:00Z', now), '3 小時前')
  assert.equal(ageLabel('2026-10-18T14:05:00Z', now), '15 小時前')
  assert.equal(ageLabel('2026-10-17T14:05:00Z', now), '10/17 22:05')
  const h = (over: Partial<NewsHealth>): NewsHealth => ({ now: now.toISOString(), last_ok_at: '2026-10-19T05:48:00Z', stale: false, sources: [], ...over })
  assert.deepEqual(healthLine(h({}), now), { text: '最後抓取 12 分鐘前', tone: 'ok' })
  assert.deepEqual(healthLine(h({ sources: [{ source: 'ft', name: 'Financial Times', failing: true, last_error: 'HTTP 403' }] }), now), {
    text: '最後抓取 12 分鐘前・Financial Times 暫停',
    tone: 'warn',
  })
  assert.equal(healthLine(h({ stale: true }), now).tone, 'bad')
  assert.equal(healthLine(h({ last_ok_at: null }), now).text, '還沒抓到任何新聞')
  assert.equal(healthLine(null, now).tone, 'bad')
})

test('T-400：「更多」從最舊那則的時間（含）接著抓，重複的只留一次', () => {
  const page1 = [row('a', '2026-10-19T02:00:00Z'), row('b', '2026-10-19T01:00:00Z')]
  assert.equal(nextUntil(page1), '2026-10-19T01:00:00Z')
  assert.equal(nextUntil([]), null)
  const merged = appendPage(page1, [row('b', '2026-10-19T01:00:00Z'), row('c', '2026-10-19T01:00:00Z')])
  assert.deepEqual(merged.map((r) => r.id), ['a', 'b', 'c'])
})

test('T-400：網址 ?tab=news 打開新聞分頁', () => {
  assert.equal(tabFromUrl('https://twquant.pages.dev/?tab=news'), 'news')
})
