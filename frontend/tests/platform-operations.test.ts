import assert from 'node:assert/strict'
import test from 'node:test'
import {
  DEFAULT_OPERATIONS_TAB,
  operationsTabs,
  parseOperationsTab,
} from '../src/platform/operationsTabs.ts'
import { parseRoute, routeHref } from '../src/platform/routes.ts'

test('AC-212-1: every legacy operations area is still reachable as its own tab', () => {
  assert.deepEqual(operationsTabs.map(tab => tab.id), ['data', 'gaps', 'jobs'])
  for (const tab of operationsTabs) assert.equal(parseOperationsTab(tab.id), tab.id)
  assert.equal(parseOperationsTab(null), DEFAULT_OPERATIONS_TAB)
  assert.equal(parseOperationsTab('training'), DEFAULT_OPERATIONS_TAB)
})

test('AC-212-1: the active tab lives in the URL so a poll cannot reset it', () => {
  const href = routeHref('operations', undefined, new URLSearchParams('tab=jobs'))
  const restored = parseRoute(href)
  assert.equal(restored.page, 'operations')
  assert.equal(parseOperationsTab(restored.query.get('tab')), 'jobs')
  // The legacy #ingest entry point still resolves to the operations page.
  assert.equal(parseRoute('#ingest').page, 'operations')
})
