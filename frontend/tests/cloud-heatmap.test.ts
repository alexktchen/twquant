import assert from 'node:assert/strict'
import test from 'node:test'
import { heatColor, squarify, tileLines, type Rect } from '../src/cloud/heatmap.ts'

const box: Rect = { x: 0, y: 0, w: 390, h: 420 }
const items = [60, 30, 25, 12, 8, 6, 5, 3, 2, 1, 0.5].map((value, i) => ({ key: `G${i}`, value }))

test('T-355：熱力圖方塊面積與成交比重成正比，鋪滿、不出界、不重疊', () => {
  const tiles = squarify(items, box)
  assert.equal(tiles.length, items.length)
  const total = items.reduce((s, it) => s + it.value, 0)
  const eps = 1e-6
  let area = 0
  for (const t of tiles) {
    const want = (items.find((it) => it.key === t.key)!.value / total) * box.w * box.h
    assert.ok(Math.abs(t.w * t.h - want) < 1e-6 * box.w * box.h, `${t.key} 面積不對`)
    assert.ok(t.x >= -eps && t.y >= -eps && t.x + t.w <= box.w + eps && t.y + t.h <= box.h + eps, `${t.key} 出界`)
    area += t.w * t.h
  }
  assert.ok(Math.abs(area - box.w * box.h) < 1e-6 * box.w * box.h)
  for (const a of tiles)
    for (const b of tiles) {
      if (a === b) continue
      const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)
      const oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y)
      assert.ok(ox <= eps || oy <= eps, `${a.key} 與 ${b.key} 重疊`)
    }
  // 最大的在左上；大方塊不會被切成細長條
  assert.deepEqual([tiles[0].key, tiles[0].x, tiles[0].y], ['G0', 0, 0])
  const big = tiles.slice(0, 4).map((t) => Math.max(t.w / t.h, t.h / t.w))
  assert.ok(Math.max(...big) < 3, `前幾大的長寬比 ${big}`)
})

test('T-355：沒有成交、壞值略過；空的或沒有空間回空；同輸入同結果', () => {
  assert.deepEqual(squarify([], box), [])
  assert.deepEqual(squarify([{ key: 'a', value: 0 }], box), [])
  assert.deepEqual(squarify(items, { ...box, w: 0 }), [])
  const got = squarify([...items, { key: 'nan', value: Number.NaN }, { key: 'neg', value: -1 }], box)
  assert.ok(!got.some((t) => t.key === 'nan' || t.key === 'neg'))
  assert.deepEqual(squarify(items, box), squarify(items, box))
  // 一個族群就是整塊
  assert.deepEqual(squarify([{ key: 'only', value: 3 }], box), [{ key: 'only', ...box }])
})

test('T-355：漲跌模式紅漲綠跌、越多越深；資金模式 1 倍灰、多橘少藍；算不出來是灰', () => {
  const gray = heatColor(null, 'ret')
  assert.equal(heatColor(0, 'ret'), gray)
  assert.equal(heatColor(1, 'ratio'), gray)
  assert.equal(heatColor(Number.NaN, 'ratio'), gray)
  assert.equal(heatColor(0.05, 'ret'), 'rgb(246, 70, 93)')
  assert.equal(heatColor(0.2, 'ret'), 'rgb(246, 70, 93)') // 超過 5% 不再更深
  assert.equal(heatColor(-0.05, 'ret'), 'rgb(14, 203, 129)')
  assert.equal(heatColor(2.5, 'ratio'), 'rgb(255, 122, 0)')
  assert.equal(heatColor(0.4, 'ratio'), 'rgb(61, 120, 210)')
  // 漲 1% 比漲 3% 淡（紅色成分少）
  const red = (c: string) => Number(c.match(/\d+/)![0])
  assert.ok(red(heatColor(0.01, 'ret')) < red(heatColor(0.03, 'ret')))
  assert.notEqual(heatColor(0.001, 'ret'), gray) // 小漲也看得出是紅的
})

test('T-355：方塊太小只上色，夠大才寫字，越高寫越多行', () => {
  assert.equal(tileLines({ x: 0, y: 0, w: 30, h: 80 }), 0)
  assert.equal(tileLines({ x: 0, y: 0, w: 80, h: 20 }), 0)
  assert.equal(tileLines({ x: 0, y: 0, w: 80, h: 30 }), 1)
  assert.equal(tileLines({ x: 0, y: 0, w: 45, h: 80 }), 1)
  assert.equal(tileLines({ x: 0, y: 0, w: 80, h: 50 }), 2)
  assert.equal(tileLines({ x: 0, y: 0, w: 80, h: 60 }), 3)
})
