import assert from 'node:assert/strict'
import test from 'node:test'
import { factTiles } from '../src/cloud/facts.ts'

const hit = { vol: 0.0487, group: 9, down15: 0.224, up20: 0.2446, n: 349161, vol_lo: 0.040083, vol_hi: null }
const weeks = [
  { date: '2026-08-28', pct: 0.842, big_holders: 386, total: 1 },
  { date: '2026-09-18', pct: 0.838, big_holders: 396, total: 1 },
]
const day = { date: '2026-10-16', n_call: 300, n_put: 57, call_value: 56_950_000, put_value: 1_490_000, underlying_amount: 1, value_ratio: 0.002, iv_call: 1.12, iv_put: null, iv_call_days: 60, iv_put_days: null }

test('使用者 2026-10-18「一目瞭然」：籌碼與風險四張數字卡——標籤、大數字、一行小字；沒資料的卡不出現', () => {
  const t = factTiles({ hit, weeks, days: [day] })
  assert.deepEqual(
    t.map((x) => [x.id, x.label, x.value, x.sub, x.tone]),
    [
      ['down15', '碰停損 −15%', '22%', '同波動股・20 日', 'warn'],
      ['up20', '漲兩成 +20%', '24%', '同波動股・20 日', 'plain'],
      ['big', '千張大戶', '83.8%', '週 ▼0.4pp', 'down'],
      ['wr', '權證認售占比', '3%', '成交 5,844 萬・IV 112%', 'plain'],
    ],
  )
  assert.deepEqual(factTiles({ hit: null, weeks: [], days: [] }), [])
  assert.deepEqual(
    factTiles({ hit: null, weeks: [weeks[1]], days: [{ ...day, iv_call: null, call_value: 0, put_value: 0 }] }).map((x) => [x.value, x.sub]),
    [
      ['83.8%', '每週六更新'],
      ['—', '成交 0'],
    ],
  )
})
