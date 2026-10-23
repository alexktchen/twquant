import assert from 'node:assert/strict'
import test from 'node:test'
import {
  DRAG_RAD,
  FOCUS_RELATED,
  REF_S,
  clusterFocus,
  clusterLabelAt,
  density,
  depthAlpha,
  dockInspector,
  dragRotate,
  ease,
  galaxySummary,
  hubClass,
  hubFocus,
  hubRadius,
  labelAt,
  pickAt,
  pivotScreenY,
  placeLabels,
  project,
  qFromYawPitch,
  qSame,
  qSlerp,
  type GalaxyData,
  type GalaxyItem,
} from '../src/cloud/news-galaxy.ts'

const near = (a: number, b: number, eps = 1e-9) => assert.ok(Math.abs(a - b) < eps, `${a} ≉ ${b}`)

test('T-418 投影：不轉時原點在畫面中心、z=0 不放大；繞直軸轉 90° 把 x 轉成深度；越近越大', () => {
  const v = { s: 100, x: 200, y: 300 }
  const p0 = project({ x: 0, y: 0, z: 0 }, qFromYawPitch(0, 0), v)
  assert.deepEqual(p0.slice(0, 3), [200, 300, 0])
  near(p0[3], 1)
  const p = project({ x: 1, y: 0, z: 0 }, qFromYawPitch(Math.PI / 2, 0), v)
  near(p[0], 200, 1e-6)
  near(p[2], -1, 1e-9) // 轉到後面去了
  assert.ok(p[3] < 1, '遠的變小')
  const q = project({ x: 0, y: 0, z: 1 }, qFromYawPitch(0, 0), v)
  assert.ok(q[3] > 1 && q[2] === 1, '近的變大')
})

test('亮度與大小：遠的淡、縮小時淡（手機太亮的修正）、亮點半徑有上限；顏色類', () => {
  near(depthAlpha(-1.15), 0.4)
  near(depthAlpha(1.15), 1)
  assert.equal(density(REF_S), 1)
  assert.equal(density(REF_S * 3), 1, '放大不會更亮')
  assert.ok(density(159) < 0.6, '手機一開約 0.5')
  assert.equal(density(10), 0.32, '下限')
  assert.equal(hubRadius(466), hubRadius(81), '「AI」466 則跟 81 則一樣大（上限）')
  assert.ok(hubRadius(4) < hubRadius(25))
  assert.deepEqual(['tw', 'us', 'company', 'group', 'theme'].map(hubClass), ['tw', 'abroad', 'abroad', 'cluster', 'cluster'])
})

test('標籤避讓：照優先序放、重疊的不放、夾在畫面內（下緣留給按鈕）', () => {
  const got = placeLabels(
    [
      { key: 'a', x: 100, y: 100, w: 80, h: 20 },
      { key: 'b', x: 110, y: 105, w: 80, h: 20 },
      { key: 'c', x: 100, y: 160, w: 80, h: 20 },
      { key: 'd', x: -50, y: 900, w: 80, h: 20 },
    ],
    400,
    600,
  )
  assert.deepEqual([...got.keys()], ['a', 'c', 'd'], 'b 跟 a 重疊，不放')
  assert.deepEqual(got.get('d'), { x: 44, y: 600 - 10 - 48 })
})

test('主題標籤往畫面外側推、點選先亮點再光點、摘要句', () => {
  const at = clusterLabelAt([300, 200, 0, 1], { x: 200, y: 200 })
  assert.deepEqual(at, { x: 354, y: 200 })
  const hubs = [{ id: 'tw:2330', p: [100, 100, 0, 1] as [number, number, number, number], r: 30 }]
  const items = [{ id: 'n1', p: [104, 100, 0, 1] as [number, number, number, number] }, { id: 'n2', p: [300, 300, 0, 1] as [number, number, number, number] }]
  assert.deepEqual(pickAt(105, 100, hubs, items), { kind: 'hub', id: 'tw:2330' })
  assert.deepEqual(pickAt(298, 301, hubs, items), { kind: 'item', id: 'n2' })
  assert.equal(pickAt(500, 500, hubs, items), null)
  const d = { built: '2026-10-20T12:27:19Z', days: 7, items: new Array(1500).fill(null), clusters: new Array(16).fill(null), hubs: [], links: 0 } as unknown as GalaxyData
  assert.equal(galaxySummary(d), '近 7 天 1,500 則・16 個主題・10/20 20:27 更新')
})

const gi = (id: string, k: number, t: string, m: string[], src = '中央社', ev: string | null = null): GalaxyItem => ({
  id, x: 0, y: 0, z: 0, k, title: `新聞${id}`, src, kind: 'tw', tr: false, t, url: `https://example.test/${id}`, ev, m, ml: [],
})
const gh = (id: string, label: string, type: string, n: number) => ({ id, label, type, sub: type === 'tw' ? id.slice(3) : null, n, x: 0, y: 0, z: 0 })
const focusData: GalaxyData = {
  built: '2026-10-21T11:49:00Z',
  days: 7,
  links: 0,
  clusters: [
    { k: 1, name: '台積電產能布局', n: 3, x: 0, y: 0, z: 0, top: [] },
    { k: 2, name: 'AI投資風暴', n: 2, x: 0, y: 0, z: 0, top: [] },
  ],
  hubs: [gh('tw:2330', '台積電', 'tw', 5), gh('us:NVDA', '輝達', 'us', 3), gh('theme:ai', 'AI', 'theme', 9), gh('tw:2454', '聯發科', 'tw', 1)],
  items: [
    gi('a', 1, '2026-10-21T01:00:00Z', ['tw:2330', 'us:NVDA'], '中央社', 'e1'),
    gi('b', 1, '2026-10-21T03:00:00Z', ['tw:2330', 'us:NVDA', 'theme:ai'], '經濟日報', 'e1'),
    gi('c', 1, '2026-10-20T03:00:00Z', ['tw:2330', 'tw:2454'], '自由財經', 'e2'),
    gi('d', 2, '2026-10-21T05:00:00Z', ['tw:2330', 'theme:ai', 'tw:2330'], '鉅亨網', 'e3'),
    gi('e', 2, '2026-10-21T06:00:00Z', ['us:NVDA'], '中央社', 'e4'),
  ],
}

test('單獨看：提到它的新聞照主題分組、同一件事只留最新一則並記幾家、常一起出現照則數排', () => {
  const f = hubFocus(focusData, 'tw:2330')!
  assert.equal(f.hub.label, '台積電')
  assert.equal(f.total, 4, 'e 沒提到台積電')
  assert.equal(f.shown, 4)
  assert.deepEqual(f.groups.map((g) => g.name), ['台積電產能布局', 'AI投資風暴'], '列數多的主題在前')
  const g1 = f.groups[0].rows
  assert.deepEqual(g1.map((r) => r.item.id), ['b', 'c'], 'a、b 同一件事只留新的 b；組內新的在前')
  assert.deepEqual(g1[0].srcs, ['經濟日報', '中央社'], '同一件事的兩家都記著')
  assert.deepEqual(g1.map((r) => r.n), [2, 1])
  assert.deepEqual(g1[0].others.map((h) => h.label), ['輝達', 'AI'], '不列自己')
  assert.deepEqual(f.groups[1].rows[0].others.map((h) => h.label), ['AI'], '重複提到自己不算')
  assert.deepEqual(f.related.map((r) => [r.hub.label, r.n]), [['AI', 2], ['輝達', 2], ['聯發科', 1]], '同則數時亮點大的在前')
  assert.ok(FOCUS_RELATED >= 3)
})

test('單獨看：給第二個亮點只留同時提到兩個的，列的其他亮點不重複那一個；找不到 → null', () => {
  const f = hubFocus(focusData, 'tw:2330', 'us:NVDA')!
  assert.equal(f.total, 4, '總數不變')
  assert.equal(f.shown, 2)
  assert.deepEqual(f.groups.flatMap((g) => g.rows.map((r) => r.item.id)), ['b'])
  assert.deepEqual(f.groups[0].rows[0].others.map((h) => h.label), ['AI'])
  assert.equal(f.related.length, 3, '常一起出現照全部算，不跟著篩')
  assert.equal(hubFocus(focusData, 'tw:9999'), null)
})

test('不迷航：轉動中心那顆永遠在畫面中央、轉了也不動；其他的繞著它轉', () => {
  const v = { s: 100, x: 200, y: 300 }
  const c = { x: 0.6, y: -0.4, z: 0.3 }
  for (const r of [qFromYawPitch(0, 0), qFromYawPitch(1.2, -0.5), qFromYawPitch(-2.9, 0.7)]) {
    const p = project(c, r, v, c)
    near(p[0], 200)
    near(p[1], 300)
  }
  const a = project({ x: 0.6, y: -0.4, z: 0.3 + 0.5 }, qFromYawPitch(0, 0), v, c)
  const b = project({ x: 0, y: 0, z: 0.5 }, qFromYawPitch(0, 0), v)
  assert.deepEqual(a, b, '換中心＝整團平移')
})

test('不迷航：滑回去跟幀率無關；回到一開始走短的那邊；拖曳靈敏度', () => {
  near(ease(0, 10, 0), 0)
  near(ease(0, 10, 10), 10, 1e-6)
  near(ease(ease(0, 10, 0.05), 10, 0.05), ease(0, 10, 0.1), 1e-9) // 兩幀 0.05 秒＝一幀 0.1 秒
  const home = qFromYawPitch(0.5, -0.3)
  const far = qFromYawPitch(0.5 + 6 * Math.PI, -0.3) // 自轉了三圈
  near(qSame(far, home), 1, 1e-9) // 同一個轉向：不用倒轉回去
  const a = qFromYawPitch(2.8, 1), b = qFromYawPitch(-2.8, -1)
  near(qSame(qSlerp(a, b, 0), a), 1, 1e-9)
  near(qSame(qSlerp(a, b, 1), b), 1, 1e-9)
  let r = a
  for (let i = 0; i < 120; i++) r = qSlerp(r, home, 1 - Math.exp(-8 / 60)) // 60fps 兩秒
  assert.ok(qSame(r, home) > 1 - 1e-7, '回得去')
  assert.ok(200 * DRAG_RAD < Math.PI / 3, '手機滑 200px 轉不到 60°（原本 92°）')
})

test('上下左右一直轉：舊的兩角度算法照舊；往下拖一路過頭頂不卡、轉一整圈回原樣；翻過去後往右拖還是往右', () => {
  const v = { s: 100, x: 0, y: 0 }
  // 四元數跟原本「先繞直軸 yaw、再繞橫軸 pitch」的公式一樣
  for (const [yaw, pitch] of [[0.5, -0.3], [2, 1.1], [-1, 0.4]]) {
    const o = { x: 0.3, y: -0.7, z: 0.5 }
    const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch)
    const x1 = o.x * cy + o.z * sy, z1 = -o.x * sy + o.z * cy
    const y1 = o.y * cp - z1 * sp, z2 = o.y * sp + z1 * cp
    const p = project(o, qFromYawPitch(yaw, pitch), v)
    near(p[2], z2, 1e-9)
    near(p[0] / p[3], x1 * 100, 1e-9)
    near(p[1] / p[3], y1 * 100, 1e-9)
  }
  const front = { x: 0, y: 0, z: 1 }
  const start = qFromYawPitch(0, 0)
  assert.ok(project(front, dragRotate(start, 0.2, 0), v)[0] > 0, '往右拖，前面的往右')
  assert.ok(project(front, dragRotate(start, 0, 0.2), v)[1] > 0, '往下拖，前面的往下')
  let r = start
  const step = 10 * DRAG_RAD // 每次拖 10px
  const n = Math.round(Math.PI / step)
  for (let i = 0; i < n; i++) r = dragRotate(r, 0, step)
  near(project(front, r, v)[2], -1, 1e-2) // 轉了 180°：前面的到了後面，沒有卡住
  const back = { x: 0, y: 0, z: -1 } // 現在面對我們的是原本背面那顆
  near(project(back, r, v)[2], 1, 1e-2)
  assert.ok(project(back, dragRotate(r, 0.2, 0), v)[0] > 0, '翻過頭頂之後，往右拖還是面前的往右（不會左右顛倒）')
  assert.ok(project(back, dragRotate(r, 0, 0.2), v)[1] > 0, '翻過頭頂之後，往下拖還是面前的往下')
  for (let i = 0; i < n; i++) r = dragRotate(r, 0, step)
  near(qSame(r, start), 1, 1e-3) // 一整圈回原樣
  for (let i = 0; i < 4 * n; i++) r = dragRotate(r, step, 0)
  near(qSame(r, start), 1, 1e-3) // 左右轉兩圈也回原樣
})

test('不迷航：手機放不下右邊的檢視框 → 框貼下緣、選取那顆放在框上方空間的正中；桌機照舊在正中央', () => {
  assert.equal(dockInspector(390, 320), true)
  assert.equal(dockInspector(768, 320), false, '平板直放還放得下')
  assert.equal(dockInspector(1330, 320), false)
  assert.equal(pivotScreenY(760, null), 380)
  assert.equal(pivotScreenY(523, 270), (523 - 50 - 270) / 2)
  assert.equal(pivotScreenY(380, 330), 40, '框太高也不會貼到最上面')
})

// ── T-436 搜尋與牽連（使用者 2026-10-22：「下一個字來搜尋就出現他是怎麼牽連在一起的」）──
import { PATH_MAX_HOPS, SEARCH_MAX, WEB_MAX, bestHub, hubGraph, hubMatch, hubPath, hubWeb, parseQuery, searchGalaxy } from '../src/cloud/news-galaxy.ts'

test('T-436 AC1 拆查詢：半形／全形空白、頓號分開，最多兩個詞；合度：整個一樣 → 開頭 → 包含，代號也算', () => {
  assert.deepEqual(parseQuery('  台積電　輝達 '), ['台積電', '輝達'])
  assert.deepEqual(parseQuery('台積電、輝達、AI'), ['台積電', '輝達'], '最多兩個')
  assert.deepEqual(parseQuery('   '), [])
  const h = focusData.hubs[0]
  assert.equal(hubMatch(h, '台積電'), 0)
  assert.equal(hubMatch(h, '2330'), 0, '代號整個一樣')
  assert.equal(hubMatch(h, '23'), 1, '代號開頭')
  assert.equal(hubMatch(h, '積電'), 2)
  assert.equal(hubMatch(h, '聯發'), null)
  assert.equal(hubMatch(h, ''), null)
  assert.equal(hubMatch(focusData.hubs[2], 'ai'), 0, '英文不分大小寫')
})

test('T-436 AC2 打字就列：亮點先（整個一樣贏過則數多的）、不夠補標題有這個詞的新聞（新的在前）；兩個詞只列第一個詞', () => {
  const d: GalaxyData = { ...focusData, hubs: [{ ...focusData.hubs[2], id: 'theme:aiserver', label: 'AI伺服器', n: 20 }, ...focusData.hubs] }
  const hits = searchGalaxy(d, 'AI')
  assert.deepEqual(hits.map((x) => (x.kind === 'hub' ? x.hub.label : x.item.id)), ['AI', 'AI伺服器'], '整個一樣的在前，即使它則數少')
  assert.deepEqual(searchGalaxy(d, '2330').map((x) => x.kind === 'hub' && x.hub.id), ['tw:2330'])
  const byTitle = searchGalaxy(d, '新聞')
  assert.deepEqual(byTitle.map((x) => (x.kind === 'item' ? x.item.id : '?')), ['e', 'd', 'b', 'a', 'c'], '沒有亮點叫「新聞」→ 列標題有的，新的在前')
  assert.deepEqual(searchGalaxy(d, '新聞', 2).length, 2, '照上限')
  assert.deepEqual(searchGalaxy(d, 'ai 台積').map((x) => x.kind === 'hub' && x.hub.label), ['AI', 'AI伺服器'], '第二個詞是路徑的另一端，不混進來')
  assert.deepEqual(searchGalaxy(d, ''), [])
  assert.equal(bestHub(d, 'ai')?.label, 'AI')
  assert.equal(bestHub(d, '伺服')?.label, 'AI伺服器')
  assert.equal(bestHub(d, '鴻海'), null)
  assert.ok(SEARCH_MAX >= 5 && WEB_MAX >= 8 && PATH_MAX_HOPS >= 3)
})

test('T-436 AC3 牽連：同一則一起被提到的算一條（重複提到只算一次）；一個亮點列最強的幾個、再畫那些亮點彼此的線', () => {
  const g = hubGraph(focusData)
  assert.equal(g.get('tw:2330')!.get('us:NVDA'), 2, 'a、b 兩則')
  assert.equal(g.get('us:NVDA')!.get('tw:2330'), 2, '對稱')
  assert.equal(g.get('tw:2330')!.get('theme:ai'), 2, 'd 重複提到台積電只算一次')
  assert.equal(g.get('theme:ai')!.get('tw:2454'), undefined)
  const w = hubWeb(focusData, g, 'tw:2330')!
  assert.deepEqual(w.first.map((f) => [f.hub.label, f.n]), [['AI', 2], ['輝達', 2], ['聯發科', 1]], '跟單獨看的「常一起出現」同一個順序')
  assert.deepEqual(w.edges.filter((e) => e.deg === 1).map((e) => e.b), ['theme:ai', 'us:NVDA', 'tw:2454'])
  assert.deepEqual(w.edges.filter((e) => e.deg === 2), [{ a: 'theme:ai', b: 'us:NVDA', n: 1, deg: 2 }], 'AI 跟輝達彼此也牽連（b 那則）')
  assert.equal(hubWeb(focusData, g, 'tw:2330', 1)!.edges.length, 1, '只留一個就沒有彼此的線')
  assert.equal(hubWeb(focusData, g, 'tw:9999'), null)
})

test('T-436 AC4 兩個之間怎麼牽：步數少優先、同步數挑粗的；太遠或不相連 → null；自己到自己只有自己', () => {
  const g = hubGraph(focusData)
  const p = hubPath(focusData, g, 'tw:2454', 'us:NVDA')!
  assert.deepEqual(p.hubs.map((h) => h.label), ['聯發科', '台積電', '輝達'])
  assert.deepEqual(p.n, [1, 2])
  const direct = hubPath(focusData, g, 'us:NVDA', 'theme:ai')!
  assert.deepEqual(direct.hubs.map((h) => h.label), ['輝達', 'AI'], '直接有線（1 則）就不繞台積電（2 則＋2 則）')
  assert.deepEqual(direct.n, [1])
  assert.equal(hubPath(focusData, g, 'tw:2454', 'us:NVDA', 1), null, '限 1 步就到不了')
  const lonely: GalaxyData = { ...focusData, hubs: [...focusData.hubs, gh('tw:1101', '台泥', 'tw', 3)] }
  assert.equal(hubPath(lonely, hubGraph(lonely), 'tw:2330', 'tw:1101'), null, '沒有新聞把兩個連在一起')
  assert.equal(hubPath(focusData, g, 'tw:2330', 'tw:9999'), null)
  const self = hubPath(focusData, g, 'tw:2330', 'tw:2330')!
  assert.deepEqual([self.hubs.length, self.n], [1, []])
})

// ── T-437 證據句（使用者 2026-10-23：「如果我想要用在工作上我要怎麼定位那段字？」）──
import { EVIDENCE_BATCH, evidenceNeeded, evidenceParts } from '../src/cloud/news-galaxy.ts'

test('T-437 AC3 標題與證據句裡的名字標色：最早出現的先、同位置長的先、沒出現就整段普通字', () => {
  const marks = [{ label: '台積電', cls: 'tw' }, { label: '輝達', cls: 'abroad' }, { label: '台積', cls: 'tw' }]
  assert.deepEqual(evidenceParts('輝達下單台積電 2 奈米', marks), [{ t: '輝達', cls: 'abroad' }, { t: '下單' }, { t: '台積電', cls: 'tw' }, { t: ' 2 奈米' }])
  assert.deepEqual(evidenceParts('聯發科法說', marks), [{ t: '聯發科法說' }])
  assert.deepEqual(evidenceParts('台積ADR', marks), [{ t: '台積', cls: 'tw' }, { t: 'ADR' }], '只有短的出現就標短的')
  assert.deepEqual(evidenceParts('', marks), [])
})

test('T-437 AC4 只補問沒問過的：任一個實體沒問過那則就要問', () => {
  const fetched = new Map([['tw:2330', new Set(['a', 'b'])]])
  assert.deepEqual(evidenceNeeded(['a', 'b', 'c'], ['tw:2330'], fetched), ['c'])
  assert.deepEqual(evidenceNeeded(['a', 'b'], ['tw:2330', 'us:NVDA'], fetched), ['a', 'b'], '換成兩個都提到：輝達還沒問過')
  assert.deepEqual(evidenceNeeded([], ['tw:2330'], fetched), [])
  assert.ok(EVIDENCE_BATCH >= 20)
})

test('T-441 AC1 點到標籤：點在框內（含手指留的邊）就算，疊在一起取後放的；框外 → null', () => {
  const boxes = [
    { key: 'c1', x: 100, y: 100, w: 80, h: 20 },
    { key: 'h:tw:2330', x: 130, y: 105, w: 60, h: 20 },
  ]
  assert.equal(labelAt(70, 100, boxes), 'c1')
  assert.equal(labelAt(130, 105, boxes), 'h:tw:2330', '重疊的地方取後放的')
  assert.equal(labelAt(80, 114, boxes, 6), 'c1', '下緣外 4px 仍在手指邊內')
  assert.equal(labelAt(80, 130, boxes, 6), null)
  assert.equal(labelAt(0, 0, []), null)
})

test('T-441 AC2 單獨看主題：只列這個主題的新聞、同一件事只留最新一則；常一起出現＝這些新聞提到的亮點；給亮點只留也提到它的；找不到 → null', () => {
  const f = clusterFocus(focusData, 1)!
  assert.equal(f.cluster.name, '台積電產能布局')
  assert.equal(f.total, 3)
  assert.equal(f.shown, 3)
  assert.deepEqual(f.groups.map((g) => g.name), ['台積電產能布局'], '只有自己這一組')
  assert.deepEqual(f.groups[0].rows.map((r) => [r.item.id, r.n]), [['b', 2], ['c', 1]], 'a、b 同一件事只留新的 b')
  assert.deepEqual(f.groups[0].rows[0].others.map((h) => h.label), ['台積電', '輝達', 'AI'], '主題沒有中心亮點，提到的全列')
  assert.deepEqual(f.related.map((r) => [r.hub.label, r.n]), [['台積電', 3], ['輝達', 2], ['AI', 1], ['聯發科', 1]])
  const w = clusterFocus(focusData, 1, 'tw:2454')!
  assert.equal(w.total, 3, '總數不變')
  assert.deepEqual(w.groups.flatMap((g) => g.rows.map((r) => r.item.id)), ['c'])
  assert.deepEqual(w.groups[0].rows[0].others.map((h) => h.label), ['台積電'], '不重複列選的那個')
  assert.equal(clusterFocus(focusData, 99), null)
})
