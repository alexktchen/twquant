import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  boardDates,
  changeLog,
  exitReasonText,
  remainingSessions,
  resolveDay,
  stockLabel,
  type Board,
  type BoardStrategy,
} from '../src/platform/pickBoard.ts'

const quiet: BoardStrategy = {
  code: 'QUIET',
  name: '潛伏飆股',
  summary: '測試',
  kind: 'slots',
  hold_sessions: 20,
  slots: 15,
  days: [
    { date: '2026-09-22', entered: [{ stock_id: '0050', reason: 'entry' }], exited: [], holdings: [] },
    { date: '2026-09-23', entered: [], exited: [], holdings: [] },
    { date: '2026-09-24', entered: [], exited: [{ stock_id: '2330', reason: 'time_stop', entry_date: '2026-08-26', held_sessions: 20 }], holdings: [] },
  ],
  next: { rebalance: false, buy: [], sell: [] },
}
const board: Board = { as_of: '2026-09-24', generated_at: '', replay_start: '2026-06-01', strategies: [quiet], stocks: { '2330': { name: '台積電' } } }

test('AC-318-1: the shown day defaults to the latest and snaps back to the previous trading day', () => {
  const dates = boardDates(board)
  assert.deepEqual(dates, ['2026-09-22', '2026-09-23', '2026-09-24'])
  assert.equal(resolveDay(dates, null), '2026-09-24')
  assert.equal(resolveDay(dates, '2026-09-30'), '2026-09-24')
  assert.equal(resolveDay(dates, '2026-09-23'), '2026-09-23')
  assert.equal(resolveDay(['2026-09-18', '2026-09-21'], '2026-09-20'), '2026-09-18') // 週末 → 週五
  assert.equal(resolveDay(dates, '2026-05-01'), null)
  assert.equal(resolveDay([], null), null)
})

test('AC-318-2: exit reasons are shown in plain words', () => {
  assert.equal(exitReasonText('time_stop'), '持有期滿')
  assert.equal(exitReasonText('rebalance'), '月換股移出')
  assert.equal(exitReasonText('stop_loss'), '停損')
  assert.equal(exitReasonText('something_new'), 'something_new') // 不認得的原樣顯示，不吞掉
})

test('AC-318-3: slot strategies count down; monthly ones have no fixed end', () => {
  assert.equal(remainingSessions(quiet, 5), 15)
  assert.equal(remainingSessions(quiet, 25), 0)
  assert.equal(remainingSessions({ ...quiet, kind: 'rebalance', hold_sessions: null }, 5), null)
})

test('AC-318-4: the change log lists only days with moves, newest first; ids keep leading zeros', () => {
  assert.deepEqual(changeLog(quiet).map(d => d.date), ['2026-09-24', '2026-09-22'])
  assert.equal(stockLabel(board.stocks, '2330'), '2330 台積電')
  assert.equal(stockLabel(board.stocks, '0050'), '0050')
})
