import assert from 'node:assert/strict'
import test from 'node:test'
import { QUADRANT_NAME, YI, extent, instText, md, quadrant, radius, scaler, symlog, ticks, yiText, type InstPoint } from '../src/cloud/quadrant.ts'

const pt = (x: number, y: number, trail: InstPoint['trail'] = []): InstPoint => ({ x: x * YI, y: y * YI, net: 0, f5: null, t5: null, d5: null, trail })

test('T-358 象限：右上持續加碼、右下買氣降溫、左上賣壓收斂、左下持續減碼', () => {
  assert.equal(QUADRANT_NAME[quadrant(840, 120)], '持續加碼')
  assert.equal(QUADRANT_NAME[quadrant(300, -20)], '買氣降溫')
  assert.equal(QUADRANT_NAME[quadrant(-50, 10)], '賣壓收斂')
  assert.equal(QUADRANT_NAME[quadrant(-50, -10)], '持續減碼')
  assert.equal(quadrant(0, 0), 'add') // 剛好 0 算流入、加速那一邊
})

test('對稱對數軸：0 在中間、正負對稱、越大越壓縮（幾億和幾百億畫得在一起）', () => {
  assert.equal(symlog(0), 0)
  assert.equal(symlog(9), 1)
  assert.equal(symlog(-99), -2)
  assert.ok(symlog(1000) - symlog(100) < symlog(100) - symlog(1) && symlog(2) > symlog(1))
  const ext = extent([pt(800, 50), pt(-3, -200, [{ d: '2026-10-08', x: -1200 * YI, y: 5 * YI }])])
  assert.deepEqual(ext, { x: 1200, y: 200 }) // 軌跡也算進範圍
  assert.deepEqual(extent([pt(0.2, -0.1)]), { x: 1, y: 1 }) // 最小 ±1 億
  const s = scaler(ext, { left: 10, top: 20, width: 200, height: 100 })
  assert.equal(s.x(0), 110)
  assert.equal(s.y(0), 70)
  assert.ok(s.x(1200) < 210 && s.x(1200) > 200) // 留邊
  assert.ok(Math.abs(s.x(50) - 110 - (110 - s.x(-50))) < 1e-9)
  assert.ok(s.y(10) < 70 && s.y(-10) > 70) // 加速往上
})

test('刻度：1、5、10、50…到範圍為止；太多只留 1、10、100…', () => {
  assert.deepEqual(ticks(30), [1, 5, 10])
  assert.deepEqual(ticks(60), [1, 5, 10, 50])
  assert.deepEqual(ticks(2000), [1, 10, 100, 1000])
  assert.deepEqual(ticks(0.5), [])
})

test('泡泡面積跟成交比重成正比，太小的不小於最小半徑', () => {
  assert.equal(radius(0.04, 0.04, 4, 20), 20)
  assert.equal(radius(0.01, 0.04, 4, 20), 10)
  assert.equal(radius(0.0001, 0.04, 4, 20), 4)
  assert.equal(radius(0, 0.04, 4, 20), 4)
  assert.equal(radius(Number.NaN, 0.04, 4, 20), 4)
})

test('文字：億、日期、一句話講法人', () => {
  assert.equal(yiText(840.1 * YI), '+840.1 億')
  assert.equal(yiText(-3.24 * YI), '−3.2 億')
  assert.equal(yiText(0.5 * YI), '+0.50 億')
  assert.equal(md('2026-10-14'), '10/14')
  const p = { ...pt(840.1, 12.3), f5: 700 * YI, t5: 100.1 * YI, d5: 40 * YI }
  assert.equal(instText(p), '法人近 5 日 +840.1 億（外資 +700.0 億、投信 +100.1 億、自營商 +40.0 億）・加速 +12.3 億/天・持續加碼')
  assert.equal(instText(pt(-2, 1)), '法人近 5 日 −2.0 億・加速 +1.0 億/天・賣壓收斂')
})
