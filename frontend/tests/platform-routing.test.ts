import assert from 'node:assert/strict'
import test from 'node:test'
import { parseRoute, routeHref, platformPages, navPages } from '../src/platform/routes.ts'

test('AC-201-1: deep links retain string IDs and reject malformed routes', () => {
  const stock = parseRoute('#/stocks/0050?date=2026-08-24&run=run-a')
  assert.equal(stock.page, 'stocks')
  assert.equal(stock.entityId, '0050')
  assert.equal(stock.query.get('date'), '2026-08-24')
  assert.equal(parseRoute('#/models/model_a').entityId, 'model_a')
  assert.equal(parseRoute('#/backtests/bt-01').entityId, 'bt-01')
  for (const bad of ['#/missing', '#/stocks/%ZZ', '#/stocks/%2Fetc', '#/models/../secret', '#/overview/extra']) {
    assert.equal(parseRoute(bad).page, 'not-found', bad)
  }
  assert.equal(parseRoute('').page, 'picks') // D-164：首頁是每日選股
  assert.equal(parseRoute('#stock').page, 'stocks')
  assert.equal(parseRoute('#ingest').page, 'operations')
})

test('AC-201-2: serialized navigation preserves explicit context without mutating it', () => {
  const query = new URLSearchParams('date=2026-08-24&model=m1&bucket=watch&page=3&q=金融')
  const href = routeHref('recommendations', undefined, query)
  const restored = parseRoute(href)
  assert.deepEqual([...restored.query], [...query])
  assert.equal(routeHref('models'), '#/models')
  assert.equal(query.get('page'), '3')
  assert.equal(parseRoute(routeHref('stocks', '0050', query)).entityId, '0050')
})

test('AC-201-3: every page keeps a unique valid route; nav shows only the D-164 pages', () => {
  assert.deepEqual(platformPages.map(p => p.id), ['picks', 'overview', 'recommendations', 'strategies', 'watchlists', 'stocks', 'industries', 'insights', 'models', 'backtests', 'operations'])
  assert.deepEqual(navPages.map(p => p.id), ['picks', 'stocks', 'operations'])
  assert.equal(new Set(platformPages.map(p => p.id)).size, platformPages.length)
  for (const page of platformPages) {
    assert.equal(parseRoute(routeHref(page.id)).page, page.id)
    assert(page.label.length > 0 && page.group.length > 0)
  }
})
