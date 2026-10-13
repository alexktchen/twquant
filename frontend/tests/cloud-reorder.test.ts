import assert from 'node:assert/strict'
import test from 'node:test'
import { edgeScroll, moveItem, rowAt } from '../src/cloud/reorder.ts'

test('T-346 AC3: 移動一檔，其他依序遞補；超出範圍原樣', () => {
  const xs = ['a', 'b', 'c', 'd']
  assert.deepEqual(moveItem(xs, 0, 2), ['b', 'c', 'a', 'd'])
  assert.deepEqual(moveItem(xs, 3, 0), ['d', 'a', 'b', 'c'])
  assert.deepEqual(moveItem(xs, 1, 1), xs)
  assert.deepEqual(moveItem(xs, 5, 0), xs)
  assert.deepEqual(moveItem(xs, 0, -1), xs)
  assert.deepEqual(xs, ['a', 'b', 'c', 'd']) // 不改原陣列
})

test('T-346 AC3: 拖到哪一列：在列的上下緣之間；超出清單上下夾到第一或最後一列', () => {
  const rects = [
    { top: 100, bottom: 156 },
    { top: 156, bottom: 212 },
    { top: 212, bottom: 268 },
  ]
  assert.equal(rowAt(rects, 120), 0)
  assert.equal(rowAt(rects, 156), 1)
  assert.equal(rowAt(rects, 267), 2)
  assert.equal(rowAt(rects, 20), 0)
  assert.equal(rowAt(rects, 900), 2)
  assert.equal(rowAt([], 100), -1)
})

test('T-346 AC3: 靠清單上下緣自動捲動，越靠邊越快，中間不捲', () => {
  assert.equal(edgeScroll(300, 100, 500), 0)
  assert.ok(edgeScroll(110, 100, 500) < 0)
  assert.ok(edgeScroll(490, 100, 500) > 0)
  assert.ok(Math.abs(edgeScroll(101, 100, 500)) > Math.abs(edgeScroll(140, 100, 500)))
  assert.equal(edgeScroll(0, 100, 500), -14) // 拖出清單外：最快
})
