import assert from 'node:assert/strict'
import test from 'node:test'
import { coverageNote, stocksInIndustry } from '../src/platform/industry.ts'

const meta = {
  '0050': { name: '元大台灣50', industry: 'ETF', market: 'TWSE' },
  '2330': { name: '台積電', industry: '半導體', market: 'TWSE' },
  '2317': { name: '鴻海', industry: '電子零組件', market: 'TWSE' },
  '2454': { name: '聯發科', industry: '半導體', market: 'TWSE' },
  '9999': { name: null, industry: null, market: 'TPEX' },
}

test('AC-209-1: an industry lists only its own stocks and keeps string identity', () => {
  assert.deepEqual(stocksInIndustry(meta, '半導體').map(s => s.stock_id), ['2330', '2454'])
  assert.deepEqual(stocksInIndustry(meta, 'ETF'), [{ stock_id: '0050', name: '元大台灣50' }])
  // An industry the classification table does not know is empty, never silently substituted.
  assert.deepEqual(stocksInIndustry(meta, '航運'), [])
  // Unclassified stocks never leak into a named industry.
  assert.equal(stocksInIndustry(meta, '半導體').some(s => s.stock_id === '9999'), false)
})

test('AC-209-1: coverage gaps between the API and the classification table are stated', () => {
  assert.match(coverageNote(12, 12), /API 回報 12 檔.*可對應 12 檔/)
  assert.match(coverageNote(12, 5), /其餘未在分類表中/)
  assert.match(coverageNote(12, 0), /無法列出成分/)
})
