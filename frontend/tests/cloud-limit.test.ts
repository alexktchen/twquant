import assert from 'node:assert/strict'
import test from 'node:test'
import { limitPrices, limitState, pickPrice, quoteLimit, type Quote } from '../src/cloud/live.ts'

test('漲跌停價：昨收 ±10% 再取到升降單位，與 Python 的 costs.limit_up_proxy／limit_down_proxy 逐一相同', () => {
  // 右邊的數字是 Python 版算出來的（backtest/costs.py）
  const cases: [number, number, number][] = [
    [125, 137.5, 112.5],
    [9.5, 10.45, 8.55],
    [95.5, 105, 86],
    [1000, 1100, 900],
    [455, 500, 409.5],
    [48, 52.8, 43.2],
    [361, 397, 325],
    [88.3, 97.1, 79.5],
  ]
  for (const [prev, up, down] of cases) assert.deepEqual(limitPrices(prev), { up, down }, `昨收 ${prev}`)
  assert.deepEqual(limitPrices(null), { up: null, down: null })
  assert.deepEqual(limitPrices(0), { up: null, down: null })
})

test('漲停／跌停判斷：到價才算，缺漲跌停價不判斷', () => {
  assert.equal(limitState(137.5, 137.5, 112.5), 'up')
  assert.equal(limitState(137, 137.5, 112.5), null)
  assert.equal(limitState(112.5, 137.5, 112.5), 'down')
  assert.equal(limitState(null, 137.5, 112.5), null)
  assert.equal(limitState(137.5, null, null), null)
})

const quote = (over: Partial<Quote>): Quote => ({
  sid: '2478',
  date: '2026-10-14',
  time: '09:28:01',
  open: 128.5,
  high: 137.5,
  low: 128,
  last: 136.5,
  prev: 125,
  volume: 4_466_000,
  limit_up: 137.5,
  limit_down: 112.5,
  auction: false,
  ...over,
})
const pick = { close: 125, chg: 0.02, prev: 122.5 }

test('名單在盤中跟著即時價，並留著推薦當天的收盤（使用者 2026-10-14）', () => {
  const live = pickPrice(pick, '2026-10-13', quote({}), '2026-10-14', false)
  assert.equal(live.price, 136.5)
  assert.ok(live.chg !== null && Math.abs(live.chg - (136.5 / 125 - 1)) < 1e-12) // 對昨收＝推薦當天的收盤
  assert.equal(live.rec, 125)
  assert.equal(live.tag, '盤中')
  assert.equal(live.limit, null)
  // 打到漲停：加紅框
  assert.equal(pickPrice(pick, '2026-10-13', quote({ last: 137.5 }), '2026-10-14', false).limit, 'up')
  assert.equal(pickPrice(pick, '2026-10-13', quote({ last: 112.5 }), '2026-10-14', false).limit, 'down')
  // 證交所沒給漲跌停價：由昨收推
  assert.equal(pickPrice(pick, '2026-10-13', quote({ last: 137.5, limit_up: null }), '2026-10-14', false).limit, 'up')
})

test('看過去某天、名單就是今天的、或還沒有今天的報價：用名單那天的收盤，不顯示推薦價', () => {
  const same = { price: 125, chg: 0.02, rec: null, tag: null }
  const pick2 = { close: 125, chg: 0.02, prev: 113.7 } // 113.7 漲停是 125.0
  assert.deepEqual(pickPrice(pick2, '2026-10-08', quote({}), '2026-10-14', true), { ...same, limit: 'up' })
  assert.deepEqual(pickPrice(pick, '2026-10-14', quote({}), '2026-10-14', false), { ...same, limit: null }) // 收盤後名單已是今天
  assert.deepEqual(pickPrice(pick, '2026-10-13', quote({ date: '2026-10-13' }), '2026-10-14', false), { ...same, limit: null }) // 還沒開盤
  assert.deepEqual(pickPrice(pick, '2026-10-13', quote({ last: null }), '2026-10-14', false), { ...same, limit: null }) // 還沒成交
  assert.deepEqual(pickPrice(pick, '2026-10-13', undefined, '2026-10-14', false), { ...same, limit: null })
})

test('追蹤清單：只有今天的即時報價才判斷漲跌停', () => {
  assert.equal(quoteLimit(quote({ last: 137.5 }), '2026-10-14'), 'up')
  assert.equal(quoteLimit(quote({ last: 137.5, date: '2026-10-13' }), '2026-10-14'), null)
  assert.equal(quoteLimit(undefined, '2026-10-14'), null)
})
