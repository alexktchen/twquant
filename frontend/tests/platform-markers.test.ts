import assert from 'node:assert/strict'
import test from 'node:test'
import { eventId, mapEventsToSessions, markerKind } from '../src/platform/eventMarkers.ts'
import type { StockEvent } from '../src/platform/stockRecommendations.ts'

const base: StockEvent = {
  run_id: 'run_a', model_id: 'm1', stock_id: '0050', as_of: '2026-06-22T18:30:00+08:00',
  published_at: null, mode: 'research', rank: 1, score: 0.5, bucket: 'top_decile',
  reason_ref: null, insight_run_id: null, data_status: 'pending',
}
const at = (day: string, run: string, patch: Partial<StockEvent> = {}): StockEvent =>
  ({ ...base, as_of: `${day}T18:30:00+08:00`, run_id: run, ...patch })
const sessions = ['2026-06-19', '2026-06-22', '2026-06-23', '2026-06-24']

test('AC-208-1: an after-hours publication marks its own session and no others', () => {
  const { groups, pending } = mapEventsToSessions([at('2026-06-22', 'run_a')], sessions)
  assert.equal(groups.length, 1)
  assert.deepEqual([groups[0].session, groups[0].day, groups[0].mapped], ['2026-06-22', '2026-06-22', false])
  assert.deepEqual(pending, [])
  // No recommendation must never invent a marker.
  assert.deepEqual(mapEventsToSessions([], sessions).groups, [])
})

test('AC-208-1: a repeated event id never draws a second marker', () => {
  const duplicate = mapEventsToSessions([at('2026-06-22', 'run_a'), at('2026-06-22', 'run_a')], sessions)
  assert.equal(duplicate.groups.length, 1)
  assert.equal(duplicate.groups[0].events.length, 1)
  assert.equal(eventId(at('2026-06-22', 'run_a')), 'run_a:0050')
})

test('AC-208-1: same-day events group together and keep every event for expansion', () => {
  const { groups } = mapEventsToSessions(
    [at('2026-06-22', 'run_a'), at('2026-06-22', 'run_b', { insight_run_id: 'ins_1' })],
    sessions,
  )
  assert.equal(groups.length, 1)
  assert.deepEqual(groups[0].events.map(eventId), ['run_a:0050', 'run_b:0050'])
  assert.equal(groups[0].kind, 'insight_pick') // An insight pick outranks a raw candidate.
  assert.equal(markerKind(at('2026-06-22', 'run_c', { bucket: 'watch' })), 'watch')
  assert.equal(markerKind(at('2026-06-22', 'run_d', { bucket: 'excluded' })), 'excluded')
})

test('AC-208-2: a non-trading publication day moves forward, never onto the previous bar', () => {
  const { groups } = mapEventsToSessions([at('2026-06-20', 'run_h')], sessions)
  assert.equal(groups[0].session, '2026-06-22') // The next session, not 06-19.
  assert.equal(groups[0].day, '2026-06-20')
  assert.equal(groups[0].mapped, true)
})

test('AC-208-2: an event with no later session stays pending instead of being plotted', () => {
  const { groups, pending } = mapEventsToSessions([at('2026-06-30', 'run_f')], sessions)
  assert.deepEqual(groups, [])
  assert.deepEqual(pending.map(eventId), ['run_f:0050'])
})

test('AC-208-2: weekly bars absorb the day into their own week and keep the exact day', () => {
  const weeks = ['2026-06-15', '2026-06-22']
  const { groups } = mapEventsToSessions([at('2026-06-24', 'run_w')], weeks, { weekly: true })
  assert.equal(groups[0].session, '2026-06-22') // The week containing 06-24.
  assert.equal(groups[0].day, '2026-06-24') // Detail still shows the precise publication day.
  assert.deepEqual(mapEventsToSessions([at('2026-06-01', 'run_e')], weeks, { weekly: true }).pending.map(eventId), ['run_e:0050'])
})
