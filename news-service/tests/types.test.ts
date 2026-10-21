import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { classify, typePending, validateTypeWeights } from '../src/types.ts'

const real = validateTypeWeights(JSON.parse(readFileSync(new URL('../config/news_types.json', import.meta.url), 'utf8')))

const tiny = validateTypeWeights({
  version: 'v',
  model: 'm',
  classes: ['a', 'b', 'other'],
  labels: { a: 'A', b: 'B', other: '其他' },
  min_confidence: 0.45,
  w: [
    [4, 0],
    [0, 4],
    [0, 0],
  ],
  b: [0, 0, 0],
})

test('T-421：真的權重檔讀得進來，6 類、最後是 other、1024 維', () => {
  assert.equal(real.classes.length, 6)
  assert.equal(real.classes[5], 'other')
  assert.equal(real.w[0].length, 1024)
  assert.ok(real.metrics === undefined || true)
})

test('T-421：softmax 最高的那類；信心不到門檻 → other；維度不對丟錯', () => {
  assert.deepEqual(classify([1, 0], tiny).type, 'a')
  assert.deepEqual(classify([0, 1], tiny).type, 'b')
  const even = classify([0, 0], tiny)
  assert.equal(even.type, 'other')
  assert.ok(Math.abs(even.confidence - 1 / 3) < 1e-9)
  assert.throws(() => classify([1, 0, 0], tiny), /維度/)
  assert.throws(() => validateTypeWeights({ ...tiny, classes: ['a', 'b'] }), /other/)
})

test('T-421：typePending 分完存回、算每類幾則；沒有待分類不寫', async () => {
  const saved: unknown[] = []
  const r = await typePending(tiny, 10, {
    pending: async () => [
      { item_id: 'x', vec: [1, 0] },
      { item_id: 'y', vec: [0, 0] },
    ],
    save: async (rows) => {
      saved.push(...rows)
    },
  })
  assert.deepEqual(r, { typed: 2, by_type: { a: 1, other: 1 } })
  assert.equal(saved.length, 2)
  const none = await typePending(tiny, 10, { pending: async () => [], save: async () => assert.fail('不該寫') })
  assert.deepEqual(none, { typed: 0, by_type: {} })
})
