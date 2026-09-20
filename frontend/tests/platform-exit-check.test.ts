import assert from 'node:assert/strict'
import test from 'node:test'
import { parseWatchlist, serializeWatchlist, setEntryPrice, toggleWatch } from '../src/platform/watchlist.ts'
import { ruleVerdict, summarize, type ExitRule } from '../src/platform/exitCheck.ts'

const stop = (pct: number): ExitRule => ({ code: 'E1a', label: '停損', kind: 'stop_loss', triggered: null, note: '', pct })

test('D-116: entries saved before entry_price existed still load, with no price', () => {
  const state = parseWatchlist('{"version":2,"entries":[{"stock_id":"0050","note":"n","run_id":null}]}')
  assert.equal(state.status, 'ok')
  assert.equal(state.entries[0].entry_price, null)
  assert.equal(state.entries[0].stock_id, '0050')
})

test('D-116: entry price round-trips and clearing it stores null, not zero', () => {
  let entries = setEntryPrice(toggleWatch([], '2330'), '2330', 2460.5)
  assert.equal(parseWatchlist(serializeWatchlist(entries)).entries[0].entry_price, 2460.5)
  entries = setEntryPrice(entries, '2330', null)
  assert.equal(parseWatchlist(serializeWatchlist(entries)).entries[0].entry_price, null)
})

test('D-116: a stored entry price that is not a positive number is corruption, not "no price"', () => {
  for (const bad of ['"abc"', '-5', '0', 'true']) {
    const state = parseWatchlist(`{"version":2,"entries":[{"stock_id":"2330","note":"","run_id":null,"entry_price":${bad}}]}`)
    assert.equal(state.status, 'corrupt', bad)
  }
})

test('D-116: stop loss is computed from the local entry price', () => {
  assert.equal(ruleVerdict(stop(0.15), 84.9, 100).state, 'sell')
  assert.equal(ruleVerdict(stop(0.15), 85, 100).state, 'sell') // 恰好 −15% 也觸發（引擎是 <=）
  assert.equal(ruleVerdict(stop(0.15), 85.1, 100).state, 'hold')
})

test('D-116: stop loss without an entry price is unknown, never hold', () => {
  assert.equal(ruleVerdict(stop(0.15), 50, null).state, 'unknown')
  assert.equal(ruleVerdict(stop(0.15), null, 100).state, 'unknown')
})

test('D-116: server-side rules pass through, and null stays unknown', () => {
  const ma = (triggered: boolean | null): ExitRule => ({ code: 'E3', label: '破20日線', kind: 'below_ma', triggered, note: '歷史不足', reference: 100 })
  assert.equal(ruleVerdict(ma(true), 90, null).state, 'sell')
  assert.equal(ruleVerdict(ma(false), 110, null).state, 'hold')
  const unknown = ruleVerdict(ma(null), 110, null)
  assert.equal(unknown.state, 'unknown')
  assert.match(unknown.detail, /歷史不足/)
})

test('D-116: summary counts unknowns separately so "0 sell" cannot hide "could not check"', () => {
  const rules: ExitRule[] = [stop(0.15), { code: 'E3', label: 'x', kind: 'below_ma', triggered: true, note: '', reference: 1 },
    { code: 'E6', label: 'y', kind: 'score_below', triggered: null, note: '分數落後', threshold: 0.7 }]
  assert.deepEqual(summarize(rules, 90, null), { sell: ['E3'], unknown: ['E1a', 'E6'], hold: [] })
})
