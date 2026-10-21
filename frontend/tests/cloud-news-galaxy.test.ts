import assert from 'node:assert/strict'
import test from 'node:test'
import {
  FOCUS_RELATED,
  REF_S,
  clusterLabelAt,
  density,
  depthAlpha,
  galaxySummary,
  hubClass,
  hubFocus,
  hubRadius,
  pickAt,
  placeLabels,
  project,
  type GalaxyData,
  type GalaxyItem,
} from '../src/cloud/news-galaxy.ts'

const near = (a: number, b: number, eps = 1e-9) => assert.ok(Math.abs(a - b) < eps, `${a} ≉ ${b}`)

test('T-418 投影：不轉時原點在畫面中心、z=0 不放大；繞直軸轉 90° 把 x 轉成深度；越近越大', () => {
  const v = { s: 100, x: 200, y: 300 }
  const p0 = project({ x: 0, y: 0, z: 0 }, { yaw: 0, pitch: 0 }, v)
  assert.deepEqual(p0.slice(0, 3), [200, 300, 0])
  near(p0[3], 1)
  const p = project({ x: 1, y: 0, z: 0 }, { yaw: Math.PI / 2, pitch: 0 }, v)
  near(p[0], 200, 1e-6)
  near(p[2], -1, 1e-9) // 轉到後面去了
  assert.ok(p[3] < 1, '遠的變小')
  const q = project({ x: 0, y: 0, z: 1 }, { yaw: 0, pitch: 0 }, v)
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
