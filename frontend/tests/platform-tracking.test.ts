import assert from 'node:assert/strict'
import { test } from 'node:test'
import { neighbourDays, streakReason, streakText } from '../src/platform/tracking.ts'

const dates = ['2026-09-23', '2026-09-22', '2026-09-21', '2026-09-18']

test('AC-300-4/AC-301-4: prev/next jump between saved days, not calendar days', () => {
  assert.deepEqual(neighbourDays(dates, '2026-09-21'), { prev: '2026-09-18', next: '2026-09-22' })
  assert.deepEqual(neighbourDays(dates, '2026-09-23'), { prev: '2026-09-22', next: null })
  assert.deepEqual(neighbourDays(dates, '2026-09-18'), { prev: null, next: '2026-09-21' })
})

test('AC-300-4: a picked day without a snapshot still finds both neighbours', () => {
  assert.deepEqual(neighbourDays(dates, '2026-09-19'), { prev: '2026-09-18', next: '2026-09-21' })
  assert.deepEqual(neighbourDays([], '2026-09-19'), { prev: null, next: null })
  assert.deepEqual(neighbourDays(dates, null), { prev: null, next: null })
})

test('AC-300-2: an open streak is printed as a floor and says why', () => {
  assert.equal(streakText(3, null), '3 天')
  assert.equal(streakText(20, 'lookback'), '≥20 天')
  assert.match(streakReason('lookback', 20, null), /只往回看 20 份快照/)
  assert.match(streakReason('history', 20, '2026-09-18'), /最早一份快照（2026-09-18）/)
  assert.match(streakReason('failed', 20, null), /計算失敗/)
})
