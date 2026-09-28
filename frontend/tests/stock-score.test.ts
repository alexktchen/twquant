import assert from 'node:assert/strict'
import test from 'node:test'
import { STOCK_SCORE, combineStockScore, pctTo100 } from '../src/stock/stockScore.ts'

test('AC-325-1: the total is the equal-weight mean of the available parts, graded by the thresholds', () => {
  const s = combineStockScore(80, 70, 66)
  assert.equal(s.total, 72)
  assert.equal(s.grade, '偏多')
  assert.deepEqual(s.parts.map((p) => p.label), ['畫線', '籌碼', '基本面'])
  assert.equal(combineStockScore(50, 40, null).total, 45) // A missing part is skipped, not counted as 0.
  assert.equal(combineStockScore(50, 40, null).grade, '中性')
  assert.equal(combineStockScore(10, 30, 20).grade, '偏空')
  assert.equal(combineStockScore(STOCK_SCORE.bull, STOCK_SCORE.bull, STOCK_SCORE.bull).grade, '偏多')
  assert.equal(combineStockScore(STOCK_SCORE.bear, STOCK_SCORE.bear, STOCK_SCORE.bear).grade, '中性')
})

test('AC-325-1: fewer than minParts available parts gives no total instead of a one-part "total"', () => {
  const s = combineStockScore(90, null, null)
  assert.equal(s.total, null)
  assert.equal(s.grade, '資料不足')
  assert.equal(combineStockScore(null, null, null).total, null)
})

test('AC-325-2: category percentiles map to 0-100 and keep missing as null', () => {
  assert.equal(pctTo100(0.634), 63)
  assert.equal(pctTo100(0), 0)
  assert.equal(pctTo100(null), null)
})
