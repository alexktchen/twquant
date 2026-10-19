import assert from 'node:assert/strict'
import test from 'node:test'
import { branchCurve, classOf, hiddenKids, mindBranches, mindLayout, pathTo, relevance, tableRows, taperPath, topTw, viaText, type GNode, type MindOpts } from '../src/cloud/news-graph.ts'

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
const PHONE: MindOpts = { rowH: { 1: 42, 2: 44, 3: 40 }, slotW: { 1: 98, 2: 104, 3: 92 }, gap: 24, indent3: 16, twoSided: false, centerW: 104, centerH: 66, pad: 12 }
const DESK: MindOpts = { ...PHONE, indent3: null, twoSided: true, centerW: 150, centerH: 72, pad: 16 }

test('心智圖（手機、只往右）：一度在自己子樹的正中間、二度一列一列往下、三度點開才出現且縮排在二度底下；欄位 x 固定', () => {
  const nodes = [n('A', 1, null), n('B', 1, null, { score: 0.9 }), n('A1', 2, 'A'), n('A2', 2, 'A', { score: 0.4 }), n('B1', 2, 'B'), n('A1x', 3, 'A1'), n('A1y', 3, 'A1')]
  const lay = mindLayout(nodes, new Set(), PHONE, 390)
  const by = new Map(lay.items.map((i) => [i.n.id, i]))
  assert.deepEqual(
    lay.items.map((i) => i.n.id),
    ['A', 'A1', 'A2', 'B', 'B1'],
    '沒點開 → 沒有三度',
  )
  // A 的子樹＝兩列二度（88）；A 在這 88 的正中間
  assert.equal(by.get('A1')!.top, 12)
  assert.equal(by.get('A2')!.top, 56)
  assert.equal(by.get('A')!.top + by.get('A')!.h / 2, 12 + 44)
  // B 只有一列二度（44 ≥ 一度的 42）→ B 跟 B1 同高
  assert.equal(by.get('B')!.top + by.get('B')!.h / 2, by.get('B1')!.top + by.get('B1')!.h / 2)
  assert.equal(lay.height, 88 + 44 + 24, '兩棵子樹的高度加上下留白')
  assert.equal(lay.cy, lay.height / 2)
  assert.equal(lay.cx, 12 + 52, '中心方塊靠左')
  assert.equal(by.get('A')!.x, 12 + 104 + 24, '一度在中心方塊右邊一個分支的距離')
  assert.equal(by.get('A1')!.x, 12 + 104 + 24 + 98 + 24)
  for (const i of lay.items) assert.equal(i.side, 1)
  assert.ok(lay.width <= 390, `手機畫布寬 ${lay.width} 不超過螢幕`)

  const open = mindLayout(nodes, new Set(['A1']), PHONE, 390)
  const ob = new Map(open.items.map((i) => [i.n.id, i]))
  assert.deepEqual(
    open.items.map((i) => i.n.id),
    ['A', 'A1', 'A1x', 'A1y', 'A2', 'B', 'B1'],
  )
  assert.equal(ob.get('A1x')!.x, ob.get('A1')!.x + 16, '三度縮排在二度底下')
  assert.equal(ob.get('A1x')!.top, ob.get('A1')!.top + 44, '二度在最上面、三度列在它下面')
  assert.equal(ob.get('A1')!.more, 0)
  assert.equal(by.get('A1')!.more, 2, '沒點開時寫「＋2」')
  assert.deepEqual(mindLayout(nodes, new Set(['A1']), PHONE, 390), open, '同輸入同輸出')
})

test('心智圖（手機現行設定：只往右、每一度一欄）：三度在二度右邊一欄、跟二度同高置中；畫布比 390 寬＝往右捲（使用者 2026-10-19：「要一直往右邊延伸」）', () => {
  const RIGHT: MindOpts = { ...PHONE, indent3: null, slotW: { 1: 98, 2: 104, 3: 108 }, gap: 26 }
  const nodes = [n('A', 1, null), n('A1', 2, 'A'), n('A1x', 3, 'A1'), n('A1y', 3, 'A1'), n('B', 1, null, { score: 0.9 }), n('B1', 2, 'B')]
  const lay = mindLayout(nodes, new Set(['A1']), RIGHT, 390)
  const by = new Map(lay.items.map((i) => [i.n.id, i]))
  for (const i of lay.items) assert.equal(i.side, 1, '只往右')
  assert.equal(by.get('A1x')!.x, by.get('A1')!.x + 104 + 26, '三度在二度右邊一欄')
  assert.equal(by.get('A1y')!.x, by.get('A1x')!.x)
  const mid = (id: string) => by.get(id)!.top + by.get(id)!.h / 2
  assert.equal(mid('A1'), (mid('A1x') + mid('A1y')) / 2, '二度在它的三度中間')
  assert.equal(lay.width, 12 + 104 + 26 + 98 + 26 + 104 + 26 + 108 + 12, '三欄排開的寬度')
  assert.ok(lay.width > 390, '比手機螢幕寬 → 往右捲')
  const br = new Map(mindBranches(lay, RIGHT).map((b) => [b.to, b]))
  const a1 = by.get('A1')!
  assert.ok(br.get('A1x')!.d.startsWith(`M${(a1.x + a1.w).toFixed(1)},`), '三度的分支從二度底線的末端往右長')
})

test('心智圖（桌機、左右都長）：高的那棵先放、往比較矮的那邊放；左邊的字格在中心左邊、三度自己一欄', () => {
  const nodes = [n('A', 1, null), n('B', 1, null, { score: 0.9 }), n('C', 1, null, { score: 0.8 }), n('A1', 2, 'A'), n('A2', 2, 'A'), n('A3', 2, 'A'), n('B1', 2, 'B'), n('C1', 2, 'C'), n('C1x', 3, 'C1')]
  const lay = mindLayout(nodes, new Set(['C1']), DESK, 1200)
  const by = new Map(lay.items.map((i) => [i.n.id, i]))
  assert.equal(by.get('A')!.side, 1, '最高的 A 先放右邊')
  assert.equal(by.get('C')!.side, -1, '第二高的 C（含三度，84）放左邊')
  assert.equal(by.get('B')!.side, -1, 'B 放到比較矮的左邊（右 132 vs 左 84）')
  assert.equal(lay.cx, 600)
  assert.ok(by.get('C')!.x + by.get('C')!.w < 600 - 75, '左邊的一度在中心方塊左邊')
  assert.ok(by.get('A')!.x > 600 + 75)
  assert.equal(by.get('C1x')!.x, by.get('C1')!.x - 24 - 92, '三度自己一欄、再往左')
  assert.equal(by.get('C1x')!.top + by.get('C1x')!.h / 2, by.get('C1')!.top + by.get('C1')!.h / 2, '桌機每一層都在子樹正中間')
})

test('分支：一度從中心方塊的邊出發、二度從一度底線的末端出發、縮排的三度從二度底線的頭鉤下去；越相關越粗、草擬是虛線曲線', () => {
  const nodes = [n('A', 1, null), n('A1', 2, 'A', { via: 'supplier', draft: true }), n('A2', 2, 'A', { type: 'group', score: 0.4 }), n('A1x', 3, 'A1', { draft: true })]
  const lay = mindLayout(nodes, new Set(['A1']), PHONE, 390)
  const by = new Map(lay.items.map((i) => [i.n.id, i]))
  const br = new Map(mindBranches(lay, PHONE).map((b) => [b.to, b]))
  const a = by.get('A')!
  const first = br.get('A')!.d.slice(1).split('L')[0].split(',').map(Number)
  assert.ok(Math.abs(first[0] - (lay.cx + 52)) < 1 && Math.abs(first[1] - lay.cy) < 5, `一度從中心方塊右邊出發（彩帶第一個點 ${first} 在起點的半個寬度內）`)
  assert.ok(br.get('A')!.d.endsWith('Z'), '實心彩帶是封閉的')
  assert.equal(br.get('A')!.cls, 'tw')
  assert.equal(br.get('A1')!.draft, true)
  assert.equal(br.get('A1')!.d, `M${(a.x + a.w).toFixed(1)},${a.y.toFixed(1)} C` + br.get('A1')!.d.split(' C')[1], '草擬＝從一度底線末端出發的虛線曲線')
  assert.equal(br.get('A2')!.cls, 'cluster')
  const x = by.get('A1x')!
  const hook = br.get('A1x')!.d
  assert.ok(hook.startsWith(`M${(by.get('A1')!.x + 8).toFixed(1)},${by.get('A1')!.y.toFixed(1)} C`), '縮排的三度從二度底線的頭往下鉤')
  assert.ok(hook.endsWith(` ${x.x.toFixed(1)},${x.y.toFixed(1)}`), '接到三度底線的頭')
  assert.equal(relevance(1), 1)
  assert.equal(relevance(0), 0.6)
  assert.ok(br.get('A1')!.w > br.get('A1x')!.w, '一度到二度比二度到三度粗')
})

test('彩帶與曲線：S 形的控制點水平出、水平進；鉤形先往下再轉進；彩帶起點寬 w0、終點寬 w1', () => {
  const s = branchCurve(0, 0, 100, 40, 1)
  assert.deepEqual([s[1].y, s[2].y], [0, 40])
  assert.ok(s[1].x > 0 && s[2].x < 100)
  const h = branchCurve(0, 0, 16, 60, 1, true)
  assert.deepEqual(branchCurve(0, 0, 16, 60, 1)[1], { x: 16, y: 0 }, '沒說是鉤形 → 一律 S 形（手機中心到一度的間距很短也一樣）')
  assert.deepEqual([h[1].x, h[2]], [0, { x: 0, y: 60 }])
  const d = taperPath(s, 8, 4, 4)
  const pts = d.slice(1, -1).split('L').map((p) => p.split(',').map(Number))
  assert.equal(pts.length, 10, '4 段 → 5 個取樣點、兩側各 5')
  assert.ok(Math.abs(Math.hypot(pts[0][0] - pts[9][0], pts[0][1] - pts[9][1]) - 8) < 0.2, '起點兩側相距 w0')
  assert.ok(Math.abs(Math.hypot(pts[4][0] - pts[5][0], pts[4][1] - pts[5][1]) - 4) < 0.2, '終點兩側相距 w1')
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
