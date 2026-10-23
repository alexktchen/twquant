// T-446／D-217：星雲圖「大家都在搜」。
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { TREND_BODY_MAX, TREND_DAYS, TREND_DDL, TREND_MAX_HUBS, TREND_PURGE_DAYS, parseSearch } from '../functions/_lib/search-trend.ts'
import { TREND_SHOW, trendHubs, type GalaxyData } from '../src/cloud/news-galaxy.ts'

const ID = '0123456789abcdef0123456789abcdef'

test('T-446 AC1 搜尋紀錄內容：{ id, hubs: 1～2 個亮點 id }，重複只留一個；中文族群名可以；打的字、多欄位、怪字元都不收', () => {
  assert.deepEqual(parsePresenceLike({ id: ID, hubs: ['tw:2330'] }), { id: ID, hubs: ['tw:2330'] })
  assert.deepEqual(parsePresenceLike({ id: ID, hubs: ['tw:2330', 'us:NVDA'] }), { id: ID, hubs: ['tw:2330', 'us:NVDA'] })
  assert.deepEqual(parsePresenceLike({ id: ID, hubs: ['grp:散熱', 'grp:散熱'] }), { id: ID, hubs: ['grp:散熱'] })
  assert.equal(TREND_MAX_HUBS, 2)
  for (const bad of [
    { id: ID, hubs: [] },
    { id: ID, hubs: ['tw:2330', 'us:NVDA', 'th:ai'] },
    { id: ID, hubs: ['台積電'] },
    { id: ID, hubs: ['TW:2330'] },
    { id: ID, hubs: ['tw:23 30'] },
    { id: ID, hubs: ['tw:<script>'] },
    { id: ID, hubs: ["tw:a'b"] },
    { id: ID, hubs: ['tw:' + 'x'.repeat(41)] },
    { id: ID, hubs: [2330] },
    { id: ID, hubs: 'tw:2330' },
    { id: ID, hubs: ['tw:2330'], q: '台積' },
    { id: 'nope', hubs: ['tw:2330'] },
    { hubs: ['tw:2330'] },
  ])
    assert.equal(parseSearch(JSON.stringify(bad)), null, JSON.stringify(bad))
  assert.equal(parseSearch('{'), null)
  assert.equal(parseSearch(JSON.stringify({ id: ID, hubs: ['tw:1'], pad: 'x'.repeat(TREND_BODY_MAX) })), null)
})

const parsePresenceLike = (v: unknown) => parseSearch(JSON.stringify(v))

const hub = (id: string, label: string, type = 'tw') => ({ id, label, type, sub: null, n: 5, x: 0, y: 0, z: 0 })
const data = { built: '', days: 7, links: 0, clusters: [], items: [], hubs: [hub('tw:2330', '台積電'), hub('us:NVDA', '輝達', 'us'), hub('grp:散熱', '散熱', 'group')] } as GalaxyData

test('T-446 AC2 只列目前星雲圖裡有的亮點、照伺服器的順序、最多 TREND_SHOW 個', () => {
  const got = trendHubs(data, [
    { hub: 'us:NVDA', n: 9 },
    { hub: 'tw:9999', n: 8 }, // 亂送的、或已經不在快照裡
    { hub: 'grp:散熱', n: 3 },
    { hub: 'tw:2330', n: 2 },
  ])
  assert.deepEqual(got.map((t) => [t.hub.label, t.n]), [['輝達', 9], ['散熱', 3], ['台積電', 2]])
  assert.deepEqual(trendHubs(data, []), [])
  const many = Array.from({ length: 20 }, () => ({ hub: 'tw:2330', n: 1 }))
  assert.equal(trendHubs(data, many).length, TREND_SHOW)
  assert.equal(trendHubs(data, many, 2).length, 2)
})

test('T-446 AC3 只記哪天、哪個分頁、哪個亮點：不記身分與打的字；只收本站；排行公開暫存 60 秒、不放在新聞代理底下', () => {
  assert.deepEqual(TREND_DDL, [
    'CREATE TABLE IF NOT EXISTS web.galaxy_searches (day date NOT NULL, viewer text NOT NULL, hub text NOT NULL, PRIMARY KEY (day, viewer, hub))',
  ])
  assert.equal(TREND_DAYS, 7)
  assert.ok(TREND_PURGE_DAYS > TREND_DAYS)
  const ep = readFileSync(new URL('../functions/pub/galaxy-search.ts', import.meta.url), 'utf-8')
  assert.ok(ep.includes('sameOrigin(request)'))
  assert.ok(!/userEmail|cf-connecting-ip|x-forwarded-for/i.test(ep), '不記身分與 IP')
  assert.match(ep, /ON CONFLICT DO NOTHING/, '同一個分頁同一天搜同一個只算一次')
  assert.match(ep, /cached\(cacheKey\(request\)/)
  assert.match(ep, /public, max-age=\$\{PUBLIC_MAX_AGE_S\}/)
  assert.match(ep, /AT TIME ZONE 'Asia\/Taipei'/, '日期用台北')
  const api = readFileSync(new URL('../src/cloud/api.ts', import.meta.url), 'utf-8')
  assert.match(api, /recordGalaxySearch = \(id: string, hubs: string\[\]\)/, '前端只送分頁 id 與亮點 id')
})
