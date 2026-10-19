import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { childrenOf, classOf, hiddenKids, lineWidth, pathTo, routeLines, routeRows, tableRows, topTw, viaText, type GNode, type NewsGraph, type RowH } from '../src/cloud/news-graph.ts'

const n = (id: string, degree: number, parent: string | null, extra: Partial<GNode> = {}): GNode => ({
  id,
  type: 'tw',
  label: id,
  sub: id,
  degree,
  score: 1 / degree,
  parent,
  via: degree === 1 ? 'mention' : 'peer',
  draft: false,
  ...extra,
})
const H: RowH = { 1: 56, 2: 48, 3: 40 }
const COLS: [number, number, number] = [30, 74, 118]

test('路線圖的列：一度依分數排、每個一度底下接它的二度、三度只在二度點開後列出；列高依度數、y 連續、「＋N 站」只在收著的二度', () => {
  const nodes = [n('A', 1, null), n('B', 1, null, { score: 0.9 }), n('A1', 2, 'A'), n('A2', 2, 'A', { score: 0.4 }), n('B1', 2, 'B'), n('A1x', 3, 'A1'), n('A1y', 3, 'A1')]
  const closed = routeRows(nodes, new Set(), H, 62)
  assert.deepEqual(
    closed.map((r) => r.n.id),
    ['A', 'A1', 'A2', 'B', 'B1'],
  )
  assert.deepEqual(
    closed.map((r) => r.depth),
    [1, 2, 2, 1, 2],
  )
  assert.equal(closed[0].y, 62)
  for (let i = 1; i < closed.length; i++) assert.equal(closed[i].y, closed[i - 1].y + closed[i - 1].h, '列一列接一列')
  assert.equal(closed[0].cy, 62 + 28)
  assert.deepEqual(
    closed.map((r) => r.more),
    [0, 2, 0, 0, 0],
    'A1 底下收著兩個三度',
  )
  const open = routeRows(nodes, new Set(['A1']), H, 62)
  assert.deepEqual(
    open.map((r) => r.n.id),
    ['A', 'A1', 'A1x', 'A1y', 'A2', 'B', 'B1'],
  )
  assert.equal(open[1].more, 0, '點開就不再寫「＋N 站」')
  assert.equal(open[2].h, 40)
  assert.deepEqual(routeRows(nodes, new Set(['A1']), H, 62), open, '同輸入同輸出')
})

test('路線圖的線：一度在主幹直直往下（起點站→A→B）；二度第一站從上一層 S 形接過來、同層之後直直往下；顏色＝到站的類別、粗細＝分數、草擬', () => {
  const nodes = [n('A', 1, null), n('B', 1, null, { score: 0.9 }), n('A1', 2, 'A', { via: 'supplier', draft: true }), n('A2', 2, 'A', { type: 'group', score: 0.4 }), n('B1', 2, 'B')]
  const rows = routeRows(nodes, new Set(), H, 62)
  const segs = routeLines(rows, COLS, 31)
  const by = new Map(segs.map((s) => [s.to, s]))
  assert.equal(by.get('A')!.d, 'M30,31 V90', '起點站中心到 A')
  assert.equal(by.get('A')!.cls, 'trunk')
  assert.equal(by.get('B')!.d, `M30,90 V${rows.find((r) => r.n.id === 'B')!.cy}`, '主幹從 A 直接到 B，中間的二度列不打斷')
  assert.ok(by.get('A1')!.d.startsWith('M30,90 C30,'), 'A 的第一個二度從 A 的站 S 形出去')
  assert.ok(by.get('A1')!.d.endsWith(' 74,142'), '接到二度欄、A1 的中心（62＋56＋24）')
  assert.equal(by.get('A2')!.d, 'M74,142 V190', '第二個二度從 A1 直直往下')
  assert.deepEqual([by.get('A1')!.cls, by.get('A2')!.cls, by.get('B1')!.cls], ['tw', 'cluster', 'tw'])
  assert.deepEqual([by.get('A1')!.draft, by.get('A2')!.draft, by.get('A')!.draft], [true, false, false])
  assert.equal(by.get('A')!.w, 7)
  assert.equal(by.get('A2')!.w, 4.6)
  assert.equal(lineWidth(0), 3)
  assert.equal(lineWidth(9), 7)
})

test('關係圖：從新聞到某點的路徑、線的說明、類別、清單順序、最相關台股', () => {
  const nodes = [n('us:NVDA', 1, null, { type: 'us' }), n('tw:6669', 2, 'us:NVDA', { via: 'supplier', draft: true }), n('tw:3706', 3, 'tw:6669', { via_label: '伺服器', draft: true })]
  assert.deepEqual(pathTo(nodes, 'tw:3706').map((x) => x.id), ['us:NVDA', 'tw:6669', 'tw:3706'])
  assert.deepEqual(pathTo(nodes, 'nope'), [])
  assert.deepEqual(nodes.map(viaText), ['新聞提到', '供應商', '同族群・伺服器'])
  assert.deepEqual(['us', 'company', 'tw', 'group', 'theme'].map((t) => classOf(t as GNode['type'])), ['abroad', 'abroad', 'tw', 'cluster', 'cluster'])
  assert.deepEqual(tableRows([nodes[2], nodes[0], nodes[1]]).map((x) => x.degree), [1, 2, 3])
  assert.deepEqual(topTw(nodes).map((x) => x.id), ['tw:6669', 'tw:3706'])
  assert.equal(hiddenKids(nodes[1], nodes, new Set()), 1)
  assert.equal(hiddenKids(nodes[1], nodes, new Set(['tw:6669'])), 0)
})

test('關係圖示範資料：每個點的上一層都在圖上、度數連續、一度沒有上一層；草擬一路往下傳', () => {
  const demo = JSON.parse(readFileSync(new URL('../src/cloud/news-graph-demo.json', import.meta.url), 'utf8')) as NewsGraph[]
  assert.equal(demo.length, 3)
  for (const g of demo) {
    const by = new Map(g.nodes.map((x) => [x.id, x]))
    for (const x of g.nodes) {
      if (x.degree === 1) assert.equal(x.parent, null)
      else {
        const parent = by.get(x.parent ?? '')
        assert.ok(parent, `${g.item.id} ${x.id} 的上一層 ${x.parent} 不在圖上`)
        assert.equal(parent.degree, x.degree - 1)
        if (parent.draft) assert.equal(x.draft, true, `${x.id} 的上一層是草擬的`)
      }
    }
    assert.ok(childrenOf(g.nodes).get(null)!.length >= 3)
  }
})
