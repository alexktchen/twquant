import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  boardDates,
  changeLog,
  changeText,
  equitySummary,
  resolveTab,
  tradeStats,
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

test('AC-319-1: change text is signed and toned red-up / green-down; missing stays hidden', () => {
  assert.deepEqual(changeText(0.1234), { text: '+12.3%', tone: 'up' })
  assert.deepEqual(changeText(-0.05), { text: '-5.0%', tone: 'down' })
  assert.deepEqual(changeText(0.00001), { text: '0.0%', tone: 'flat' })
  assert.equal(changeText(null), null)
  assert.equal(changeText(undefined), null)
})

test('AC-319-2: equity summary stops at the shown day and tracks the worst drawdown', () => {
  const eq = [
    { date: '2026-09-01', value: 100 },
    { date: '2026-09-02', value: 120 },
    { date: '2026-09-03', value: 90 },
    { date: '2026-09-04', value: 110 },
  ]
  const s = equitySummary(eq, 100, '2026-09-03')
  assert.ok(s)
  assert.equal(s.pnl, -10)
  assert.equal(s.dayPnl, -30)
  assert.ok(Math.abs(s.maxDrawdown - (90 / 120 - 1)) < 1e-12)
  assert.equal(equitySummary(eq, 100, '2026-08-31'), null)
  assert.equal(equitySummary([], 100, '2026-09-03'), null)
})

test('AC-319-3: unknown tab falls back to the first model', () => {
  assert.equal(resolveTab(['CORE', 'QUIET'], 'QUIET'), 'QUIET')
  assert.equal(resolveTab(['CORE', 'QUIET'], 'NOPE'), 'CORE')
  assert.equal(resolveTab(['CORE', 'QUIET'], null), 'CORE')
  assert.equal(resolveTab([], 'CORE'), null)
})

test('AC-319-4: trade stats count closed trades up to the shown day and average the open ones', () => {
  const s: BoardStrategy = {
    ...quiet,
    days: [
      { date: '2026-09-22', entered: [], exited: [{ stock_id: 'A', reason: 'time_stop', entry_date: '2026-08-01', held_sessions: 20, change: 0.1 }], holdings: [] },
      { date: '2026-09-23', entered: [], exited: [{ stock_id: 'B', reason: 'time_stop', entry_date: '2026-08-02', held_sessions: 20, change: -0.3 }], holdings: [{ stock_id: 'C', entry_date: '2026-09-01', held_sessions: 5, change: 0.02 }, { stock_id: 'D', entry_date: '2026-09-01', held_sessions: 5, change: -0.04 }] },
      { date: '2026-09-24', entered: [], exited: [{ stock_id: 'E', reason: 'time_stop', entry_date: '2026-08-03', held_sessions: 20, change: 0.5 }], holdings: [] },
    ],
  }
  const t = tradeStats(s, '2026-09-23')
  assert.equal(t.closed, 2)
  assert.ok(Math.abs((t.avgClosed ?? 0) - -0.1) < 1e-12)
  assert.equal(t.winRate, 0.5)
  assert.equal(t.open, 2)
  assert.equal(t.openUp, 1)
  assert.ok(Math.abs((t.avgOpen ?? 0) - -0.01) < 1e-12)
})
