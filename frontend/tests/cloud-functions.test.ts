import assert from 'node:assert/strict'
import test from 'node:test'
import { DATE_RE, LINES, SID_RE, annotate, change, groupCounts, lineOrder, linesForDay, recentFlags } from '../functions/_lib/picks.ts'

test('T-332 AC9: Cloudflare 版連續上榜／新進與 Python 版相同', () => {
  assert.deepEqual(annotate(['A', 'B', 'C'], [['A', 'B'], ['A', 'X'], ['A']]), {
    A: [4, false],
    B: [2, false],
    C: [1, true],
  })
  assert.deepEqual(annotate(['A'], []), { A: [1, true] })
})

test('T-332 AC9: 漲跌缺值不補 0、線別依設定順序、輸入檢查', () => {
  assert.equal(change(99, 110), 99 / 110 - 1)
  assert.equal(change(null, 110), null)
  assert.equal(change(99, 0), null)
  assert.deepEqual(lineOrder(['QUIET', 'CORE', 'QUIET', 'ZZZ']), ['CORE', 'QUIET', 'ZZZ'])
  assert.ok(SID_RE.test('2887G') && !SID_RE.test("2330';--"))
  assert.ok(DATE_RE.test('2026-10-08') && !DATE_RE.test('10/08'))
})

test('T-332 AC10: 近 6 日方格舊到新、沒打分的日子是 null（與 Python 版相同）', () => {
  const window = [['A'], null, ['A', 'B']] // 由近到遠
  assert.deepEqual(recentFlags('A', window), [true, null, true])
  assert.deepEqual(recentFlags('B', window), [true, null, false])
})


test('D-174／D-215：登記過的線一律列出（沒有股票的那天名單為空），第三、四條線在正式兩條之後', () => {
  assert.deepEqual(linesForDay(['QUIET']), ['CORE', 'QUIET', 'QUIETV', 'BREAKOUT'])
  assert.deepEqual(linesForDay([]), ['CORE', 'QUIET', 'QUIETV', 'BREAKOUT'])
  assert.deepEqual(linesForDay(['ZZZ', 'CORE']), ['CORE', 'QUIET', 'QUIETV', 'BREAKOUT', 'ZZZ'])
  const v = LINES.find((l) => l.code === 'QUIETV')
  assert.ok(v && v.note && v.note.includes('驗證中'))
  const b = LINES.find((l) => l.code === 'BREAKOUT')
  assert.ok(b && b.name === '帶量突破' && b.note && b.note.includes('前 60 個交易日最高價') && b.note.includes('2 倍'))
})

test('日期選單檔數：把每天每條線的檔數與每條線的第一天整理好', () => {
  const out = groupCounts(
    [
      { d: '2026-10-08', line: 'CORE', n: 15 },
      { d: '2026-10-08', line: 'QUIETV', n: 2 },
      { d: '2026-10-07', line: 'CORE', n: 15 },
    ],
    [
      { line: 'CORE', d: '2026-05-04' },
      { line: 'QUIETV', d: '2026-10-08' },
    ],
  )
  assert.deepEqual(out.counts, { '2026-10-08': { CORE: 15, QUIETV: 2 }, '2026-10-07': { CORE: 15 } })
  assert.deepEqual(out.since, { CORE: '2026-05-04', QUIETV: '2026-10-08' })
})
