import assert from 'node:assert/strict'
import test from 'node:test'
import { GRID, colsFromWidth, moveCard, parseLayout, resolveLayout, type CardDefault } from '../src/stock/layout.ts'

const defs: CardDefault[] = [{ id: '01', w: 6, tall: true }, { id: '02', w: 3 }, { id: '03', w: 3 }, { id: '04', w: 3 }]

test('AC-322-1: no saved layout falls back to the default order and widths', () => {
  const l = resolveLayout(defs, null)
  assert.deepEqual(l.order, ['01', '02', '03', '04'])
  assert.deepEqual(l.sizes['01'], { w: 6, h: null })
})

test('AC-322-1: saved layout keeps the user order, drops removed cards and slots new ones after their neighbour', () => {
  const saved = { order: ['03', 'gone', '01', '02', '03'], sizes: { '01': { w: 12, h: 500 }, '02': { w: 99, h: 10 } } }
  const l = resolveLayout(defs, saved)
  assert.deepEqual(l.order, ['03', '04', '01', '02']) // 04 is new: placed right after 03, its default predecessor.
  assert.deepEqual(l.sizes['01'], { w: 12, h: 500 })
  assert.deepEqual(l.sizes['02'], { w: GRID.cols, h: GRID.minHeight }) // Out-of-range sizes are clamped.
})

test('AC-322-2: moving a card forward lands after the target, backward lands before it', () => {
  const o = ['a', 'b', 'c', 'd']
  assert.deepEqual(moveCard(o, 'a', 'c'), ['b', 'c', 'a', 'd'])
  assert.deepEqual(moveCard(o, 'd', 'b'), ['a', 'd', 'b', 'c'])
  assert.deepEqual(moveCard(o, 'b', 'b'), o)
  assert.deepEqual(moveCard(o, 'x', 'b'), o)
})

test('AC-322-3: resize width snaps to whole columns within the allowed range', () => {
  // 12 columns of 70px with 10px gaps = 950px grid; one column is 80px including its gap.
  assert.equal(colsFromWidth(230, 950, 10), 3)
  assert.equal(colsFromWidth(470, 950, 10), 6)
  assert.equal(colsFromWidth(950, 950, 10), 12)
  assert.equal(colsFromWidth(40, 950, 10), GRID.minCols)
  assert.equal(colsFromWidth(5000, 950, 10), GRID.cols)
})

test('AC-322-4: malformed saved layouts are rejected as a whole', () => {
  assert.equal(parseLayout(null), null)
  assert.equal(parseLayout('{bad json'), null)
  assert.equal(parseLayout(JSON.stringify({ order: [1, 2], sizes: {} })), null)
  assert.equal(parseLayout(JSON.stringify({ order: ['a'], sizes: { a: { w: '6', h: null } } })), null)
  assert.deepEqual(parseLayout(JSON.stringify({ order: ['a'], sizes: { a: { w: 6, h: null } } })), { order: ['a'], sizes: { a: { w: 6, h: null } } })
})
