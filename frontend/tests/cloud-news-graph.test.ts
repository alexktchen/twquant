import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { childrenOf, classOf, edgePath, orbitBounds, orbitLayout, orbitPath, pathTo, project, radialLayout, showLabel, strokeOf, tableRows, viaText, type GNode, type NewsGraph } from '../src/cloud/news-graph.ts'

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
const R: [number, number, number] = [72, 128, 172]

test('關係圖：放射狀樹——每度一圈、角度照（葉子數＋保底份量）分、子點在父點的角度範圍裡、同輸入同輸出', () => {
  // A 底下兩片葉子、B 一片；一度的保底份量 1.2 → A 分到 3.2／5.4 圈
  const nodes = [n('A', 1, null), n('B', 1, null, { score: 0.9 }), n('A1', 2, 'A'), n('A2', 2, 'A', { score: 0.4 }), n('B1', 2, 'B')]
  const p = radialLayout(nodes, R)
  const by = new Map(p.map((x) => [x.id, x]))
  for (const x of p) assert.ok(Math.abs(Math.hypot(x.x, x.y) - R[x.degree - 1]) < 0.02, `${x.id} 在第 ${x.degree} 圈`)
  const spanA = (2 * Math.PI * 3.2) / 5.4
  assert.ok(Math.abs(by.get('A')!.angle - (-Math.PI / 2 + spanA / 2)) < 1e-9, 'A 從正上方順時針')
  assert.ok(Math.abs(by.get('B')!.angle - (-Math.PI / 2 + spanA + (2 * Math.PI - spanA) / 2)) < 1e-9)
  for (const k of ['A1', 'A2']) {
    const c = by.get(k)!
    assert.ok(c.angle > -Math.PI / 2 && c.angle < -Math.PI / 2 + spanA, `${k} 在 A 的範圍裡`)
  }
  assert.ok(by.get('A1')!.angle < by.get('A2')!.angle, '分數高的先排')
  assert.deepEqual(radialLayout(nodes, R), p)
})

test('關係圖：葉子少的一度也分得到位置（博通、超微不再疊在一起）；直的螢幕拉成橢圓', () => {
  // 一個一度底下 8 片葉子、另外兩個一度各 0 片：沒有保底時兩個小的只分到 1/10 圈、疊在一起
  const big = [n('BIG', 1, null), ...Array.from({ length: 8 }, (_, i) => n(`k${i}`, 2, 'BIG'))]
  const p = radialLayout([...big, n('S1', 1, null, { score: 0.5 }), n('S2', 1, null, { score: 0.4 })], R)
  const a1 = p.find((x) => x.id === 'S1')!.angle
  const a2 = p.find((x) => x.id === 'S2')!.angle
  const gap = Math.abs(a1 - a2) * R[0]
  assert.ok(gap > 40, `兩個小的一度在圈上隔 ${gap.toFixed(0)}px，要放得下名字`)
  const tall = radialLayout(big, R, 1.4)
  for (const x of tall) assert.ok(Math.abs((x.x / x.r) ** 2 + (x.y / (x.r * 1.4)) ** 2 - 1) < 1e-3, `${x.id} 在橢圓上`)
})

test('關係圖：從新聞到某點的路徑、線粗、三度名字什麼時候顯示、線的說明', () => {
  const nodes = [n('us:NVDA', 1, null, { type: 'us' }), n('tw:6669', 2, 'us:NVDA', { via: 'supplier', draft: true }), n('tw:3706', 3, 'tw:6669', { via_label: '伺服器', draft: true })]
  assert.deepEqual(pathTo(nodes, 'tw:3706').map((x) => x.id), ['us:NVDA', 'tw:6669', 'tw:3706'])
  assert.deepEqual(pathTo(nodes, 'nope'), [])
  assert.equal(strokeOf(1), 4.5)
  assert.equal(strokeOf(0), 1)
  assert.equal(strokeOf(7), 4.5)
  assert.equal(showLabel(nodes[2], new Set(), null), false, '三度預設只畫小點')
  assert.equal(showLabel(nodes[2], new Set(['tw:6669']), null), true, '上一層點開就顯示')
  assert.equal(showLabel(nodes[2], new Set(), 'tw:3706'), true)
  assert.equal(showLabel(nodes[1], new Set(), null), true)
  assert.deepEqual(nodes.map(viaText), ['新聞提到', '供應商', '同族群・伺服器'])
  assert.deepEqual(['us', 'company', 'tw', 'group', 'theme'].map((t) => classOf(t as GNode['type'])), ['abroad', 'abroad', 'tw', 'cluster', 'cluster'])
  assert.ok(edgePath({ x: 0, y: 0 }, radialLayout(nodes, R)[0]).startsWith('M0,0 Q'))
  assert.deepEqual(tableRows([nodes[2], nodes[0], nodes[1]]).map((x) => x.degree), [1, 2, 3])
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

test('關係圖 3D：每個點在自己那一圈的高度與半徑上；透視近大遠小、俯視時近的在畫面下方；軌道環與外框同輸入同輸出', () => {
  const nodes = [n('A', 1, null), n('B', 1, null, { score: 0.9 }), n('A1', 2, 'A'), n('A2', 2, 'A', { score: 0.4 }), n('B1', 2, 'B')]
  const H: [number, number, number] = [20, 0, -20]
  const pos = orbitLayout(radialLayout(nodes, R), H)
  for (const x of nodes) {
    const v = pos.get(x.id)!
    assert.ok(Math.abs(Math.hypot(v.x, v.z) - R[x.degree - 1]) < 0.05, `${x.id} 在第 ${x.degree} 圈`)
    assert.equal(v.y, H[x.degree - 1])
  }
  const near = project({ x: 100, y: 0, z: 100 }, 0, 0, 640)
  const far = project({ x: 100, y: 0, z: -100 }, 0, 0, 640)
  assert.ok(near.s > 1 && far.s < 1 && near.x > far.x, '靠近鏡頭的比較大')
  assert.ok(Math.abs(project({ x: 0, y: 50, z: 0 }, 0, 0, 640).y - 50) < 1e-9, '正對著看、不傾斜 → 原樣')
  // 從上往下看：靠近鏡頭的點在畫面下方（y 正）、遠的在上方（y 負）
  assert.ok(project({ x: 0, y: 0, z: 100 }, 0, -0.5, 640).y > 0)
  assert.ok(project({ x: 0, y: 0, z: -100 }, 0, -0.5, 640).y < 0)
  const ring = orbitPath(100, 0, 0, -0.3, 640, 8)
  assert.ok(ring.startsWith('M') && ring.split('L').length === 9, '八段的軌道環')
  assert.equal(orbitPath(100, 0, 0, -0.3, 640, 8), ring, '同輸入同輸出')
  const b = orbitBounds(R, H, 0.5, -0.6, 640)
  assert.ok(b.w > 2 * R[2] * 0.95 && b.top < 0 && b.bottom > 0 && b.bottom > -b.top, '俯視：寬約兩倍半徑、近的那半邊比較大')
})
