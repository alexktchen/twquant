import assert from 'node:assert/strict'
import test from 'node:test'
import { pct, pickMarkers, price, quoteBar, sma, trend, type Bar } from '../src/cloud/format.ts'

const bar = (date: string, close: number): Bar => ({ date, open: close, high: close, low: close, close, volume: 1 })

test('T-332 AC8: 漲紅跌綠與格式', () => {
  assert.equal(trend(0.01), 'up')
  assert.equal(trend(-0.01), 'down')
  assert.equal(trend(0), 'flat')
  assert.equal(trend(null), 'flat')
  assert.equal(pct(0.0123), '+1.23%')
  assert.equal(pct(-0.05), '-5.00%')
  assert.equal(pct(null), '—')
  assert.equal(pct(0.2205, 1), '+22.1%')
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

test('T-332 報價列：歷史名單顯示名單那天的 K 棒，長按時換成那一根，漲跌對前一根收盤', () => {
  const bars = [bar('2026-09-23', 100), bar('2026-09-24', 110), bar('2026-10-08', 121)]
  assert.equal(quoteBar(bars, '2026-09-24', null)?.bar.date, '2026-09-24')
  assert.equal(quoteBar(bars, '2026-09-24', null)?.chg, 110 / 100 - 1)
  assert.equal(quoteBar(bars, undefined, null)?.bar.date, '2026-10-08')
  assert.equal(quoteBar(bars, '2026-01-01', null)?.bar.date, '2026-10-08', '找不到日期就用最後一根')
  assert.equal(quoteBar(bars, '2026-09-24', 0)?.bar.date, '2026-09-23', '長按優先')
  assert.equal(quoteBar(bars, '2026-09-24', 0)?.chg, null, '第一根沒有前一根')
  assert.equal(quoteBar(bars, '2026-09-24', 9)?.bar.date, '2026-09-24', '超出範圍的長按不算')
  assert.equal(quoteBar([], '2026-09-24', null), null)
})
