import assert from 'node:assert/strict'
import test from 'node:test'
import { factLines } from '../src/cloud/facts.ts'

const hit = { vol: 0.0487, group: 9, down15: 0.224, up20: 0.2446, n: 349161, vol_lo: 0.040083, vol_hi: null }
const weeks = [
  { date: '2026-08-28', pct: 0.842, big_holders: 386, total: 1 },
  { date: '2026-09-18', pct: 0.838, big_holders: 396, total: 1 },
]
const day = { date: '2026-10-16', n_call: 300, n_put: 57, call_value: 56_950_000, put_value: 1_490_000, underlying_amount: 1, value_ratio: 0.002, iv_call: 1.12, iv_put: null, iv_call_days: 60, iv_put_days: null }

test('使用者 2026-10-18 嫌個股頁太長：籌碼與風險卡每項一行——停損、千張大戶、權證；沒資料的那行不出現', () => {
  assert.deepEqual(factLines({ hit, weeks, days: [day] }), [
    { key: 'hit', label: '同波動股', text: '20 日內碰 −15%：22%，漲 +20%：24%' },
    { key: 'big', label: '千張大戶', text: '83.8%（−0.4 個百分點）' },
    { key: 'wr', label: '權證', text: '認購 5,695 萬／認售 149 萬，隱含波動 112%' },
  ])
  assert.deepEqual(factLines({ hit: null, weeks: [], days: [] }), [])
  assert.deepEqual(
    factLines({ hit: null, weeks: [weeks[1]], days: [{ ...day, iv_call: null }] }).map((l) => l.text),
    ['83.8%', '認購 5,695 萬／認售 149 萬'],
  )
})
