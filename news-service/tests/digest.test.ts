import assert from 'node:assert/strict'
import test from 'node:test'
import { baseDaysOf, hotStocks, multiOutlet, topTopics, validateDigestConfig, type DigestConfig, type EvRow } from '../src/digest.ts'

const cfg: DigestConfig = { topics_n: 3, topic_stocks: 2, multi_min_outlets: 3, multi_n: 2, hot_n: 3, hot_min_today: 3, base_days: 6, everywhere_share: 0.15 }

test('熱門主題：依近 24 小時則數排、沒提到的不列、同數依代號；附常一起提到的台股（最多 topic_stocks 檔）；到處都提的另外列', () => {
  const res = topTopics(
    [
      { id: 'th:oil', label: '油價', type: 'theme', n_now: 5, n_prev: 9 },
      { id: 'th:ai', label: 'AI', type: 'theme', n_now: 40, n_prev: 30 },
      { id: 'grp:記憶體', label: '記憶體', type: 'group', n_now: 5, n_prev: 1 },
      { id: 'th:x', label: 'X', type: 'theme', n_now: 0, n_prev: 4 },
      { id: 'th:y', label: 'Y', type: 'theme', n_now: 2, n_prev: 0 },
    ],
    [
      { topic: 'th:ai', stock: 'tw:2330', label: '台積電', n: 9 },
      { topic: 'th:ai', stock: 'tw:2382', label: '廣達', n: 9 },
      { topic: 'th:ai', stock: 'tw:3231', label: '緯創', n: 2 },
      { topic: 'grp:記憶體', stock: 'tw:2408', label: '南亞科', n: 3 },
    ],
    100,
    cfg,
  )
  const got = res.items
  // AI 40／100 則＝40%，超過 15% → 不列，改在 everywhere
  assert.deepEqual(res.everywhere, [{ label: 'AI', share: 0.4 }])
  assert.deepEqual(got.map((t) => [t.label, t.n, t.prev]), [['記憶體', 5, 1], ['油價', 5, 9], ['Y', 2, 0]])
  assert.deepEqual(got[0].stocks.map((s) => s.label), ['南亞科'])
  assert.deepEqual(got[1].stocks, [])
  assert.equal(topTopics([{ id: 'th:ai', label: 'AI', type: 'theme', n_now: 40, n_prev: 0 }], [], 0, cfg).items.length, 1, '沒有總數就不擋')
})

const ev = (ev: string, item: string, source: string, t: string, extra: Partial<EvRow> = {}): EvRow => ({
  ev, item_id: item, source, title: `T${item}`, title_zh: null, url: `https://x/${item}`, first_seen_at: t, stocks: [], ...extra,
})
const outlet = (s: string) => ({ 'cnyes-headline': '鉅亨網', 'cnyes-tw-stock': '鉅亨網', 'udn-money-stock': '經濟日報', 'cna-finance': '中央社', 'ltn-business': '自由財經' })[s] ?? s

test('多家同報：同一家的兩個頻道算一家、未達家數不列；標題用最早那則（有譯文用譯文）；家數多的在前、同家數新的在前', () => {
  const rows = [
    ev('A', 'a1', 'cnyes-headline', '2026-10-20T01:00:00Z', { title_zh: '台積電早盤跌', stocks: ['台積電'] }),
    ev('A', 'a2', 'cnyes-tw-stock', '2026-10-20T01:05:00Z', { stocks: ['台積電'] }),
    ev('A', 'a3', 'udn-money-stock', '2026-10-20T01:20:00Z', { stocks: ['聯電'] }),
    ev('A', 'a4', 'cna-finance', '2026-10-20T02:00:00Z'),
    ev('B', 'b1', 'cnyes-headline', '2026-10-20T03:00:00Z'),
    ev('B', 'b2', 'cnyes-tw-stock', '2026-10-20T03:01:00Z'),
    ev('B', 'b3', 'udn-money-stock', '2026-10-20T03:02:00Z'),
    ev('C', 'c1', 'cna-finance', '2026-10-20T04:00:00Z'),
    ev('C', 'c2', 'ltn-business', '2026-10-20T04:01:00Z'),
    ev('C', 'c3', 'udn-money-stock', '2026-10-20T04:02:00Z'),
  ]
  const got = multiOutlet(rows, outlet, cfg)
  // A、C 都是三家：同家數新的在前（C 最後一則 04:02、A 02:00）
  assert.deepEqual(got.map((m) => [m.ev, m.outlets.length]), [['C', 3], ['A', 3]])
  assert.deepEqual(got[1], {
    ev: 'A', outlets: ['中央社', '經濟日報', '鉅亨網'], items: 4, title: '台積電早盤跌', url: 'https://x/a1',
    first_seen_at: '2026-10-20T01:00:00Z', last_seen_at: '2026-10-20T02:00:00Z', stocks: ['台積電', '聯電'],
  })
  assert.ok(!got.some((m) => m.ev === 'B'), 'B 只有鉅亨網兩個頻道＋經濟日報＝兩家，不到三家')
})

test('股票熱度：平常值用之前的日平均、平常是 0 當 0.5 算；今天不到門檻不列；還沒有平常值就只照則數排', () => {
  const rows = [
    { id: 'tw:2330', label: '台積電', sub: '2330', n_now: 30, n_base: 120 },
    { id: 'tw:1815', label: '富喬', sub: '1815', n_now: 6, n_base: 0 },
    { id: 'tw:2382', label: '廣達', sub: '2382', n_now: 8, n_base: 12 },
    { id: 'tw:9999', label: '小', sub: '9999', n_now: 2, n_base: 0 },
  ]
  assert.deepEqual(hotStocks(rows, 6, cfg).map((h) => [h.label, h.n, h.base, h.ratio]), [
    ['富喬', 6, 0, 12],
    ['廣達', 8, 2, 4],
    ['台積電', 30, 20, 1.5],
  ])
  assert.deepEqual(hotStocks(rows, 0, cfg).map((h) => [h.label, h.base, h.ratio]), [['台積電', null, null], ['廣達', null, null], ['富喬', null, null]])
})

test('平常值天數：最早一則之後、近 24 小時之前有幾整天，最多 base_days；資料不到兩天 → 0', () => {
  const now = new Date('2026-10-27T12:00:00Z')
  assert.equal(baseDaysOf('2026-10-26T13:00:00Z', now, cfg), 0)
  assert.equal(baseDaysOf('2026-10-25T11:00:00Z', now, cfg), 1)
  assert.equal(baseDaysOf('2026-10-01T00:00:00Z', now, cfg), 6)
  assert.equal(baseDaysOf(null, now, cfg), 0)
  assert.throws(() => validateDigestConfig({ ...cfg, multi_min_outlets: 1 }), /multi_min_outlets/)
  assert.throws(() => validateDigestConfig({ ...cfg, everywhere_share: 0 }), /everywhere_share/)
  assert.throws(() => validateDigestConfig(undefined), /digest/)
})
