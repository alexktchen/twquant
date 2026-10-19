import assert from 'node:assert/strict'
import test from 'node:test'
import { expandEdges, parentOf, propagate, type Edge, type GraphConfig } from '../src/graph.ts'

const cfg: GraphConfig = {
  weights: { supplier: 0.6, belongs: 0.5, member: 0.5, peer: 0.4, theme: 0.6 },
  fanout_power: 0.5,
  max_degree: 3,
  max_links: 40,
}

// 輝達 → 廣達、緯穎（供應商，草擬）；緯穎屬於伺服器族群（另有神達、永擎）；台積電屬於晶圓製造（另有聯電）
const edges: Edge[] = [
  { src: 'us:NVDA', dst: 'tw:2382', type: 'supplier', draft: true },
  { src: 'us:NVDA', dst: 'tw:6669', type: 'supplier', draft: true },
  { src: 'tw:6669', dst: 'grp:伺服器', type: 'belongs' },
  { src: 'tw:3706', dst: 'grp:伺服器', type: 'belongs' },
  { src: 'tw:7711', dst: 'grp:伺服器', type: 'belongs' },
  { src: 'tw:2330', dst: 'grp:晶圓製造', type: 'belongs' },
  { src: 'tw:2303', dst: 'grp:晶圓製造', type: 'belongs' },
]

test('T-404：屬於族群自動補反向的成員線；同業是雙向的', () => {
  const e = expandEdges([...edges, { src: 'co:samsung', dst: 'us:MU', type: 'peer' }])
  assert.ok(e.some((x) => x.src === 'grp:伺服器' && x.dst === 'tw:3706' && x.type === 'member'))
  assert.ok(e.some((x) => x.src === 'us:MU' && x.dst === 'co:samsung' && x.type === 'peer'))
})

test('T-404：一度＝新聞提到的；往外走每條線乘係數、除以出度的平方根；草擬的線一路標下去', () => {
  const links = propagate(['us:NVDA', 'tw:2330'], edges, cfg)
  const by = new Map(links.map((l) => [l.entity, l]))
  assert.deepEqual([by.get('us:NVDA')?.degree, by.get('tw:2330')?.degree], [1, 1])
  // 輝達出度 2 → 0.6 ÷ √2
  assert.ok(Math.abs((by.get('tw:6669')?.score ?? 0) - 0.6 / Math.SQRT2) < 1e-12)
  assert.equal(by.get('tw:6669')?.draft, true)
  // 三度：輝達 → 緯穎 → 伺服器
  const grp = by.get('grp:伺服器')
  assert.equal(grp?.degree, 3)
  assert.deepEqual(grp?.path.map((s) => [s.entity, s.via]), [['us:NVDA', 'mention'], ['tw:6669', 'supplier'], ['grp:伺服器', 'belongs']])
  assert.equal(grp?.draft, true)
  assert.equal(parentOf(grp!), 'tw:6669')
  // 二度：台積電 → 晶圓製造；三度：晶圓製造 → 聯電（族群出度 2 → ÷√2）
  assert.equal(by.get('grp:晶圓製造')?.degree, 2)
  assert.equal(by.get('tw:2303')?.degree, 3)
  assert.ok(Math.abs((by.get('tw:2303')?.score ?? 0) - 0.5 * (0.5 / Math.SQRT2)) < 1e-12)
  assert.equal(by.has('tw:3706'), false, '超過三度（輝達→緯穎→伺服器→神達＝四度）不算')
})

test('T-404：同一檔有兩條路取分數高的；不繞回路徑上的點；同輸入同輸出', () => {
  const two = propagate(['us:NVDA', 'grp:伺服器'], edges, cfg)
  const w = two.find((l) => l.entity === 'tw:6669')
  assert.equal(w?.path[0].entity, 'us:NVDA', '輝達直接供應商 0.42 勝過伺服器族群成員 0.29')
  assert.ok(two.every((l) => new Set(l.path.map((s) => s.entity)).size === l.path.length))
  assert.deepEqual(propagate(['us:NVDA', 'grp:伺服器'], edges, cfg), two)
})

test('T-404：截到 max_links 時，留下的點的上一層一定在（樹不斷）', () => {
  const few = propagate(['us:NVDA', 'tw:2330'], edges, { ...cfg, max_links: 3 })
  const ids = new Set(few.map((l) => l.entity))
  for (const l of few) for (const s of l.path) assert.ok(ids.has(s.entity), `${l.entity} 的路徑缺 ${s.entity}`)
  assert.ok(few.length >= 3)
})
