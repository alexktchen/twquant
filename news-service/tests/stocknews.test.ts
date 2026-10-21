import assert from 'node:assert/strict'
import test from 'node:test'
import { buildNodes, type EdgeRow, type EntityRow, type GraphApiConfig } from '../src/graphapi.ts'
import { countsFor, coverageDaysOf, hubsOf, linksTo, newsDays, parseStockIds, prepare, quietGap, validateStockNewsConfig } from '../src/stocknews.ts'

const cfg: GraphApiConfig = {
  weights: { supplier: 0.6, belongs: 0.5, member: 0.5, peer: 0.4, theme: 0.6 },
  fanout_power: 0.5,
  max_degree: 3,
  max_links: 26,
  peers_per_group: 5,
}
const ent = (id: string, type: EntityRow['type'], label: string): [string, EntityRow] => [id, { id, type, label, sub: null }]
const entities = new Map<string, EntityRow>([
  ent('us:NVDA', 'us', '輝達'),
  ent('tw:2382', 'tw', '廣達'),
  ent('tw:2330', 'tw', '台積電'),
  ent('tw:3231', 'tw', '緯創'),
  ent('tw:9999', 'tw', '無關'),
  ent('grp:伺服器', 'group', '伺服器'),
])
const rows: EdgeRow[] = [
  { src: 'us:NVDA', dst: 'tw:2382', type: 'supplier', status: 'draft', label: null, rank: null },
  { src: 'tw:2382', dst: 'grp:伺服器', type: 'belongs', status: 'approved', label: null, rank: 1 },
  { src: 'tw:3231', dst: 'grp:伺服器', type: 'belongs', status: 'approved', label: null, rank: 2 },
]
const items = [
  { item_id: 'a', entities: ['tw:2382'] }, // 直接提到廣達
  { item_id: 'b', entities: ['us:NVDA'] }, // 輝達 → 供應商 廣達（二度、草擬）
  { item_id: 'c', entities: ['tw:2330'] }, // 跟廣達沒有線
  { item_id: 'd', entities: ['tw:3231'] }, // 緯創 → 同族群 廣達（二度）
  { item_id: 'e', entities: ['grp:伺服器'] }, // 族群 → 成員 廣達（二度）
]

test('反向看圖：直接提到＝一度、走一步到＝二度，路徑與草擬照單則關係圖；沒有線的不算', () => {
  const prep = prepare(rows, entities, cfg)
  const links = linksTo('tw:2382', items, prep, entities, cfg, 2)
  assert.deepEqual(
    links.map((l) => [l.item_id, l.degree, l.draft]),
    [
      ['a', 1, false],
      ['b', 2, true],
      ['e', 2, false],
      ['d', 2, false],
    ],
  )
  const b = links.find((l) => l.item_id === 'b')!
  assert.deepEqual(
    b.path.map((s) => [s.label, s.via]),
    [
      ['輝達', 'mention'],
      ['廣達', 'supplier'],
    ],
  )
  // 一度上限：只剩直接提到的
  assert.deepEqual(
    linksTo('tw:2382', items, prep, entities, cfg, 1).map((l) => l.item_id),
    ['a'],
  )
  assert.throws(() => linksTo('tw:2382', items, prep, entities, cfg, 3), /只能 1 或 2/)
})

test('反向看圖跟單則關係圖一致：說間接關聯的新聞，那則的心智圖裡一定有這一檔、度數一樣', () => {
  const prep = prepare(rows, entities, cfg)
  for (const l of linksTo('tw:2382', items, prep, entities, cfg, 2)) {
    const it = items.find((x) => x.item_id === l.item_id)!
    const node = buildNodes(it.entities, rows, entities, cfg).find((n) => n.id === 'tw:2382')
    assert.ok(node, `${l.item_id} 的心智圖裡要有廣達`)
    assert.equal(node.degree, l.degree)
  }
})

test('名單小標：每檔直接幾則、間接幾則；參數檢查', () => {
  const prep = prepare(rows, entities, cfg)
  assert.deepEqual(countsFor(['tw:2382', 'tw:9999', 'tw:2382'], items, prep, entities, cfg, 2), {
    'tw:2382': { direct: 1, indirect: 3 },
    'tw:9999': { direct: 0, indirect: 0 },
  })
  assert.deepEqual(parseStockIds('tw:2330, tw:2382,tw:2330', 10), ['tw:2330', 'tw:2382'])
  assert.throws(() => parseStockIds('', 10), /缺 ids/)
  assert.throws(() => parseStockIds('tw:2330,us:NVDA', 10), /tw:2330/)
  assert.throws(() => parseStockIds('tw:1101,tw:1102,tw:1103', 2), /最多 2/)
  assert.throws(() => validateStockNewsConfig({ stock_max_degree: 3, stock_days: 7, stock_limit: 20, counts_days: 3, counts_max_ids: 60, hub_share: 0.15, timeline_days: 120, timeline_per_day: 3, quiet_min_days: 14 }), /最多 2/)
  assert.throws(() => validateStockNewsConfig(undefined), /stock_news/)
})

test('到處都提的實體不當間接的橋（2026-10-20：「AI」害每檔都有上百則間接關聯）；直接提到照算', () => {
  const prep = prepare(rows, entities, cfg)
  // 輝達出現在 4 則裡的 3 則（75%）→ 超過 50% 就是「到處都提」
  const many = [
    { item_id: 'n1', entities: ['us:NVDA'] },
    { item_id: 'n2', entities: ['us:NVDA'] },
    { item_id: 'n3', entities: ['us:NVDA', 'tw:2382'] },
    { item_id: 'x', entities: ['tw:2330'] },
  ]
  assert.deepEqual([...hubsOf(many, 0.5)], ['us:NVDA'])
  assert.deepEqual(
    linksTo('tw:2382', many, prep, entities, cfg, 2, 0.5).map((l) => [l.item_id, l.degree]),
    [['n3', 1]],
    '輝達這條橋不算；n3 直接提到廣達照算',
  )
  assert.equal(linksTo('tw:2382', many, prep, entities, cfg, 2).length, 3, '不設門檻就三則都算')
  assert.deepEqual(countsFor(['tw:2382'], many, prep, entities, cfg, 2, 0.5), { 'tw:2382': { direct: 1, indirect: 0 } })
})

test('T-419 時間軸：每天幾則、哪幾家（同一家兩個頻道算一家）、前幾則標題（新的先）；日期由舊到新', () => {
  const outlet = (s: string) => ({ 'cnyes-headline': '鉅亨網', 'cnyes-tw-stock': '鉅亨網', 'udn-money-stock': '經濟日報' })[s] ?? s
  const row = (day: string, id: string, source: string, t: string, zh: string | null = null) => ({ day, id, source, title: `T${id}`, title_zh: zh, url: `https://x/${id}`, first_seen_at: t })
  const got = newsDays(
    [
      row('2026-10-20', 'b', 'cnyes-headline', '2026-10-20T02:00:00Z', '譯b'),
      row('2026-10-18', 'a', 'udn-money-stock', '2026-10-18T01:00:00Z'),
      row('2026-10-20', 'c', 'cnyes-tw-stock', '2026-10-20T03:00:00Z'),
      row('2026-10-20', 'd', 'udn-money-stock', '2026-10-20T01:00:00Z'),
    ],
    outlet,
    2,
  )
  assert.deepEqual(got.map((d) => [d.day, d.n, d.outlets]), [['2026-10-18', 1, ['經濟日報']], ['2026-10-20', 3, ['經濟日報', '鉅亨網']]])
  assert.deepEqual(got[1].items.map((i) => [i.id, i.title, i.src]), [['c', 'Tc', '鉅亨網'], ['b', '譯b', '鉅亨網']], '每天最多 perDay 則，新的先，有譯文用譯文')
})

test('T-419 久違的消息：隔的天數、沒有前一次就是「至少」、收新聞不夠久或隔不夠久都不標', () => {
  assert.deepEqual(quietGap(['2026-09-01', '2026-10-20'], 60, 120, 14), { days: 49, at_least: false })
  assert.equal(quietGap(['2026-10-18', '2026-10-20'], 60, 120, 14), null, '隔 2 天不算')
  assert.deepEqual(quietGap(['2026-10-20'], 30, 120, 14), { days: 30, at_least: true }, '時間軸裡沒有前一次：至少收新聞以來的天數')
  assert.deepEqual(quietGap(['2026-10-20'], 400, 120, 14), { days: 120, at_least: true }, '超過時間軸就說至少 120 天')
  assert.equal(quietGap(['2026-10-20'], 2, 120, 14), null, '我們才收 2 天新聞，沒資格說久違')
  assert.equal(quietGap([], 60, 120, 14), null)
  assert.equal(coverageDaysOf('2026-10-19T12:54:00Z', new Date('2026-10-20T13:00:00Z')), 2)
  assert.equal(coverageDaysOf(null, new Date()), 0)
  assert.throws(() => validateStockNewsConfig({ stock_max_degree: 2, stock_days: 7, stock_limit: 20, counts_days: 3, counts_max_ids: 60, hub_share: 0.15, timeline_days: 120, timeline_per_day: 3 }), /quiet_min_days/)
})
