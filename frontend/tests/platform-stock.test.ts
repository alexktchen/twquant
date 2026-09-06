import assert from 'node:assert/strict'
import test from 'node:test'
import { DEFAULT_STOCK_TAB, parseStockTab, quoteIdentity, stockTabs } from '../src/platform/stockTabs.ts'
import { parseRoute, routeHref } from '../src/platform/routes.ts'

test('AC-207-1: data tabs are addressable and unknown tabs fall back without losing the page', () => {
  assert.deepEqual(stockTabs.map(tab => tab.id), ['chips', 'fundamentals', 'factors', 'screener'])
  for (const tab of stockTabs) assert.equal(parseStockTab(tab.id), tab.id)
  assert.equal(parseStockTab(null), DEFAULT_STOCK_TAB)
  assert.equal(parseStockTab('does-not-exist'), DEFAULT_STOCK_TAB)
  // The screener stays a separate labelled entry rather than the first screen.
  assert.notEqual(DEFAULT_STOCK_TAB, 'screener')
  assert.equal(stockTabs.find(tab => tab.id === 'screener')?.label, '條件選股')
})

test('AC-207-1: entering a stock keeps the list filters in the restored URL', () => {
  const filters = new URLSearchParams('date=2026-08-24&bucket=top_decile&q=005&model=m1')
  const href = routeHref('stocks', '0050', filters)
  const restored = parseRoute(href)
  assert.equal(restored.entityId, '0050')
  assert.deepEqual([...restored.query], [...filters])
  assert.equal(parseRoute(routeHref('stocks', '0050', new URLSearchParams('tab=factors'))).query.get('tab'), 'factors')
})

test('AC-207-2: quote identity changes with either the stock or the date context', () => {
  assert.equal(quoteIdentity('0050', '2026-08-24'), '0050@2026-08-24')
  assert.notEqual(quoteIdentity('0050', '2026-08-24'), quoteIdentity('0050', '2026-08-25'))
  assert.notEqual(quoteIdentity('0050', '2026-08-24'), quoteIdentity('2330', '2026-08-24'))
  assert.notEqual(quoteIdentity('0050', null), quoteIdentity('0050', '2026-08-24'))
  // Leading zeroes survive as a string; no numeric coercion anywhere in the identity.
  assert.equal(quoteIdentity('0050', null).startsWith('0050@'), true)
})
