import assert from 'node:assert/strict'
import test from 'node:test'
import { DEFAULT_LIMIT, MAX_LIMIT, parseInsightQuery } from '../functions/_lib/insights.ts'

const q = (search: string) => parseInsightQuery(new URL(`https://x.test/api/platform/insights${search}`))

test('T-373：stock 可省；limit 不合法就用預設，不猜', () => {
  assert.deepEqual(q(''), { ok: true, stock: null, limit: DEFAULT_LIMIT })
  assert.deepEqual(q('?stock=2330&limit=5'), { ok: true, stock: '2330', limit: 5 })
  assert.deepEqual(q('?stock=0050'), { ok: true, stock: '0050', limit: DEFAULT_LIMIT })
  assert.deepEqual(q(`?limit=${MAX_LIMIT + 1}`), { ok: true, stock: null, limit: DEFAULT_LIMIT })
  assert.deepEqual(q('?limit=0'), { ok: true, stock: null, limit: DEFAULT_LIMIT })
  assert.deepEqual(q('?limit=abc'), { ok: true, stock: null, limit: DEFAULT_LIMIT })
})

test('T-373：非法代號回 400 的訊息，不進資料庫', () => {
  assert.deepEqual(q('?stock=2330;drop'), { ok: false, detail: '非法股票代號：2330;drop' })
  assert.deepEqual(q('?stock=23'), { ok: false, detail: '非法股票代號：23' })
})
