import assert from 'node:assert/strict'
import test from 'node:test'
import { DATE_RE, SID_RE, annotate, change, lineOrder } from '../functions/_lib/picks.ts'

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
