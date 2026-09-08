import assert from 'node:assert/strict'
import test from 'node:test'
import {
  fromIds,
  parseWatchlist,
  serializeWatchlist,
  setNote,
  toggleWatch,
  WATCH_KEY,
} from '../src/platform/watchlist.ts'

test('AC-211-1: the legacy watchlist stays readable and keeps leading zeroes', () => {
  const state = parseWatchlist('["0050","2330"]')
  assert.equal(state.status, 'legacy')
  assert.deepEqual(state.entries.map(e => e.stock_id), ['0050', '2330'])
  assert.equal(state.entries[0].stock_id, '0050') // Never coerced to a number.
  assert.deepEqual(state.entries.map(e => e.note), ['', ''])
  // The storage key must not change, or existing users silently lose their list.
  assert.equal(WATCH_KEY, 'twquant.watchlist')
})

test('AC-211-1: an empty or absent watchlist is a normal empty state, not corruption', () => {
  for (const raw of [null, '']) {
    const state = parseWatchlist(raw)
    assert.deepEqual([state.status, state.entries, state.detail], ['ok', [], null])
  }
  assert.deepEqual(parseWatchlist('[]').entries, [])
})

test('AC-211-1: corrupt data is reported and never returned as an empty success', () => {
  for (const raw of ['{not json', '{"entries":"nope"}', '[1,2]', '{"entries":[{"note":"x"}]}', '"0050"']) {
    const state = parseWatchlist(raw)
    assert.equal(state.status, 'corrupt', raw)
    assert.match(state.detail ?? '', /未覆寫|無法辨識/)
    assert.deepEqual(state.entries, [])
  }
})

test('AC-211-1: adding, removing and noting round-trips through the stored format', () => {
  let entries = toggleWatch([], '0050', 'run_a')
  entries = setNote(entries, '0050', '等回踩月線')
  entries = toggleWatch(entries, '2330')
  const restored = parseWatchlist(serializeWatchlist(entries))

  assert.equal(restored.status, 'ok')
  assert.deepEqual(restored.entries, [
    { stock_id: '0050', note: '等回踩月線', run_id: 'run_a' },
    { stock_id: '2330', note: '', run_id: null },
  ])
  // Removing a stock removes only that stock.
  assert.deepEqual(toggleWatch(restored.entries, '0050').map(e => e.stock_id), ['2330'])
})

test('AC-211-1: a star toggle from the stock page keeps notes written elsewhere', () => {
  const entries = setNote(toggleWatch([], '0050', 'run_a'), '0050', '觀察營收')
  const afterStar = fromIds(entries, ['0050', '2330'])

  assert.deepEqual(afterStar, [
    { stock_id: '0050', note: '觀察營收', run_id: 'run_a' },
    { stock_id: '2330', note: '', run_id: null },
  ])
  // Dropping a stock through the star drops its note with it, and nothing else.
  assert.deepEqual(fromIds(afterStar, ['2330']), [{ stock_id: '2330', note: '', run_id: null }])
})
