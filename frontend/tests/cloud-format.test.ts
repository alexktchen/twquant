import assert from 'node:assert/strict'
import test from 'node:test'
import { pct, pickMarkers, price, sma, trend, type Bar } from '../src/cloud/format.ts'

const bar = (date: string, close: number): Bar => ({ date, open: close, high: close, low: close, close, volume: 1 })

test('T-332 AC8: 漲紅跌綠與格式', () => {
  assert.equal(trend(0.01), 'up')
  assert.equal(trend(-0.01), 'down')
  assert.equal(trend(0), 'flat')
  assert.equal(trend(null), 'flat')
  assert.equal(pct(0.0123), '+1.23%')
  assert.equal(pct(-0.05), '-5.00%')
  assert.equal(pct(null), '—')
  assert.equal(price(1085), '1085')
  assert.equal(price(123.45), '123.5')
  assert.equal(price(45.678), '45.68')
  assert.equal(price(null), '—')
})

test('T-332 AC8: 均線前 n−1 根不補值', () => {
  const bars = [10, 20, 30, 40].map((c, i) => bar(`2026-10-0${i + 1}`, c))
  assert.deepEqual(sma(bars, 3), [
    { time: '2026-10-03', value: 20 },
    { time: '2026-10-04', value: 30 },
  ])
})

test('T-332 AC8: 推薦標記只標在 K 線範圍內、依日期排序', () => {
  const bars = [bar('2026-10-07', 1), bar('2026-10-08', 1)]
  const m = pickMarkers({ QUIET: ['2026-10-08', '2026-01-02'], CORE: ['2026-10-07'] }, { CORE: '大盤精選', QUIET: '潛伏飆股' }, bars)
  assert.deepEqual(m, [
    { time: '2026-10-07', text: '大盤精選', line: 'CORE' },
    { time: '2026-10-08', text: '潛伏飆股', line: 'QUIET' },
  ])
})
