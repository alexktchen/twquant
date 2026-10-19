import assert from 'node:assert/strict'
import test from 'node:test'
import { buildNodes, edgesFor, itemMentions, type EdgeRow, type EntityRow, type GraphApiConfig } from '../src/graphapi.ts'

const cfg: GraphApiConfig = {
  weights: { supplier: 0.6, belongs: 0.5, member: 0.5, peer: 0.4, theme: 0.6 },
  fanout_power: 0.5,
  max_degree: 3,
  max_links: 40,
  peers_per_group: 2,
}

const ent = (id: string, type: EntityRow['type'], label: string, sub: string | null = null): EntityRow => ({ id, type, label, sub })
const entities = new Map(
  [
    ent('us:NVDA', 'us', '輝達', 'NVDA'),
    ent('tw:2382', 'tw', '廣達', '2382'),
    ent('tw:6669', 'tw', '緯穎', '6669'),
    ent('tw:3706', 'tw', '神達', '3706'),
    ent('tw:7711', 'tw', '永擎', '7711'),
    ent('grp:伺服器', 'group', '伺服器', '族群'),
    ent('th:memory', 'theme', '記憶體'),
  ].map((e) => [e.id, e]),
)

const belongs = (src: string, rank: number | null): EdgeRow => ({ src, dst: 'grp:伺服器', type: 'belongs', status: 'approved', label: null, rank })
// 伺服器族群：成交金額名次 廣達 1、緯穎 2、神達 3、永擎沒有名次（排最後）
const rows: EdgeRow[] = [
  belongs('tw:7711', null),
  belongs('tw:3706', 3),
  belongs('tw:6669', 2),
  belongs('tw:2382', 1),
  { src: 'us:NVDA', dst: 'tw:6669', type: 'supplier', status: 'draft', label: null, rank: null },
]

const peers = (edges: ReturnType<typeof edgesFor>) => edges.filter((e) => e.type === 'peer').map((e) => `${e.src}>${e.dst}`).sort()

test('T-404：同族群線——族群裡每一檔連到成交金額前 peers_per_group 名（自己不連自己），label＝族群名', () => {
  const edges = edgesFor(rows, entities, cfg)
  assert.deepEqual(peers(edges), [
    'tw:2382>tw:6669',
    'tw:3706>tw:2382',
    'tw:3706>tw:6669',
    'tw:6669>tw:2382',
    'tw:7711>tw:2382',
    'tw:7711>tw:6669',
  ])
  for (const e of edges.filter((x) => x.type === 'peer')) {
    assert.equal(e.label, '伺服器')
    assert.equal(e.draft, false)
  }
  // 表裡的線照放
  assert.equal(edges.filter((e) => e.type === 'belongs').length, 4)
  assert.ok(edges.some((e) => e.type === 'supplier' && e.src === 'us:NVDA' && e.dst === 'tw:6669'))
})

test('T-404：draft 狀態 → draft:true，並傳到路徑下游的點', () => {
  const edges = edgesFor(rows, entities, cfg)
  assert.equal(edges.find((e) => e.type === 'supplier')?.draft, true)
  assert.equal(edges.find((e) => e.type === 'belongs')?.draft, false)
  const nodes = buildNodes(['us:NVDA'], rows, entities, cfg)
  const by = new Map(nodes.map((n) => [n.id, n]))
  assert.equal(by.get('us:NVDA')?.draft, false)
  assert.equal(by.get('tw:6669')?.draft, true)
  // 三度：輝達 → 緯穎（草擬）→ 族群或同業，一路都是草擬
  for (const n of nodes.filter((x) => x.degree >= 2)) assert.equal(n.draft, true, n.id)
})

test('T-404：實體表沒有的 id（提到的、線的兩端）直接略過，不丟錯', () => {
  const extra: EdgeRow[] = [
    ...rows,
    { src: 'us:NVDA', dst: 'tw:9999', type: 'supplier', status: 'draft', label: null, rank: null },
    { src: 'co:ghost', dst: 'tw:2382', type: 'supplier', status: 'draft', label: null, rank: null },
  ]
  const edges = edgesFor(extra, entities, cfg)
  assert.ok(!edges.some((e) => e.src === 'co:ghost' || e.dst === 'tw:9999'))
  const nodes = buildNodes(['us:NVDA', 'us:NOPE'], extra, entities, cfg)
  assert.ok(nodes.length > 0)
  assert.ok(nodes.every((n) => entities.has(n.id)))
  assert.ok(!nodes.some((n) => n.id === 'us:NOPE'))
  assert.deepEqual(buildNodes(['us:NOPE'], extra, entities, cfg), [])
  assert.deepEqual(buildNodes([], rows, entities, cfg), [])
})

test('T-404：GNode 欄位——sub 用實體表、沒有就用冒號後面；parent、via、via_label、score 四位', () => {
  const nodes = buildNodes(['us:NVDA', 'th:memory'], rows, entities, cfg)
  const by = new Map(nodes.map((n) => [n.id, n]))
  assert.deepEqual(by.get('us:NVDA'), { id: 'us:NVDA', type: 'us', label: '輝達', sub: 'NVDA', degree: 1, score: 1, parent: null, via: 'mention', draft: false })
  assert.equal(by.get('th:memory')?.sub, 'memory')
  const wiwynn = by.get('tw:6669')!
  assert.equal(wiwynn.parent, 'us:NVDA')
  assert.equal(wiwynn.via, 'supplier')
  assert.equal(wiwynn.degree, 2)
  assert.equal(wiwynn.via_label, undefined)
  assert.equal(wiwynn.chg, undefined)
  // 輝達出度 1（只有緯穎）：0.6 / 1^0.5
  assert.equal(wiwynn.score, 0.6)
  // 三度：緯穎的同族群線寫族群名
  const peer = nodes.find((n) => n.via === 'peer')!
  assert.equal(peer.parent, 'tw:6669')
  assert.equal(peer.via_label, '伺服器')
  assert.equal(peer.degree, 3)
  for (const n of nodes) assert.equal(n.score, Number(n.score.toFixed(4)))
  assert.deepEqual(Object.keys(by.get('grp:伺服器')!).sort(), ['degree', 'draft', 'id', 'label', 'parent', 'score', 'sub', 'type', 'via'])
  assert.equal(by.get('grp:伺服器')?.sub, '族群')
})

test('T-404：同輸入同輸出（線的順序打亂也一樣）', () => {
  const a = buildNodes(['us:NVDA', 'tw:3706'], rows, entities, cfg)
  const b = buildNodes(['tw:3706', 'us:NVDA'], [...rows].reverse(), entities, cfg)
  assert.deepEqual(JSON.stringify(a), JSON.stringify(b))
  assert.deepEqual(JSON.stringify(edgesFor(rows, entities, cfg)), JSON.stringify(edgesFor([...rows].reverse(), entities, cfg)))
})

test('T-402：/v1/items 的 mentions——台股在前、再國外、再族群／主題，同一實體只一次，最多 limit 個', () => {
  const r = itemMentions(
    [
      { id: 'th:memory', type: 'theme', label: '記憶體' },
      { id: 'us:NVDA', type: 'us', label: '輝達' },
      { id: 'co:samsung', type: 'company', label: '三星' },
      { id: 'tw:2408', type: 'tw', label: '南亞科' },
      { id: 'grp:伺服器', type: 'group', label: '伺服器' },
      { id: 'tw:2330', type: 'tw', label: '台積電' },
      { id: 'us:NVDA', type: 'us', label: '輝達' },
    ],
    5,
  )
  assert.deepEqual(
    r.map((x) => x.id),
    ['tw:2330', 'tw:2408', 'co:samsung', 'us:NVDA', 'grp:伺服器'],
  )
  assert.deepEqual(itemMentions([], 6), [])
})
