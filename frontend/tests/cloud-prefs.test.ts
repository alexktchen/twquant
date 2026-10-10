import assert from 'node:assert/strict'
import test from 'node:test'
import { LINE_KEY, loadPref, savePref } from '../src/cloud/prefs.ts'

const g = globalThis as unknown as { window?: { localStorage: Pick<Storage, 'getItem' | 'setItem'> } }

test('T-332 偏好：策略與 K 線根數存在這台裝置，讀得回來', () => {
  const box = new Map<string, string>()
  g.window = { localStorage: { getItem: (k) => box.get(k) ?? null, setItem: (k, v) => void box.set(k, v) } }
  assert.equal(loadPref(LINE_KEY), null)
  savePref(LINE_KEY, 'QUIET')
  assert.equal(loadPref(LINE_KEY), 'QUIET')
})

test('T-332 偏好：瀏覽器禁用儲存時不出錯，當作沒記過', () => {
  const boom = () => {
    throw new Error('SecurityError')
  }
  g.window = { localStorage: { getItem: boom, setItem: boom } }
  assert.equal(loadPref(LINE_KEY), null)
  assert.doesNotThrow(() => savePref(LINE_KEY, 'CORE'))
  delete g.window
})
