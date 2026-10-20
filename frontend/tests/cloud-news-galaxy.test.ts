import assert from 'node:assert/strict'
import test from 'node:test'
import { REF_S, clusterLabelAt, density, depthAlpha, galaxySummary, hubClass, hubRadius, pickAt, placeLabels, project, type GalaxyData } from '../src/cloud/news-galaxy.ts'

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
